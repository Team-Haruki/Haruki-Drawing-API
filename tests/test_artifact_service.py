"""ArtifactService: hash -> lookup -> upload -> index write (plan §8.4, addendum A2/A3)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
import hashlib
import logging
from typing import Any

import pytest

from src.artifact import service as service_mod
from src.artifact.directive import RenderCacheDirective
from src.artifact.service import ArtifactOutcome, ArtifactService
from src.artifact.stats import ArtifactStats
from src.core.image_payload import EncodedImagePayload
from src.index.protocols import ContentRow, IndexSchemaError, IndexUnavailable
from src.settings import StorageSettings
from src.storage.protocols import StorageTooLarge, StorageUnavailable, StorageWriteFailed
from tests.storage_fakes import FakeObjectStore, FakeRenderIndex

NOW = datetime(2026, 9, 13, 12, 0, 0, tzinfo=UTC)
DATA = b"\x89PNG\r\n\x1a\nrendered"
DIGEST = hashlib.sha256(DATA).hexdigest()


class Clock:
    def __init__(self, start: float = 1000.0, step: float = 0.0) -> None:
        self.value = start
        self.step = step

    def __call__(self) -> float:
        self.value += self.step
        return self.value


@pytest.fixture
def stages(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    seen: list[str] = []
    monkeypatch.setattr(service_mod, "_set_stage", seen.append)
    return seen


def _payload(data: bytes = DATA, media_type: str = "image/png") -> EncodedImagePayload:
    return EncodedImagePayload(data, media_type, "x.png", 640, 480, "RGBA", 0.01)


def _directive(**overrides: Any) -> RenderCacheDirective:
    values: dict[str, Any] = {
        "cache_key": "0123456789abcdef0123",
        "key_version": 3,
        "ttl_seconds": 600,
        "store": True,
        "group": "pjsk",
        "api_path": "api/pjsk/honor",
        "user_id": "public",
    }
    values.update(overrides)
    return RenderCacheDirective(**values)


def _service(
    *,
    store: FakeObjectStore | None = None,
    index: FakeRenderIndex | None = None,
    settings: StorageSettings | None = None,
    stats: ArtifactStats | None = None,
    clock: Clock | None = None,
    **kwargs: Any,
) -> tuple[ArtifactService, FakeObjectStore, ArtifactStats]:
    store = store if store is not None else FakeObjectStore(bucket="image-cache")
    stats = stats or ArtifactStats()
    svc = ArtifactService(
        store=store,
        index=index,
        settings=settings or StorageSettings(enabled=True),
        node_name="cn09",
        stats=stats,
        clock=clock or Clock(),
        now=lambda: NOW,
        **kwargs,
    )
    return svc, store, stats


def _run(svc: ArtifactService, payload: EncodedImagePayload | None = None, **directive: Any) -> ArtifactOutcome:
    return asyncio.run(svc.process(payload or _payload(), _directive(**directive)))


def _row(**overrides: Any) -> ContentRow:
    values: dict[str, Any] = {
        "hash": DIGEST,
        "group_name": "pjsk",
        "cdn_path": f"pjsk/api/pjsk/card/{DIGEST}.png",
        "storage_backend": "garage",
        "media_type": "image/png",
        "size_bytes": 999,
        "expires_at": None,
    }
    values.update(overrides)
    return ContentRow(**values)


def test_happy_path_field_by_field(stages: list[str]) -> None:
    index = FakeRenderIndex()
    svc, store, stats = _service(index=index, clock=Clock(step=0.5))
    outcome = _run(svc)
    assert outcome.degraded is False
    assert outcome.reason == "ok"
    ref = outcome.ref
    assert ref is not None
    key = f"pjsk/api/pjsk/honor/{DIGEST}.png"
    assert ref.kind == "artifact_ref"
    assert ref.hash == DIGEST
    assert ref.cdn_path == key
    assert ref.object_key == key
    assert ref.storage_backend == "garage"
    assert ref.bucket == "image-cache"
    assert ref.size_bytes == len(DATA)
    assert ref.media_type == "image/png"
    assert (ref.width, ref.height) == (640, 480)
    assert ref.cache_key == "0123456789abcdef0123"
    assert ref.ttl_seconds == 600
    assert ref.expires_at == "2026-09-13T12:10:00Z"
    assert ref.reused is False
    assert ref.index_written is True
    assert ref.upload_elapsed > 0.0
    assert ref.node_name == "cn09"

    assert store.writes == [(key, len(DATA), "image/png")]
    assert store.objects[key] == DATA
    assert [c for c, _ in index.calls] == ["lookup_content", "record"]
    content, request = index.calls[1][1]  # type: ignore[misc]
    assert content == ContentRow(DIGEST, "pjsk", key, "garage", "image/png", len(DATA), NOW + timedelta(seconds=600))
    assert request.request_key == "0123456789abcdef0123"
    assert request.content_hash == DIGEST
    assert request.api_path == "api/pjsk/honor"
    assert (request.user_id, request.group_name, request.key_version, request.ttl_seconds) == ("public", "pjsk", 3, 600)
    assert request.expires_at == NOW + timedelta(seconds=600)

    assert stages == ["artifact:hash", "artifact:index_lookup", "artifact:upload", "artifact:index_write"]
    snap = stats.snapshot()
    assert list(snap["stages"]) == ["hash", "index_lookup", "upload", "index_write"]
    assert all(snap["stages"][name]["count"] == 1 for name in snap["stages"])
    assert snap["published"] == 1
    assert snap["reused"] == 0
    assert snap["uploads"] == 1
    assert snap["upload_bytes"] == len(DATA)
    assert snap["upload_elapsed_total"] > 0
    assert snap["index_lookups"] == 1
    assert snap["index_lookup_hits"] == 0
    assert snap["index_writes"] == 1
    assert snap["index"]["usable"] is True
    assert sum(snap["degraded"].values()) == 0


def test_infinite_ttl_gives_null_expiry(stages: list[str]) -> None:
    index = FakeRenderIndex()
    svc, _, _ = _service(index=index)
    ref = _run(svc, ttl_seconds=0).ref
    assert ref is not None
    assert ref.expires_at is None
    content, request = index.calls[1][1]  # type: ignore[misc]
    assert content.expires_at is None
    assert request.expires_at is None


def test_group_never_reaches_the_key(stages: list[str]) -> None:
    svc, _, _ = _service()
    ref = _run(svc, group="other", api_path="api/pjsk/card/detail").ref
    assert ref is not None
    assert ref.object_key == f"pjsk/api/pjsk/card/detail/{DIGEST}.png"


def test_reuse_returns_the_stored_row_and_never_recomputes(stages: list[str]) -> None:
    stored = _row()
    index = FakeRenderIndex({DIGEST: stored})
    svc, store, stats = _service(index=index)
    outcome = _run(svc, api_path="api/pjsk/honor")
    ref = outcome.ref
    assert ref is not None
    assert outcome.reason == "reused"
    assert ref.reused is True
    assert ref.cdn_path == stored.cdn_path
    assert ref.object_key == stored.cdn_path
    assert ref.size_bytes == 999
    assert ref.media_type == "image/png"
    assert ref.upload_elapsed == 0.0
    assert ref.index_written is True
    assert store.writes == []
    content, request = index.calls[1][1]  # type: ignore[misc]
    assert content.cdn_path == stored.cdn_path
    assert request.api_path == "api/pjsk/honor"
    assert stages == ["artifact:hash", "artifact:index_lookup", "artifact:index_write"]
    snap = stats.snapshot()
    assert snap["reused"] == 1
    assert snap["published"] == 0
    assert snap["reused_foreign"] == 0
    assert snap["index_lookup_hits"] == 1
    assert snap["uploads"] == 0


def test_reuse_of_cloud_store_hashed_row_is_foreign(stages: list[str]) -> None:
    stored = _row(cdn_path=f"pjsk/{DIGEST}.jpg", media_type="image/jpeg")
    svc, store, stats = _service(index=FakeRenderIndex({DIGEST: stored}))
    ref = _run(svc).ref
    assert ref is not None
    assert ref.cdn_path == f"pjsk/{DIGEST}.jpg" == ref.object_key
    assert ref.media_type == "image/jpeg"
    assert store.writes == []
    assert stats.snapshot()["reused_foreign"] == 1


def test_reuse_with_null_media_and_size_falls_back_to_payload(stages: list[str]) -> None:
    svc, _, _ = _service(index=FakeRenderIndex({DIGEST: _row(media_type=None, size_bytes=None)}))
    ref = _run(svc).ref
    assert ref is not None
    assert ref.reused
    assert ref.media_type == "image/png"
    assert ref.size_bytes == len(DATA)


def test_legacy_disk_row_is_not_reused(stages: list[str]) -> None:
    legacy = _row(storage_backend="legacy_disk", cdn_path=f"pjsk/{DIGEST}.png")
    index = FakeRenderIndex({DIGEST: legacy})
    svc, store, stats = _service(index=index)
    ref = _run(svc).ref
    assert ref is not None
    assert ref.reused is False
    assert ref.object_key == f"pjsk/api/pjsk/honor/{DIGEST}.png"
    assert len(store.writes) == 1
    assert stats.snapshot()["index_lookup_hits"] == 0


def test_index_lookup_error_is_a_miss_and_backs_off(stages: list[str]) -> None:
    clock = Clock()
    index = FakeRenderIndex(lookup_error=IndexUnavailable("down"))
    svc, store, stats = _service(index=index, clock=clock)
    ref = _run(svc).ref
    assert ref is not None
    assert ref.reused is False
    assert ref.index_written is False
    assert len(store.writes) == 1
    assert [c for c, _ in index.calls] == ["lookup_content"]
    snap = stats.snapshot()
    assert snap["index_lookup_errors"] == 1
    assert snap["index_skipped"]["unavailable"] == 1  # the write was skipped, counted once
    assert snap["index"]["usable"] is False
    assert snap["index"]["last_error"]["stage"] == "index_lookup"

    ref = _run(svc).ref  # inside the backoff window: no PG call at all
    assert ref is not None
    assert [c for c, _ in index.calls] == ["lookup_content"]
    assert stats.snapshot()["index_skipped"]["unavailable"] == 2

    clock.value += 31.0
    index.lookup_error = None
    ref = _run(svc).ref
    assert ref is not None
    assert ref.index_written is True
    assert stats.snapshot()["index"]["usable"] is True


@pytest.mark.parametrize(
    ("error", "reason", "counter"),
    [
        (StorageWriteFailed("quorum"), "upload_failed", "upload_failures"),
        (StorageUnavailable("down"), "upload_failed", "upload_failures"),
        (StorageTooLarge("big"), "upload_failed", "upload_failures"),
    ],
)
def test_upload_failure_degrades_without_index_write(stages, error, reason, counter) -> None:
    index = FakeRenderIndex()
    svc, _, stats = _service(store=FakeObjectStore(fail=error), index=index)
    outcome = _run(svc)
    assert outcome == ArtifactOutcome(ref=None, degraded=True, reason=reason)
    assert [c for c, _ in index.calls] == ["lookup_content"]  # zero record calls
    snap = stats.snapshot()
    assert snap[counter] == 1
    assert snap["degraded"][reason] == 1
    assert snap["uploads"] == 0
    assert snap["published"] == 0
    assert snap["last_error"]["stage"] == "upload"


def test_upload_timeout_degrades_without_index_calls(stages: list[str]) -> None:
    settings = StorageSettings(enabled=True, upload_timeout_seconds=0.01)
    index = FakeRenderIndex()
    svc, _, stats = _service(store=FakeObjectStore(delay=1.0), index=index, settings=settings)
    outcome = _run(svc)
    assert outcome.reason == "upload_timeout"
    assert outcome.degraded
    assert outcome.ref is None
    assert [c for c, _ in index.calls] == ["lookup_content"]
    snap = stats.snapshot()
    assert snap["upload_timeouts"] == 1
    assert snap["degraded"]["upload_timeout"] == 1


def test_index_write_failure_is_ref_with_rate_limited_error(stages, caplog: pytest.LogCaptureFixture) -> None:
    clock = Clock()
    settings = StorageSettings(enabled=True)
    settings.index.connect_retry_seconds = 0.0  # no backoff: every request retries the write
    index = FakeRenderIndex(record_error=IndexUnavailable("write lost"))
    svc, _, stats = _service(index=index, settings=settings, clock=clock)
    with caplog.at_level(logging.ERROR, logger="src.artifact.service"):
        first = _run(svc).ref
        second = _run(svc).ref
        index.record_error = RuntimeError("other class")
        third = _run(svc).ref
        clock.value += 61.0
        index.record_error = IndexUnavailable("again")
        fourth = _run(svc).ref
    for ref in (first, second, third, fourth):
        assert ref is not None
        assert ref.index_written is False
    messages = [r.getMessage() for r in caplog.records if "artifact.index_write_failed" in r.getMessage()]
    assert len(messages) == 3  # IndexUnavailable once, RuntimeError once, IndexUnavailable again after 60 s
    assert f"hash={DIGEST}" in messages[0]
    assert "key=0123456789abcdef0123" in messages[0]
    snap = stats.snapshot()
    assert snap["index_write_failures"] == 4
    assert snap["index_writes"] == 0
    assert sum(snap["degraded"].values()) == 0


def test_schema_missing_write_marks_schema_reason(stages: list[str]) -> None:
    index = FakeRenderIndex(record_error=IndexSchemaError("no table"))
    svc, _, stats = _service(index=index)
    assert _run(svc).ref is not None
    assert _run(svc).ref is not None
    snap = stats.snapshot()
    assert snap["index_write_failures"] == 1
    assert snap["index_skipped"]["schema_missing"] == 1


def test_unsupported_media_degrades(stages: list[str], caplog: pytest.LogCaptureFixture) -> None:
    svc, store, stats = _service(index=FakeRenderIndex())
    with caplog.at_level(logging.WARNING, logger="src.artifact.service"):
        outcome = _run(svc, _payload(media_type="raw_rgba_premul"))
    assert outcome.reason == "unsupported_media"
    assert outcome.degraded
    assert store.writes == []
    assert stages == []
    assert stats.snapshot()["degraded"]["unsupported_media"] == 1
    assert "artifact.unsupported_media" in caplog.text


def test_no_store_is_runtime_unavailable(stages: list[str]) -> None:
    stats = ArtifactStats()
    svc = ArtifactService(store=None, index=None, settings=StorageSettings(), node_name="n", stats=stats)
    outcome = asyncio.run(svc.process(_payload(), _directive()))
    assert outcome.reason == "runtime_unavailable"
    assert svc.store is None
    assert svc.index is None


def test_hash_offload_above_threshold(stages: list[str]) -> None:
    calls: list[int] = []

    async def offload(func, *args):
        calls.append(len(args[1]))
        return func(*args)

    settings = StorageSettings(enabled=True, hash_in_pool_min_bytes=4)
    svc, _, _ = _service(settings=settings, offload=offload)
    ref = _run(svc).ref
    assert ref is not None
    assert ref.hash == DIGEST
    assert calls == [len(DATA)]

    calls.clear()
    svc, _, _ = _service(offload=offload)  # default threshold 262144: inline
    assert _run(svc).ref is not None
    assert calls == []


def test_default_offload_uses_render_pool(monkeypatch: pytest.MonkeyPatch, stages: list[str]) -> None:
    from src.sekai.base import utils

    used: list[object] = []

    async def fake_run_in_pool(func, *args, pool=None):
        used.append(func)
        return func(*args)

    monkeypatch.setattr(utils, "run_in_pool", fake_run_in_pool)
    settings = StorageSettings(enabled=True, hash_in_pool_min_bytes=1)
    svc, _, _ = _service(settings=settings)
    assert _run(svc).ref is not None
    assert used == [service_mod._sha256_hex]


def test_store_false_uploads_but_writes_no_index(stages: list[str]) -> None:
    index = FakeRenderIndex()
    svc, store, stats = _service(index=index)
    ref = _run(svc, store=False).ref
    assert ref is not None
    assert ref.index_written is False
    assert len(store.writes) == 1
    assert [c for c, _ in index.calls] == ["lookup_content"]
    assert sum(stats.snapshot()["index_skipped"].values()) == 0


def test_index_disabled_setting_ignores_index(stages: list[str]) -> None:
    settings = StorageSettings(enabled=True)
    settings.index.enabled = False
    index = FakeRenderIndex()
    svc, _, stats = _service(index=index, settings=settings)
    ref = _run(svc).ref
    assert ref is not None
    assert ref.index_written is False
    assert index.calls == []
    assert stats.snapshot()["index_skipped"]["disabled"] == 1


def test_unexpected_exception_degrades_to_internal(stages, caplog: pytest.LogCaptureFixture) -> None:
    class Exploding(FakeObjectStore):
        async def write(self, key, data, *, content_type):
            raise KeyError("bug")

    svc, _, stats = _service(store=Exploding())
    with caplog.at_level(logging.ERROR, logger="src.artifact.service"):
        outcome = _run(svc)
    assert outcome.reason == "internal"
    assert outcome.degraded
    assert stats.snapshot()["degraded"]["internal"] == 1
    assert "artifact.internal_error" in caplog.text


def test_cancellation_propagates(stages: list[str]) -> None:
    svc, _, _ = _service(store=FakeObjectStore(delay=10.0))

    async def exercise() -> None:
        task = asyncio.create_task(svc.process(_payload(), _directive()))
        await asyncio.sleep(0.01)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task

    asyncio.run(exercise())


def test_upload_concurrency_is_bounded_and_survives_new_loops(stages: list[str]) -> None:
    active = 0
    peak = 0

    class Tracking(FakeObjectStore):
        async def write(self, key, data, *, content_type):
            nonlocal active, peak
            active += 1
            peak = max(peak, active)
            await asyncio.sleep(0.01)
            active -= 1
            await super().write(key, data, content_type=content_type)

    settings = StorageSettings(enabled=True, upload_concurrency=2)
    svc, _, _ = _service(store=Tracking(), settings=settings)

    async def burst() -> list[ArtifactOutcome]:
        return await asyncio.gather(*(svc.process(_payload(bytes([i]) * 8), _directive()) for i in range(6)))

    assert all(o.ref is not None for o in asyncio.run(burst()))
    assert peak == 2
    assert all(o.ref is not None for o in asyncio.run(burst()))  # a second loop gets a fresh semaphore


def test_real_stage_setter_marks_request_stage() -> None:
    from src.core.debug import current_request_stage

    async def exercise() -> str:
        service_mod._set_stage("artifact:hash")
        return current_request_stage()

    assert asyncio.run(exercise()) == "artifact:hash"
