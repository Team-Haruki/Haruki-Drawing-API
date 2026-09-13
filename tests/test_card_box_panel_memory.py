import asyncio

import pytest

from src.sekai.base.draw import roundrect_bg
from src.sekai.base.plot import Canvas, Frame


def test_large_card_panel_keeps_glass_under_native_scene_memory_limit(monkeypatch):
    pytest.importorskip("haruki_skia_renderer")
    from src.sekai.skia_renderer.canvas import render_canvas_payload

    async def render(blur):
        with Canvas() as canvas:
            Frame().set_size((4000, 4000)).set_bg(roundrect_bg(alpha=80, blur_glass_kwargs={"blur": blur}))
        return await render_canvas_payload(canvas, endpoint="card_box")

    # Full-resolution blur needs 320 MB here: valid under the new default,
    # but still rejected when a caller explicitly requests the old 256 MiB cap.
    result = asyncio.run(render(2))
    assert result is not None
    assert (result.image_width, result.image_height) == (4000, 4000)

    from src.sekai.skia_renderer.ir_builder import IRBuilder

    original = IRBuilder.build

    def limited_scene(self):
        scene = original(self)
        scene["limits"] = {"max_scene_bytes": 256 * 1024 * 1024}
        return scene

    monkeypatch.setattr(IRBuilder, "build", limited_scene)
    assert asyncio.run(render(2)) is None
    assert asyncio.run(render(4)) is not None
