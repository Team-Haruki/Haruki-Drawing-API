import asyncio
from types import SimpleNamespace

import pytest

from src.sekai.base.draw import roundrect_bg
from src.sekai.base.plot import Canvas, Frame
from src.sekai.card.panel import MAX_GLASS_PIXELS, card_box_panel_bg


def test_small_panels_keep_glass_and_large_panels_keep_translucent_fill():
    calls = []
    painter = SimpleNamespace(
        size=(2000, 2000),
        blurglass_roundrect=lambda *a, **kw: calls.append(("glass", a, kw)),
        roundrect=lambda *a, **kw: calls.append(("plain", a, kw)),
    )
    bg = card_box_panel_bg(alpha=80)
    assert painter.size[0] * painter.size[1] == MAX_GLASS_PIXELS
    bg.draw(painter)
    assert calls[-1][0] == "glass"
    painter.size = (2001, 2000)
    bg.draw(painter)
    assert calls[-1][0] == "plain"
    assert calls[-1][1][2] == (255, 255, 255, 80)
    # A large draw must not mutate a shared background and disable later small draws.
    painter.size = (300, 200)
    bg.draw(painter)
    assert calls[-1][0] == "glass"
    card_box_panel_bg(alpha=80, blur_glass=False).draw(painter)
    assert calls[-1][0] == "plain"


def test_large_card_panel_renders_under_native_scene_memory_limit():
    pytest.importorskip("haruki_skia_renderer")
    from src.sekai.skia_renderer.canvas import render_canvas_payload

    async def render(bg):
        with Canvas() as canvas:
            Frame().set_size((4000, 4000)).set_bg(bg)
        return await render_canvas_payload(canvas, endpoint="card_box")

    assert asyncio.run(render(roundrect_bg(alpha=80))) is None
    result = asyncio.run(render(card_box_panel_bg(alpha=80)))
    assert result is not None
    assert (result.image_width, result.image_height) == (4000, 4000)
