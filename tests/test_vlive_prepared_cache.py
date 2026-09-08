"""VLive early reuse follows displayed countdowns and optional asset arrival."""

import asyncio
from datetime import UTC, datetime, timedelta
from io import BytesIO

from PIL import Image
import pytest

from src.sekai.skia_renderer import canvas as renderer, fragment_cache
from src.sekai.skia_renderer.payload_cache import _SkiaPayloadCache
from src.sekai.vlive import drawer
from src.sekai.vlive.model import VLiveBrief, VLiveListRequest


def entry(now, **kwargs):
    return VLiveBrief(
        id=1, name="Live", start_at=now + timedelta(minutes=2, seconds=10), end_at=now + timedelta(hours=1), **kwargs
    )


def test_key_covers_display_changes_within_minute_and_survives_unchanged_minute_boundary():
    now = datetime(2026, 9, 7, 12, tzinfo=UTC)
    live = entry(now)
    later = now + timedelta(seconds=20)
    assert now.minute == later.minute
    assert drawer._vlive_entry_time_texts(live, now) != drawer._vlive_entry_time_texts(live, later)
    assert drawer._build_vlive_entry_cache_key(live, now) != drawer._build_vlive_entry_cache_key(live, later)
    live.start_at = now + timedelta(days=10)
    live.end_at = now + timedelta(days=11)
    a, b = now + timedelta(seconds=59), now + timedelta(minutes=1)
    assert drawer._vlive_entry_time_texts(live, a) == drawer._vlive_entry_time_texts(live, b)
    assert drawer._build_vlive_entry_cache_key(live, a) == drawer._build_vlive_entry_cache_key(live, b)


def test_vlive_warm_reuse_validates_assets_time_and_keeps_footer_live(tmp_path, monkeypatch, real_fonts):
    try:
        renderer.load_native_renderer()
    except ImportError:
        pytest.skip("native renderer unavailable")
    monkeypatch.setattr(drawer, "ASSETS_BASE_DIR", tmp_path)
    monkeypatch.setattr(renderer, "ASSETS_BASE_DIR", tmp_path)
    pool = _SkiaPayloadCache(32, 16 * 1024 * 1024, 60)
    monkeypatch.setattr(fragment_cache, "_cache", pool)
    now = datetime(2026, 9, 7, 12, tzinfo=UTC)
    monkeypatch.setattr(drawer, "request_now", lambda _: now)
    monkeypatch.setattr(renderer, "background_hour", lambda: 12.0)
    req = VLiveListRequest(region="jp", lives=[entry(now, banner_path="arriving.png")], dt=int(now.timestamp() * 1000))
    calls = []
    original = drawer._preload_vlive_entry_assets

    async def load(live):
        calls.append(live.id)
        return await original(live)

    monkeypatch.setattr(drawer, "_preload_vlive_entry_assets", load)

    async def render():
        payload = await drawer.try_render_vlive_list_payload(req)
        assert payload is not None
        return Image.open(BytesIO(payload.image_bytes)).convert("RGBA").tobytes()

    async def go():
        a = await render()
        assert await render() == a
        assert len(calls) == 1
        req.dt += 60000
        assert await render() != a
        assert len(calls) == 1
        Image.new("RGBA", (320, 100), "red").save(tmp_path / "arriving.png")
        b = await render()
        assert len(calls) == 2
        assert await render() == b
        Image.new("RGBA", (320, 100), "blue").save(tmp_path / "arriving.png")
        c = await render()
        assert c != b
        assert len(calls) == 3
        monkeypatch.setattr(drawer, "request_now", lambda _: now + timedelta(seconds=20))
        d = await render()
        assert c != d
        assert len(calls) == 4
        pool.clear()
        assert await render() == d
        pool._max_bytes = 0
        assert await render() == d
        assert len(calls) == 6

    asyncio.run(go())
