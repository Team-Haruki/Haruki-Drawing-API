"""Safety-net behavior of the Skia paths: fail-open on missing extension and
mem-image lifetime anchoring (no native extension required)."""

from __future__ import annotations

import asyncio
import gc

from PIL import Image
import pytest

from src.sekai.base.utils import get_img_from_path
import src.sekai.skia_renderer.canvas as skia_canvas
from src.sekai.skia_renderer.ir_painter import IRPainter
from src.settings import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT, DEFAULT_FONT, FONT_DIR, settings


def test_render_canvas_payload_fails_open_when_extension_missing(monkeypatch):
    """A missing native extension must degrade to Pillow (None), never raise."""
    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)

    def _boom():
        raise ImportError("simulated missing haruki_skia_renderer")

    monkeypatch.setattr(skia_canvas, "load_native_renderer", _boom)

    from src.sekai.base.draw import Canvas, TextBox
    from src.sekai.base.plot import TextStyle

    with Canvas().set_padding(4) as canvas:
        TextBox("x", style=TextStyle(font=DEFAULT_FONT, size=12, color=(0, 0, 0, 255)))

    result = asyncio.run(skia_canvas.render_canvas_payload(canvas))
    assert result is None


def test_irpainter_mem_images_hold_strong_refs():
    """The id(img) -> key map must anchor the PIL image: a GC'd temporary whose
    address gets recycled would otherwise alias a later image (wrong pixels)."""
    painter = IRPainter(
        (40, 40),
        assets_base_dir=str(ASSETS_BASE_DIR),
        font_dir=str(FONT_DIR),
        default_font=DEFAULT_FONT,
        bold_font=DEFAULT_BOLD_FONT,
    )
    img = Image.new("RGBA", (8, 8), (255, 0, 0, 255))
    ref1 = painter._mem_image(img)
    assert painter._mem_by_id[id(img)][0] is img  # strong reference held
    # Same object -> same key; the keepalive makes id() collisions impossible.
    assert painter._mem_image(img) == ref1
    addr = id(img)
    del img
    gc.collect()
    entry = painter._mem_by_id.get(addr)
    assert entry is not None
    assert entry[0] is not None  # still alive via the painter


def test_irpainter_uses_asset_paths_only_for_pristine_images(tmp_path):
    asset_path = tmp_path / "icons" / "sample.png"
    asset_path.parent.mkdir(parents=True)
    Image.new("RGBA", (8, 8), (20, 80, 140, 255)).save(asset_path)

    pristine = asyncio.run(get_img_from_path(tmp_path, "icons/sample.png", on_missing="raise"))
    painter = IRPainter(
        (20, 20),
        assets_base_dir=str(tmp_path),
        font_dir=str(FONT_DIR),
        default_font=DEFAULT_FONT,
        bold_font=DEFAULT_BOLD_FONT,
    )
    painter.paste(pristine, (0, 0), (4, 4))
    scene, mem_images = painter.build_scene()
    image_node = scene["root"]["children"][0]
    assert image_node["path"] == "icons/sample.png"
    assert image_node["size"] == [4, 4]
    assert image_node["sampling"] == "linear_mipmap"
    assert mem_images == {}

    modified = asyncio.run(get_img_from_path(tmp_path, "icons/sample.png", on_missing="raise"))
    modified.putalpha(128)
    fallback = IRPainter(
        (20, 20),
        assets_base_dir=str(tmp_path),
        font_dir=str(FONT_DIR),
        default_font=DEFAULT_FONT,
        bold_font=DEFAULT_BOLD_FONT,
    )
    fallback.paste(modified, (0, 0), (4, 4))
    fallback_scene, fallback_mem_images = fallback.build_scene()
    assert fallback_scene["root"]["children"][0]["path"] == "mem:m0"
    assert len(fallback_mem_images) == 1

    pristine.close()
    modified.close()


@pytest.mark.parametrize("flag", ["use_skia_plot", "use_skia_card_list"])
def test_skia_gates_default_on(flag):
    from src.settings import DrawingSettings

    assert getattr(DrawingSettings(), flag) is True
