"""A ref is published only after the shared hash lock and both index writes commit."""

import asyncio
from contextlib import asynccontextmanager
from dataclasses import replace
from datetime import timedelta

import pytest

from src.artifact.service import ArtifactService
from src.artifact.stats import ArtifactStats
from src.index.asyncpg_index import AsyncpgRenderIndex
from src.index.protocols import IndexUnavailable
from src.index.sql import FINISH_UPLOAD, LOCK_CONTENT, PREPARE_UPLOAD, RECORD, SELECT_CONTENT
from src.settings import IndexSettings, StorageSettings
from tests.storage_fakes import FakeObjectStore, FakePgPool, FakeRenderIndex
from tests.test_artifact_service import DIGEST, NOW, _directive, _payload, _row, _run, _service


def test_prepare_intent_commits_before_lock_and_put_then_is_removed_with_index():
    pool = FakePgPool()

    async def factory(*args, **kwargs):
        return pool

    index = AsyncpgRenderIndex("unused", IndexSettings(), pool_factory=factory)

    class Store(FakeObjectStore):
        async def write(self, key, data, *, content_type):
            pool.events.append(("object.put", key, ()))
            await super().write(key, data, content_type=content_type)

    service, _, _ = _service(index=index, store=Store())
    assert _run(service).ref is not None
    ordered = [(kind, sql) for kind, sql, _ in pool.events]
    stages = [
        next(i for i, (_, sql) in enumerate(ordered) if sql == PREPARE_UPLOAD),
        next(i for i, (kind, _) in enumerate(ordered) if kind == "transaction.begin"),
        next(i for i, (_, sql) in enumerate(ordered) if sql == LOCK_CONTENT),
        next(i for i, (_, sql) in enumerate(ordered) if sql == SELECT_CONTENT),
        next(i for i, (kind, _) in enumerate(ordered) if kind == "object.put"),
        next(i for i, (_, sql) in enumerate(ordered) if sql == RECORD),
        next(i for i, (_, sql) in enumerate(ordered) if sql == FINISH_UPLOAD),
        next(i for i, (kind, _) in enumerate(ordered) if kind == "transaction.commit"),
    ]
    assert stages == sorted(stages)


@pytest.mark.parametrize("statement", [PREPARE_UPLOAD, LOCK_CONTENT, SELECT_CONTENT, RECORD, FINISH_UPLOAD])
def test_database_failure_never_publishes_ref(statement):
    pool = FakePgPool(errors={statement: OSError("connection lost")})

    async def factory(*args, **kwargs):
        return pool

    index = AsyncpgRenderIndex("unused", IndexSettings(connect_retry_seconds=0), pool_factory=factory)
    service, store, stats = _service(index=index)
    outcome = _run(service)
    assert outcome.ref is None
    assert outcome.reason == "index_unavailable"
    assert stats.snapshot()["published"] == 0
    if statement in (PREPARE_UPLOAD, LOCK_CONTENT, SELECT_CONTENT):
        assert store.writes == []
    if statement != PREPARE_UPLOAD:
        assert pool.events[-1][0] == "transaction.rollback"


def test_no_index_never_uploads_an_uncoordinated_object():
    store = FakeObjectStore()
    service = ArtifactService(
        store=store, index=None, settings=StorageSettings(enabled=True), node_name="test", stats=ArtifactStats()
    )
    assert _run(service).reason == "index_unavailable"
    assert store.writes == []


def test_lookup_failure_with_zero_backoff_cannot_be_treated_as_a_miss():
    index = FakeRenderIndex(lookup_error=IndexUnavailable("down"))
    service, store, _ = _service(index=index, settings=StorageSettings(index=IndexSettings(connect_retry_seconds=0)))
    assert _run(service).ref is None
    assert store.writes == []
    assert index.writer_events == ["prepare_upload", "lock", "rollback"]
    assert len(index.intents) == 1


def test_cancelled_upload_rolls_back_and_keeps_cleanup_intent():
    index = FakeRenderIndex()

    async def run():
        entered = asyncio.Event()

        class Store(FakeObjectStore):
            async def write(self, key, data, *, content_type):
                entered.set()
                await asyncio.Event().wait()

        service, _, _ = _service(index=index, store=Store())
        task = asyncio.create_task(service.process(_payload(), _directive()))
        await entered.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(run())
    assert index.writer_events == ["prepare_upload", "lock", "rollback"]
    assert len(index.intents) == 1
    assert index.content == {}


def test_commit_failure_returns_bytes_and_does_not_count_published():
    class CommitFailure(FakeRenderIndex):
        @asynccontextmanager
        async def content_writer(self, content_hash):
            async with super().content_writer(content_hash) as writer:
                yield writer
                raise IndexUnavailable("commit disconnected")

    service, _, stats = _service(index=CommitFailure())
    assert _run(service).ref is None
    assert stats.snapshot()["published"] == 0
    assert stats.snapshot()["index_writes"] == 0


def test_reuse_retains_original_recent_writer_hint_and_write_time():
    existing = _row(writer_node="original", written_at=NOW - timedelta(seconds=30))
    index = FakeRenderIndex({DIGEST: existing})
    service, store, _ = _service(index=index)
    outcome = _run(service)
    assert outcome.ref.node_name == "original"
    recorded, _ = index.calls[-1][1]
    assert recorded.written_at == existing.written_at
    assert recorded.writer_node == "original"
    assert not index.intents
    assert store.writes == []
    index.content[DIGEST] = replace(existing, written_at=NOW - timedelta(minutes=5))
    assert _run(service).ref.node_name == "cn09"


def test_new_generation_survives_late_delete_of_old_generation():
    index = FakeRenderIndex()
    service, store, _ = _service(index=index)
    first = _run(service).ref
    index.content.clear()
    second = _run(service).ref
    assert first.hash == second.hash
    assert first.object_key != second.object_key
    del store.objects[first.object_key]
    assert store.objects[second.object_key] == _payload().image_bytes
