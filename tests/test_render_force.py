"""`X-Haruki-Render-Force`: parsed with the directive, bound per request, and every result cache misses."""

from __future__ import annotations

import asyncio
from pathlib import Path

from PIL import Image
import pytest

from src.artifact.directive import HEADER_RENDER_FORCE, DirectiveError, parse_render_cache_directive
from src.core import debug, heavy_render_pool as pool_mod
from src.core.image_payload import EncodedImagePayload
from src.core.render_force import begin_render_force, end_render_force, render_forced
from src.sekai.base import utils
from src.sekai.skia_renderer import render_stats
from src.sekai.skia_renderer.payload_cache import _SkiaPayloadCache
from tests.test_assets_mirror import LOGICAL, _make
from tests.test_heavy_render_pool import FakeQueue, LockedValue

_DIRECTIVE = {
    "x-haruki-artifact": "1",
    "x-haruki-cache-key": "0123456789abcdef",
    "x-haruki-cache-ttl": "60",
    "x-haruki-cache-key-version": "6",
    "x-haruki-api-path": "/api/pjsk/profile",
}


def _parse(**extra: str):
    return parse_render_cache_directive({**_DIRECTIVE, **extra}, ttl_max=86400)


@pytest.fixture
def forced():
    token = begin_render_force(True)
    yield
    end_render_force(token)


def test_directive_force_flag() -> None:
    assert _parse().force is False
    assert _parse(**{HEADER_RENDER_FORCE.lower(): ""}).force is False
    assert _parse(**{HEADER_RENDER_FORCE.lower(): "0"}).force is False
    assert _parse(**{HEADER_RENDER_FORCE.lower(): "1"}).force is True
    with pytest.raises(DirectiveError) as info:
        _parse(**{HEADER_RENDER_FORCE.lower(): "yes"})
    assert (info.value.header, info.value.reason) == (HEADER_RENDER_FORCE, "malformed")


def test_bytes_mode_never_reads_force() -> None:
    assert parse_render_cache_directive({HEADER_RENDER_FORCE.lower(): "1"}, ttl_max=60) is None


def test_force_is_request_scoped() -> None:
    assert render_forced() is False
    token = begin_render_force(True)
    try:
        assert render_forced() is True
    finally:
        end_render_force(token)
    assert render_forced() is False


def _bound_force(headers: dict[str, str]) -> tuple[bool, bool]:
    """Run the middleware's directive binding for `headers`; return (forced inside, forced after pop)."""

    class _Request:
        def __init__(self) -> None:
            self.headers = headers

    trace = debug._DebugRequestTrace(request_id="r", started_at=0.0, inflight=0)
    trace.tokens = debug.push_request_context("r", "/api/pjsk/profile", "POST")
    try:
        assert debug._bind_render_directive(_Request(), trace) is None
        inside = render_forced()
    finally:
        debug.pop_request_context(trace.tokens)
    return inside, render_forced()


def test_middleware_binds_and_releases_force() -> None:
    assert _bound_force({**_DIRECTIVE, HEADER_RENDER_FORCE.lower(): "1"}) == (True, False)
    assert _bound_force(dict(_DIRECTIVE)) == (False, False)


def test_composed_caches_miss_but_still_store(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(utils, "_composed_image_disk_cache", utils._DiskImageCache(tmp_path, 3600))
    first = Image.new("RGBA", (2, 2), (255, 0, 0, 255))
    utils.put_composed_image_cache("force-key", first)
    utils.put_composed_image_disk_cache("ns", "force-key", first)
    assert utils.get_composed_image_cached("force-key") is not None
    assert utils.get_composed_image_disk_cached("ns", "force-key") is not None

    token = begin_render_force(True)
    try:
        assert utils.get_composed_image_cached("force-key") is None
        assert utils.get_composed_image_disk_cached("ns", "force-key") is None
        fresh = Image.new("RGBA", (2, 2), (0, 0, 255, 255))
        utils.put_composed_image_cache("force-key", fresh)
    finally:
        end_render_force(token)
    assert utils.get_composed_image_cached("force-key").getpixel((0, 0)) == (0, 0, 255, 255)


def test_payload_cache_misses_when_forced(forced) -> None:
    cache = _SkiaPayloadCache(4, 1 << 20, 60)
    cache.set("k", "payload", 7)
    assert cache.get("k") is None


def test_payload_cache_hits_without_force() -> None:
    cache = _SkiaPayloadCache(4, 1 << 20, 60)
    cache.set("k", "payload", 7)
    assert cache.get("k") == "payload"


def test_mirror_negative_memo_is_skipped_when_forced(tmp_path: Path) -> None:
    mirror, store = _make(tmp_path, None)
    try:
        assert mirror.ensure_local(LOGICAL) is None
        assert mirror.ensure_local(LOGICAL) is None
        assert store.ops == 1  # second miss served by the NotFound memo
        token = begin_render_force(True)
        try:
            assert mirror.ensure_local(LOGICAL) is None
        finally:
            end_render_force(token)
        assert store.ops == 2  # forced: the store was asked again
    finally:
        mirror.close()


def test_heavy_task_carries_force(monkeypatch) -> None:
    pool = pool_mod.HeavyRenderWorkerPool(
        worker_count=1,
        queue_limit=1,
        queue_timeout_seconds=0.1,
        task_timeout_seconds=1.0,
        heartbeat_timeout_seconds=1.0,
        result_poll_interval_seconds=0.1,
    )
    slot = pool._slots[0]
    payload = EncodedImagePayload(b"png", "image/png", "image.png", 1, 1, "RGBA", 0.01, backend="skia")
    tasks: list[pool_mod._WorkerTask] = []

    async def acquire(_kind, _ctx):
        return slot

    async def wait(_slot, _task):
        return payload

    async def release(_slot):
        return None

    monkeypatch.setattr(debug, "current_request_context", lambda: {"request_id": "r", "path": "/", "method": "POST"})
    monkeypatch.setattr(pool, "_acquire_slot", acquire)
    monkeypatch.setattr(pool, "_put_task", lambda _slot, task: tasks.append(task))
    monkeypatch.setattr(pool, "_wait_for_result", wait)
    monkeypatch.setattr(pool, "_release_slot", release)
    monkeypatch.setattr(render_stats, "record_worker_payload_backend", lambda *_args: "skia")

    asyncio.run(pool.render("deck_recommend", {}))
    token = begin_render_force(True)
    try:
        asyncio.run(pool.render("deck_recommend", {}))
    finally:
        end_render_force(token)
    assert [task.force for task in tasks] == [False, True]


def test_worker_binds_task_force(monkeypatch) -> None:
    seen: list[bool] = []
    payload = EncodedImagePayload(b"png", "image/png", "image.png", 1, 1, "RGBA", 0.01, backend="skia")

    def render(_kind, _payload):
        seen.append(render_forced())
        return payload

    monkeypatch.setattr(pool_mod, "_render_heavy_task", render)
    tasks = [
        pool_mod._WorkerTask("a", "deck_recommend", {}, "r", "/deck", "POST", force=True),
        pool_mod._WorkerTask("b", "deck_recommend", {}, "r", "/deck", "POST"),
        None,
    ]
    pool_mod._heavy_render_worker_main("worker", FakeQueue(tasks), FakeQueue(), LockedValue(0.0))
    assert seen == [True, False]
    assert render_forced() is False
