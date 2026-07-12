"""IRPainter shim: render a plot.py Canvas tree to Skia and check it matches the layout."""

from __future__ import annotations

from io import BytesIO
import json

from PIL import Image
import pytest

try:
    import haruki_skia_renderer as _native
except ImportError:  # pragma: no cover - extension not built
    _native = None

from src.sekai.base.draw import Canvas, TextBox, roundrect_bg
from src.sekai.base.plot import TextStyle, VSplit
from src.sekai.skia_renderer.ir_painter import IRPainter
from src.settings import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT, DEFAULT_FONT, FONT_DIR

DEF = DEFAULT_FONT

pytestmark = pytest.mark.skipif(_native is None, reason="haruki_skia_renderer not built")


def _build_canvas() -> Canvas:
    with Canvas().set_padding(10) as canvas:
        with VSplit().set_sep(6).set_bg(roundrect_bg(alpha=80)).set_padding(8):
            TextBox("行一 Hello", style=TextStyle(font=DEFAULT_FONT, size=20, color=(0, 0, 0, 255)))
            TextBox("行二 World", style=TextStyle(font=DEFAULT_FONT, size=20, color=(20, 40, 60, 255)))
    return canvas


def _render(canvas: Canvas):
    size = canvas._get_self_size()
    p = IRPainter(size, assets_base_dir=str(ASSETS_BASE_DIR), font_dir=str(FONT_DIR),
                  default_font=DEF, bold_font=DEFAULT_BOLD_FONT, bg_hour=15.5)
    canvas.draw(p)
    scene, mem = p.build_scene()
    result = _native.render_scene(json.dumps(scene, ensure_ascii=False).encode(), mem)
    return size, result


def test_irpainter_renders_canvas_matching_layout():
    canvas = _build_canvas()
    size, result = _render(canvas)
    assert (result["image_width"], result["image_height"]) == tuple(size)
    img = Image.open(BytesIO(result["image_bytes"])).convert("RGBA")
    assert img.size == tuple(size)
    # The frosted panel + text means the canvas is not blank.
    assert img.getbbox() is not None


def test_irpainter_mem_image_renders():
    red = Image.new("RGBA", (24, 24), (255, 0, 0, 255))
    p = IRPainter((40, 40), assets_base_dir=str(ASSETS_BASE_DIR), font_dir=str(FONT_DIR),
                  default_font=DEF, bold_font=DEFAULT_BOLD_FONT)
    p.paste(red, (8, 8), (24, 24))
    scene, mem = p.build_scene()
    assert len(mem) == 1  # the runtime image was captured as a mem:<key> entry
    result = _native.render_scene(json.dumps(scene).encode(), mem)
    img = Image.open(BytesIO(result["image_bytes"])).convert("RGBA")
    assert img.getpixel((20, 20))[0] > 200  # red present


def test_irpainter_gradient_text_maps_to_glyph_overlay_fill():
    # Gradient text no longer raises SkiaUnsupported: the gradient endpoints are mapped
    # onto the glyph overlay (ink bbox + 10px) and the Rust Text node renders the
    # gradient as a glyph-masked shader.
    from src.sekai.base.painter import FontDesc, LinearGradient

    p = IRPainter((40, 40), assets_base_dir=str(ASSETS_BASE_DIR), font_dir=str(FONT_DIR),
                  default_font=DEF, bold_font=DEFAULT_BOLD_FONT)
    grad = LinearGradient((255, 0, 0, 255), (0, 0, 255, 255), (0, 0), (1, 0))
    p.text("hi", (0, 0), FontDesc(DEF, 20), fill=grad)
    scene, _mem = p.build_scene()
    texts = [n for n in scene["root"]["children"] if n.get("type") == "Text"]
    assert texts, "expected a text node in the scene"
    fill = texts[0]["fill"]
    assert isinstance(fill, dict)
    assert fill.get("kind") == "linear"
