import asyncio

import pytest

from src.sekai.base.draw import roundrect_bg
from src.sekai.base.plot import Canvas, Frame


def test_large_card_panel_keeps_glass_under_native_scene_memory_limit():
    pytest.importorskip("haruki_skia_renderer")
    from src.sekai.skia_renderer.canvas import render_canvas_payload

    async def render(blur):
        with Canvas() as canvas:
            Frame().set_size((4000, 4000)).set_bg(roundrect_bg(alpha=80, blur_glass_kwargs={"blur": blur}))
        return await render_canvas_payload(canvas, endpoint="card_box")

    # Blur 4 downsamples each axis by 2. The full glass effect fits the unchanged
    # 256 MiB scene budget; blur 2 has four full-size buffers and must still fail.
    result = asyncio.run(render(4))
    assert result is not None
    assert (result.image_width, result.image_height) == (4000, 4000)
    assert asyncio.run(render(2)) is None
