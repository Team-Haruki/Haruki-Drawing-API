"""Render observability: outcome counters, the shared cached-render helper (fail-open,
cache/disabled short-circuits, canvas-size guard) and the Skia payload cache reporting.

No native extension required — the render is faked throughout.
"""

from __future__ import annotations

import asyncio

import pytest

from src.core.debug import current_render_backend, pop_request_context, push_request_context
from src.core.heavy_render_pool import EncodedImagePayload
import src.sekai.skia_renderer.canvas as canvas_mod
from src.sekai.skia_renderer.payload_cache import (
    _SkiaPayloadCache,
    clear_skia_payload_cache,
    get_skia_payload_cache_stats,
    put_skia_payload_cache,
)
from src.sekai.skia_renderer.render_stats import (
    get_render_stats,
    record_native_metrics,
    record_render,
    record_worker_payload_backend,
    reset_render_stats,
)
from src.settings import settings


@pytest.fixture(autouse=True)
def _clean_stats():
    reset_render_stats()
    yield
    reset_render_stats()


def _payload(nbytes: int = 8) -> EncodedImagePayload:
    return EncodedImagePayload(
        image_bytes=b"x" * nbytes,
        media_type="image/png",
        filename="image.png",
        image_width=10,
        image_height=10,
        image_mode="RGBA",
        encode_elapsed=0.0,
    )


class _FakeCanvas:
    """Stand-in for a built plot Canvas (only the size probe is used by the Skia path)."""

    def __init__(self, size: tuple[int, int] = (100, 100)) -> None:
        self._size = size
        self.drawn = False

    def _get_self_size(self) -> tuple[int, int]:
        return self._size

    def draw(self, painter) -> None:  # pragma: no cover - must never run in these tests
        self.drawn = True
        raise AssertionError("draw() must not run")


async def _build_ok() -> _FakeCanvas:
    return _FakeCanvas()


# ------------------------------- counters -------------------------------


def test_record_render_counts_per_endpoint_and_totals():
    record_render("card_list", "skia")
    record_render("card_list", "skia")
    record_render("card_list", "cache_hit")
    record_render("profile", "fallback")
    record_render("profile", "disabled")
    record_render("profile", "error")

    stats = get_render_stats()
    assert stats["endpoints"]["card_list"] == {
        "skia": 2,
        "cache_hit": 1,
        "fallback": 0,
        "disabled": 0,
        "error": 0,
        "total": 3,
    }
    assert stats["endpoints"]["profile"]["fallback"] == 1
    assert stats["endpoints"]["profile"]["disabled"] == 1
    assert stats["endpoints"]["profile"]["error"] == 1
    assert stats["totals"] == {
        "skia": 2,
        "cache_hit": 1,
        "fallback": 1,
        "disabled": 1,
        "error": 1,
        "total": 6,
    }


def test_record_render_never_raises_on_unknown_outcome():
    record_render("weird", "not_an_outcome")
    assert get_render_stats()["endpoints"]["weird"]["error"] == 1


def test_reset_render_stats_clears_counters():
    record_render("card_list", "skia")
    record_native_metrics({"font_fallbacks": 3})
    reset_render_stats()
    assert get_render_stats() == {
        "endpoints": {},
        "totals": dict.fromkeys(("skia", "cache_hit", "fallback", "disabled", "error", "total"), 0),
        "font_fallbacks": 0,
    }


def test_font_fallbacks_are_aggregated_from_the_payload():
    """The Rust font-fallback counter is process-local, so it is invisible for the two endpoints
    that render in a spawned heavy worker. The per-render count rides back on the payload; if it
    were not folded in here, /render-stats would report a healthy 0 while every deck image
    silently rendered in sans-serif."""
    reset_render_stats()
    record_native_metrics({"font_fallbacks": 2})
    record_native_metrics({"font_fallbacks": 1})
    record_native_metrics(None)  # Pillow render / no metrics
    record_native_metrics({"font_fallbacks": "junk"})  # must not raise
    assert get_render_stats()["font_fallbacks"] == 3


# ----------------------- outcomes on the render path -----------------------
#
# There is no separate "cached render helper" any more: page-level result caching was removed
# (the caller already caches by payload, and our key could never hit), so render_canvas_payload
# IS the entry point, and it is where every outcome is recorded. The three endpoints that keep a
# payload cache (card/box, card/list, honor) return the cached payload before reaching it and
# record the hit themselves via record_skia_cache_hit — covered in tests/test_skia_card_box.py.


def test_records_skia_and_stamps_the_payload(monkeypatch):
    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)
    payload = _payload()

    async def fake_render(canvas, **kwargs):
        return payload

    monkeypatch.setattr(canvas_mod, "_render_canvas_uncounted", fake_render)

    result = asyncio.run(canvas_mod.render_canvas_payload(_FakeCanvas(), endpoint="card_list"))
    assert result is payload
    assert get_render_stats()["endpoints"]["card_list"]["skia"] == 1
    assert payload.backend == "skia"


def test_records_disabled_without_rendering(monkeypatch):
    monkeypatch.setattr(settings.drawing, "use_skia_plot", False)

    async def _boom(canvas, **kwargs):  # pragma: no cover - must not run
        raise AssertionError("must not render when the gate is off")

    monkeypatch.setattr(canvas_mod, "_render_canvas_uncounted", _boom)

    result = asyncio.run(canvas_mod.render_canvas_payload(_FakeCanvas(), endpoint="card_list"))
    assert result is None
    assert get_render_stats()["endpoints"]["card_list"]["disabled"] == 1


def test_records_fallback_when_render_declines(monkeypatch):
    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)

    async def fake_render(canvas, **kwargs):
        return None  # unsupported primitive / native ext missing

    monkeypatch.setattr(canvas_mod, "_render_canvas_uncounted", fake_render)

    result = asyncio.run(canvas_mod.render_canvas_payload(_FakeCanvas(), endpoint="card_list"))
    assert result is None
    assert get_render_stats()["endpoints"]["card_list"]["fallback"] == 1


def test_swallows_render_exception_and_records_error(monkeypatch):
    """FAIL-OPEN: an unexpected Skia error must degrade to Pillow, never propagate."""
    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)

    async def fake_render(canvas, **kwargs):
        raise RuntimeError("native exploded")

    monkeypatch.setattr(canvas_mod, "_render_canvas_uncounted", fake_render)

    result = asyncio.run(canvas_mod.render_canvas_payload(_FakeCanvas(), endpoint="card_list"))
    assert result is None
    assert get_render_stats()["endpoints"]["card_list"]["error"] == 1


def test_an_unnamed_caller_is_still_counted(monkeypatch):
    """endpoint= is optional so a forgotten call site still renders; it must not vanish from
    /render-stats though, or the gap would be invisible."""
    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)

    async def fake_render(canvas, **kwargs):
        return _payload()

    monkeypatch.setattr(canvas_mod, "_render_canvas_uncounted", fake_render)

    asyncio.run(canvas_mod.render_canvas_payload(_FakeCanvas()))
    assert get_render_stats()["endpoints"]["unknown"]["skia"] == 1


# ---------------------------- canvas-size guard ----------------------------


def test_canvas_size_guard_falls_back_without_rendering(monkeypatch):
    """An absurd canvas is refused inside the pool task, so the scene is never rendered.
    (The native MODULE may be imported first — that is just a cached import; what must not
    happen is render_scene being handed a multi-gigapixel surface.)"""
    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)

    class _Native:
        def render_scene(self, *a, **kw):  # pragma: no cover - must not run
            raise AssertionError("an oversized canvas must not reach render_scene")

    monkeypatch.setattr(canvas_mod, "load_native_renderer", lambda: _Native())

    huge = _FakeCanvas((30000, 30000))  # 900 Mpx
    result = asyncio.run(canvas_mod.render_canvas_payload(huge, endpoint="mysekai_map"))
    assert result is None
    assert get_render_stats()["endpoints"]["mysekai_map"]["fallback"] == 1


def test_canvas_size_guard_is_a_dos_bound_not_a_mirror_of_the_pillow_limit():
    """Skia is the ONLY backend that can render a canvas Pillow refuses (the largest real
    payload, chart, is already 14.2 Mpx against Pillow's 16.8 Mpx budget). Mirroring the Pillow
    assertion here would bounce those to Pillow, which then raises -> a 500 for a render that
    works today. The guard must only catch the absurd."""
    assert canvas_mod.canvas_size_within_limit((5248, 2704))  # the real chart payload
    assert canvas_mod.canvas_size_within_limit((4097, 4096))  # over Pillow's budget, fine here
    assert canvas_mod.canvas_size_within_limit((8000, 8000))  # 64 Mpx, exactly at the bound

    assert not canvas_mod.canvas_size_within_limit((9000, 9000))  # 81 Mpx
    assert not canvas_mod.canvas_size_within_limit((40000, 10))  # absurd single edge
    assert not canvas_mod.canvas_size_within_limit((0, 100))  # degenerate


# ----------------------------- backend contextvar -----------------------------


@pytest.mark.parametrize(
    ("outcome", "backend"),
    [
        ("skia", "skia"),
        ("cache_hit", "skia_cache"),
        ("fallback", "skia_fallback"),
        ("error", "skia_fallback"),
        ("disabled", "pillow"),
    ],
)
def test_backend_contextvar_tracks_the_outcome(outcome, backend):
    tokens = push_request_context("rid", "/api/pjsk/card/list", "POST")
    try:
        assert current_render_backend() == "pillow"  # a request that never attempts Skia
        canvas_mod._record("card_list", outcome)
        assert current_render_backend() == backend
    finally:
        pop_request_context(tokens)
    assert current_render_backend() == "pillow"  # the token reset restores the default


def test_worker_stamps_the_backend_on_a_skia_payload():
    """The worker's contextvar/counters die with the child process; the payload carries the
    backend home. A drawer that used the helper already set one — do not clobber it."""
    from src.core.heavy_render_pool import _stamp_skia_backend

    assert _stamp_skia_backend(_payload()).backend == "skia"

    cached = _payload()
    cached.backend = "skia_cache"
    assert _stamp_skia_backend(cached).backend == "skia_cache"


def test_worker_payload_backend_is_replayed_in_the_parent(monkeypatch):
    """The heavy worker renders in another process, so the parent replays the payload backend."""
    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)

    assert record_worker_payload_backend("chara_birthday", "skia") == "skia"
    # No backend on the payload -> the worker used Pillow; Skia is on, so it declined.
    assert record_worker_payload_backend("chara_birthday", None) == "skia_fallback"

    monkeypatch.setattr(settings.drawing, "use_skia_plot", False)
    assert record_worker_payload_backend("deck_recommend", None) == "pillow"

    stats = get_render_stats()["endpoints"]
    assert stats["chara_birthday"]["skia"] == 1
    assert stats["chara_birthday"]["fallback"] == 1
    assert stats["deck_recommend"]["disabled"] == 1


# ----------------------------- payload cache stats -----------------------------


def test_skia_payload_cache_stats_and_eviction():
    cache = _SkiaPayloadCache(max_size=2, max_bytes=1024, ttl_seconds=60)
    assert cache.get("a") is None
    cache.set("a", _payload(), 10)
    cache.set("b", _payload(), 10)
    assert cache.get("a") is not None
    cache.set("c", _payload(), 10)  # evicts "b" (LRU: "a" was just touched)

    stats = cache.stats()
    assert stats["enabled"] is True
    assert stats["entries"] == 2
    assert stats["bytes"] == 20
    assert stats["ttl_seconds"] == 60
    assert stats["hits"] == 1
    assert stats["misses"] == 1
    assert stats["sets"] == 3
    assert stats["evictions"] == 1

    cache.clear()
    assert cache.stats()["entries"] == 0
    assert cache.stats()["bytes"] == 0


def test_global_payload_cache_is_reported_and_cleared():
    from src.sekai.base.utils import get_runtime_cache_stats

    clear_skia_payload_cache()
    put_skia_payload_cache("render-stats-test", _payload(32), 32)

    stats = get_runtime_cache_stats()["skia_payload_cache"]
    assert stats == get_skia_payload_cache_stats()
    if stats["enabled"]:  # disabled by config -> nothing to assert beyond the wiring
        assert stats["entries"] == 1
        assert stats["bytes"] == 32

    clear_skia_payload_cache()
    assert get_runtime_cache_stats()["skia_payload_cache"]["entries"] == 0
