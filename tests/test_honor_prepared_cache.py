"""Static honor reuse must exclude the footer and retain missing-asset semantics."""

import asyncio
from io import BytesIO

from PIL import Image
import pytest

from src.sekai.honor import drawer, skia
from src.sekai.honor.model import HonorRequest
from src.sekai.skia_renderer import canvas as renderer, fragment_cache
from src.sekai.skia_renderer.payload_cache import _SkiaPayloadCache, clear_skia_payload_cache


def test_honor_badge_reuse_preserves_footer_assets_and_disabled_cache(tmp_path, monkeypatch, real_fonts):
    try:
        skia.load_native_renderer()
    except ImportError:
        pytest.skip("native renderer unavailable")
    monkeypatch.setattr(drawer, "ASSETS_BASE_DIR", tmp_path)
    monkeypatch.setattr(skia, "ASSETS_BASE_DIR", tmp_path)
    monkeypatch.setattr(renderer, "ASSETS_BASE_DIR", tmp_path)
    pool = _SkiaPayloadCache(16, 4 * 1024 * 1024, 60)
    monkeypatch.setattr(fragment_cache, "_cache", pool)
    base = tmp_path / "badge.png"
    Image.new("RGBA", (380, 80), "blue").save(base)
    request = HonorRequest(
        honor_type="birthday", honor_img_path="badge.png", rank_img_path="rank.png", dt=1788753600000
    )
    loads = []
    original = drawer.load_honor_images

    async def load(req):
        loads.append(req.dt)
        return await original(req)

    monkeypatch.setattr(drawer, "load_honor_images", load)

    async def render():
        clear_skia_payload_cache()
        result = await skia.try_render_full_honor_payload(request)
        return None if result is None else Image.open(BytesIO(result.image_bytes)).convert("RGBA").tobytes()

    async def go():
        first = await render()
        assert first is not None
        assert await render() == first
        assert len(loads) == 1
        request.dt += 60000
        second = await render()
        assert first != second
        assert len(loads) == 1
        Image.new("RGBA", (50, 20), "red").save(tmp_path / "rank.png")
        third = await render()
        assert third != second
        assert len(loads) == 2
        Image.new("RGBA", (380, 80), "green").save(base)
        fourth = await render()
        assert fourth != third
        assert len(loads) == 3
        pool.clear()
        assert await render() == fourth
        assert len(loads) == 4
        pool._max_bytes = 0
        assert await render() == fourth
        assert await render() == fourth
        assert len(loads) == 6
        pool._max_bytes = 4 * 1024 * 1024
        assert await render() == fourth
        base.unlink()
        assert await render() is None

    asyncio.run(go())
