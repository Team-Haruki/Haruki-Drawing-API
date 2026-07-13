"""card/box now renders through the IRPainter shadow layer (the dedicated scene builder,
written to chase pixel parity with the pre-rework Pillow layout, was retired once
real-data parity held); these tests pin the gate/fallback/caching wiring of that path."""

from __future__ import annotations

import asyncio

import pytest

from src.core.heavy_render_pool import EncodedImagePayload
from src.core.pjsk import card as card_router
from src.sekai.card import drawer as card_drawer
from src.sekai.card.model import CardBasic, CardBoxRequest, UserCard
from src.sekai.profile.model import CardFullThumbnailRequest
from src.settings import settings


def _thumbnail(card_id: int) -> CardFullThumbnailRequest:
    return CardFullThumbnailRequest(
        card_id=card_id,
        card_thumbnail_path="cards/card.png",
        rare="rarity_4",
        frame_img_path="frames/frame.png",
        attr_img_path="icons/attr.png",
        rare_img_path="icons/star.png",
        train_rank=None,
    )


def _request() -> CardBoxRequest:
    card = CardBasic(
        card_id=1,
        character_id=1,
        rare="rarity_4",
        attr="cool",
        prefix="p",
        asset_bundle_name="res001_no001",
        release_at=1,
        thumbnail_info=[_thumbnail(1)],
    )
    return CardBoxRequest(
        cards=[UserCard(card=card, has_card=True)],
        region="jp",
        character_icon_paths={1: "icons/chara_1.png"},
    )


def _payload() -> EncodedImagePayload:
    return EncodedImagePayload(
        image_bytes=b"\x89PNG\r\n\x1a\n",
        media_type="image/png",
        filename="box.png",
        image_width=10,
        image_height=10,
        image_mode="RGB",
        encode_elapsed=0.0,
    )


def test_box_returns_none_when_gate_off(monkeypatch):
    monkeypatch.setattr(settings.drawing, "use_skia_plot", False)
    assert asyncio.run(card_drawer.try_render_box_payload(_request())) is None


def test_box_renders_via_shadow_layer_and_caches_payload(monkeypatch):
    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)

    calls = {"build": 0}
    payload = _payload()

    async def fake_build(rqd):
        calls["build"] += 1
        return object()

    async def fake_render(canvas, **kwargs):
        return payload

    cache: dict[str, EncodedImagePayload] = {}
    monkeypatch.setattr(card_drawer, "_build_box_canvas", fake_build)
    monkeypatch.setattr(card_drawer, "render_canvas_payload", fake_render)
    monkeypatch.setattr(card_drawer, "get_skia_payload_cached", cache.get)
    monkeypatch.setattr(card_drawer, "put_skia_payload_cache", lambda k, v, _size: cache.__setitem__(k, v))

    rqd = _request()
    assert asyncio.run(card_drawer.try_render_box_payload(rqd)) is payload
    assert calls["build"] == 1
    assert len(cache) == 1  # encoded payload cached under the box business key
    # Second call hits the payload cache without rebuilding the canvas.
    assert asyncio.run(card_drawer.try_render_box_payload(rqd)) is payload
    assert calls["build"] == 1


def test_box_falls_back_when_shadow_render_returns_none(monkeypatch):
    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)

    async def fake_build(rqd):
        return object()

    async def fake_render(canvas, **kwargs):
        return None  # SkiaUnsupported / native error inside render_canvas_payload

    monkeypatch.setattr(card_drawer, "_build_box_canvas", fake_build)
    monkeypatch.setattr(card_drawer, "render_canvas_payload", fake_render)
    monkeypatch.setattr(card_drawer, "get_skia_payload_cached", lambda _k: None)
    assert asyncio.run(card_drawer.try_render_box_payload(_request())) is None


def test_card_box_endpoint_uses_shadow_payload(monkeypatch):
    async def fake_try_render(_request):
        return _payload()

    async def fake_compose(_request):
        raise AssertionError("pillow composer should not be called")

    monkeypatch.setattr(card_router, "try_render_box_payload", fake_try_render)
    monkeypatch.setattr(card_router, "compose_box_image", fake_compose)

    response = asyncio.run(card_router.card_box(_request()))
    assert response.media_type == "image/png"


def test_no_dedicated_card_scene_builders_remain():
    """Both card endpoints now draw the shared plot.py widget tree, so the whole hand-written IR
    scene module is retired -- card/box first, card/list after it. A dedicated builder means two
    layouts to keep in step, which is exactly how card/list drifted from the Pillow tree."""
    import importlib

    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("src.sekai.skia_renderer.card_render")
