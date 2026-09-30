"""A ref is published only after the object write and both index rows commit under the shared hash lock.

The PUT runs between two round trips, outside any transaction: before it the cleanup intent is committed, after
it the locked write re-checks that intent (or an existing Garage row) before recording anything.
"""

import asyncio
from dataclasses import replace
from datetime import timedelta
from types import SimpleNamespace

import pytest

from src.artifact import service as service_mod
from src.artifact.service import ArtifactService
from src.artifact.stats import ArtifactStats
from src.index.asyncpg_index import AsyncpgRenderIndex
from src.index.protocols import IndexUnavailable
from src.index.sql import (
    COMMIT,
    PREFLIGHT_CONTENT,
    PREFLIGHT_REQUEST,
    PREPARE_UPLOAD,
    RECORD_UPLOAD,
    ROLLBACK,
    begin_and_lock,
)
from src.settings import IndexSettings, StorageSettings
from tests.storage_fakes import FakeObjectStore, FakePgPool, FakeRenderIndex
from tests.test_artifact_service import (
    DIGEST,
    GENERATION,
    NOW,
    _directive,
    _payload,
    _row,
    _run,
    _service,
    fixed_generation,  # noqa: F401 - autouse fixture: every candidate key is KEY
)

LOCK = begin_and_lock(DIGEST)
KEY = f"pjsk/api/pjsk/honor/{DIGEST}-{GENERATION}.png"


def _pg_index(pool: FakePgPool, **settings) -> AsyncpgRenderIndex:
    async def factory(*args, **kwargs):
        return pool

    return AsyncpgRenderIndex("unused", IndexSettings(**settings), pool_factory=factory)


def _round_trips(pool: FakePgPool) -> list[tuple[str, str | None]]:
    """The request's database round trips and the PUT, in order (the per-loop preflight excluded)."""
    preflight = {PREFLIGHT_REQUEST, PREFLIGHT_CONTENT}
    return [
        (kind, sql)
        for kind, sql, _ in pool.events
        if kind in ("execute", "fetchrow", "fetchval", "object.put", "transaction.begin", "transaction.commit")
        and sql not in preflight
    ]


class _TracingStore(FakeObjectStore):
    def __init__(self, pool: FakePgPool) -> None:
        super().__init__()
        self._pool = pool

    async def write(self, key, data, *, content_type):
        self._pool.events.append(("object.put", key, ()))
        await super().write(key, data, content_type=content_type)


def test_miss_is_four_round_trips_with_the_put_outside_the_transaction():
    pool = FakePgPool()
    service, _, _ = _service(index=_pg_index(pool), store=_TracingStore(pool))
    outcome = _run(service)
    assert outcome.ref is not None
    assert outcome.ref.object_key == KEY
    assert _round_trips(pool) == [
        ("fetchval", PREPARE_UPLOAD),  # lookup + committed intent
        ("object.put", KEY),  # no transaction open, no lock held
        ("execute", LOCK),  # BEGIN + hash lock
        ("fetchrow", RECORD_UPLOAD),  # intent check + both rows + intent removal
        ("execute", COMMIT),
    ]
    assert len([event for event in _round_trips(pool) if event[0] != "object.put"]) == 4
    calls = {sql: args for _, sql, args in pool.events}
    assert calls[PREPARE_UPLOAD] == (DIGEST, KEY)
    assert calls[RECORD_UPLOAD][:3] == (DIGEST, "pjsk", KEY)
    assert calls[RECORD_UPLOAD][-1] is True  # uploaded: an unrecorded candidate would be re-queued


def test_hit_is_four_round_trips_without_a_put():
    pool = FakePgPool(values={PREPARE_UPLOAD: "pjsk/api/pjsk/card/stored.png"})
    pool.default_row = {
        "cdn_path": "pjsk/api/pjsk/card/stored.png",
        "media_type": "image/png",
        "size_bytes": 999,
        "writer_node": "original",
        "written_at": NOW - timedelta(seconds=30),
        "prior_backend": "garage",
    }
    service, store, stats = _service(index=_pg_index(pool), store=_TracingStore(pool))
    ref = _run(service).ref
    assert ref is not None
    assert ref.reused
    assert ref.object_key == "pjsk/api/pjsk/card/stored.png"
    assert ref.node_name == "original"
    assert store.writes == []
    assert [sql for _, sql in _round_trips(pool)] == [PREPARE_UPLOAD, LOCK, RECORD_UPLOAD, COMMIT]
    assert pool.events[-2][2][-1] is False  # not uploaded: nothing to queue
    assert stats.snapshot()["index_lookup_hits"] == 1


@pytest.mark.parametrize("statement", [PREPARE_UPLOAD, LOCK, RECORD_UPLOAD, COMMIT])
def test_database_failure_never_publishes_ref(statement):
    pool = FakePgPool(errors={statement: OSError("connection lost")})
    service, store, stats = _service(index=_pg_index(pool, connect_retry_seconds=0))
    outcome = _run(service)
    assert outcome.ref is None
    assert outcome.reason == "index_unavailable"
    assert stats.snapshot()["published"] == 0
    if statement == PREPARE_UPLOAD:
        assert store.writes == []  # no committed intent: no PUT
    else:
        assert len(store.writes) == 1
        assert pool.events[-1][:2] == ("execute", ROLLBACK)


def test_nothing_owned_commits_and_returns_bytes():
    # RECORD_UPLOAD returns no row: the intent expired / was claimed, and no Garage row owns the hash. The
    # transaction still commits, because the statement may have re-queued the uploaded candidate.
    pool = FakePgPool(echo_records=False)
    service, store, stats = _service(index=_pg_index(pool))
    outcome = _run(service)
    assert outcome.ref is None
    assert outcome.reason == "index_unavailable"
    assert len(store.writes) == 1
    assert [sql for _, sql in _round_trips(pool)][-2:] == [RECORD_UPLOAD, COMMIT]
    assert stats.snapshot()["index"]["usable"] is True  # not a health problem: no backoff


def test_no_index_never_uploads_an_uncoordinated_object():
    store = FakeObjectStore()
    service = ArtifactService(
        store=store, index=None, settings=StorageSettings(enabled=True), node_name="test", stats=ArtifactStats()
    )
    assert _run(service).reason == "index_unavailable"
    assert store.writes == []


def test_prepare_failure_with_zero_backoff_cannot_be_treated_as_a_miss():
    index = FakeRenderIndex(lookup_error=IndexUnavailable("down"))
    service, store, _ = _service(index=index, settings=StorageSettings(index=IndexSettings(connect_retry_seconds=0)))
    assert _run(service).ref is None
    assert store.writes == []
    assert index.writer_events == ["prepare_upload"]
    assert not index.intents


def test_cancelled_upload_keeps_cleanup_intent_and_never_locks():
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
    assert index.writer_events == ["prepare_upload"]
    assert index.intents == {(DIGEST, KEY)}
    assert index.content == {}


def test_intent_expired_during_put_is_never_recorded():
    index = FakeRenderIndex()

    class Store(FakeObjectStore):
        async def write(self, key, data, *, content_type):
            await super().write(key, data, content_type=content_type)
            index.expired_intents.add((DIGEST, key))  # GC became eligible while the PUT ran

    service, store, stats = _service(index=index, store=Store())
    outcome = _run(service)
    assert outcome.ref is None
    assert outcome.reason == "index_unavailable"
    assert len(store.writes) == 1
    assert index.content == {}
    assert index.requests == {}
    assert index.intents == {(DIGEST, KEY)}  # left for GC to delete the object
    assert index.writer_events == ["prepare_upload", "lock", "commit"]
    assert stats.snapshot()["published"] == 0


def test_intent_consumed_by_gc_during_put_is_queued_again():
    # GC found the intent due, deleted the (not yet written) key and dropped the queue row; the PUT then landed.
    index = FakeRenderIndex()

    class Store(FakeObjectStore):
        async def write(self, key, data, *, content_type):
            index.intents.discard((DIGEST, key))
            await super().write(key, data, content_type=content_type)

    service, store, _ = _service(index=index, store=Store())
    assert _run(service).ref is None
    assert KEY in store.objects
    assert index.content == {}
    assert index.intents == {(DIGEST, KEY)}  # the object is queued again, so it cannot leak


def test_losing_the_race_to_another_writer_reuses_its_row_and_leaves_the_upload_to_gc():
    index = FakeRenderIndex()
    winner = _row(cdn_path=f"pjsk/api/pjsk/honor/{DIGEST}-{'2' * 32}.png", writer_node="cn06", written_at=NOW)

    class Store(FakeObjectStore):
        async def write(self, key, data, *, content_type):
            await super().write(key, data, content_type=content_type)
            index.content[DIGEST] = winner  # another writer committed first

    service, _, stats = _service(index=index, store=Store())
    ref = _run(service).ref
    assert ref is not None
    assert ref.reused
    assert ref.object_key == winner.cdn_path
    assert ref.node_name == "cn06"
    assert index.content[DIGEST] == winner
    assert index.intents == {(DIGEST, KEY)}  # our unrecorded object stays queued for GC
    snap = stats.snapshot()
    assert snap["unrecorded_uploads"] == 1
    assert snap["reused"] == 1


def test_hit_whose_row_was_retired_before_the_lock_returns_bytes():
    index = FakeRenderIndex({DIGEST: _row()})
    original = index.record_upload

    async def retired_first(content, request, *, uploaded):
        del index.content[DIGEST]  # GC retired the row between the lookup and the lock
        return await original(content, request, uploaded=uploaded)

    index.record_upload = retired_first
    service, store, _ = _service(index=index)
    outcome = _run(service)
    assert outcome.ref is None
    assert store.writes == []
    assert index.content == {}
    assert index.requests == {}


def test_commit_failure_returns_bytes_and_does_not_count_published():
    pool = FakePgPool(errors={COMMIT: OSError("commit disconnected")})
    service, _, stats = _service(index=_pg_index(pool))
    assert _run(service).ref is None
    assert stats.snapshot()["published"] == 0
    assert stats.snapshot()["index_writes"] == 0


def test_reuse_retains_original_recent_writer_hint_and_write_time():
    existing = _row(writer_node="original", written_at=NOW - timedelta(seconds=30))
    index = FakeRenderIndex({DIGEST: existing})
    service, store, _ = _service(index=index)
    outcome = _run(service)
    assert outcome.ref.node_name == "original"
    assert index.content[DIGEST].written_at == existing.written_at
    assert index.content[DIGEST].writer_node == "original"
    assert not index.intents
    assert store.writes == []
    index.content[DIGEST] = replace(existing, written_at=NOW - timedelta(minutes=5))
    assert _run(service).ref.node_name == "cn09"


def test_new_generation_survives_late_delete_of_old_generation(monkeypatch):
    generations = iter(["a" * 32, "b" * 32])
    monkeypatch.setattr(service_mod, "uuid4", lambda: SimpleNamespace(hex=next(generations)))
    index = FakeRenderIndex()
    service, store, _ = _service(index=index)
    first = _run(service).ref
    index.content.clear()
    second = _run(service).ref
    assert first.hash == second.hash
    assert first.object_key != second.object_key
    del store.objects[first.object_key]
    assert store.objects[second.object_key] == _payload().image_bytes


def test_lock_timeout_does_not_disable_unrelated_writes():
    pool = FakePgPool(errors={LOCK: TimeoutError()})
    service, _, _ = _service(index=_pg_index(pool))
    assert _run(service).ref is None
    assert pool.events[-1][:2] == ("execute", ROLLBACK)
    pool.errors.clear()
    assert _run(service).ref is not None


def test_request_budget_timeout_does_not_disable_index():
    class SlowOnce(FakeRenderIndex):
        slow = True

        async def prepare_upload(self, content_hash, cdn_path):
            if self.slow:
                self.slow = False
                raise TimeoutError()
            return await super().prepare_upload(content_hash, cdn_path)

    service, store, _ = _service(index=SlowOnce())
    assert _run(service).ref is None
    assert store.writes == []
    assert _run(service).ref is not None
