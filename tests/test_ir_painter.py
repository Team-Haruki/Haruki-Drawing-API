"""IRPainter shim: render a plot.py Canvas tree to Skia and check it matches the layout."""

from __future__ import annotations

import asyncio
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
from src.sekai.base.utils import get_img_from_path
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
    p = IRPainter(
        size,
        assets_base_dir=str(ASSETS_BASE_DIR),
        font_dir=str(FONT_DIR),
        default_font=DEF,
        bold_font=DEFAULT_BOLD_FONT,
        bg_hour=15.5,
    )
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
    p = IRPainter(
        (40, 40),
        assets_base_dir=str(ASSETS_BASE_DIR),
        font_dir=str(FONT_DIR),
        default_font=DEF,
        bold_font=DEFAULT_BOLD_FONT,
    )
    p.paste(red, (8, 8), (24, 24))
    scene, mem = p.build_scene()
    assert len(mem) == 1  # the runtime image was captured as a mem:<key> entry
    result = _native.render_scene(json.dumps(scene).encode(), mem)
    img = Image.open(BytesIO(result["image_bytes"])).convert("RGBA")
    assert img.getpixel((20, 20))[0] > 200  # red present


def test_irpainter_path_image_renders_without_mem_transport(tmp_path):
    asset = tmp_path / "icons" / "red.png"
    asset.parent.mkdir(parents=True)
    Image.new("RGBA", (8, 8), (255, 0, 0, 255)).save(asset)
    source = asyncio.run(get_img_from_path(tmp_path, "icons/red.png", on_missing="raise"))

    painter = IRPainter(
        (40, 40),
        assets_base_dir=str(tmp_path),
        font_dir=str(FONT_DIR),
        default_font=DEF,
        bold_font=DEFAULT_BOLD_FONT,
    )
    painter.paste(source, (8, 8), (24, 24))
    scene, mem = painter.build_scene()
    assert mem == {}
    assert scene["root"]["children"][0]["path"] == "icons/red.png"

    result = _native.render_scene(json.dumps(scene).encode(), mem)
    img = Image.open(BytesIO(result["image_bytes"])).convert("RGBA")
    assert img.getpixel((20, 20))[0] > 200
    source.close()


def test_irpainter_gradient_text_maps_to_glyph_overlay_fill():
    # Gradient text no longer raises SkiaUnsupported: the gradient endpoints are mapped
    # onto the glyph overlay (ink bbox + 10px) and the Rust Text node renders the
    # gradient as a glyph-masked shader.
    from src.sekai.base.painter import FontDesc, LinearGradient

    p = IRPainter(
        (40, 40),
        assets_base_dir=str(ASSETS_BASE_DIR),
        font_dir=str(FONT_DIR),
        default_font=DEF,
        bold_font=DEFAULT_BOLD_FONT,
    )
    grad = LinearGradient((255, 0, 0, 255), (0, 0, 255, 255), (0, 0), (1, 0))
    p.text("hi", (0, 0), FontDesc(DEF, 20), fill=grad)
    scene, _mem = p.build_scene()
    texts = [n for n in scene["root"]["children"] if n.get("type") == "Text"]
    assert texts, "expected a text node in the scene"
    fill = texts[0]["fill"]
    assert isinstance(fill, dict)
    assert fill.get("kind") == "linear"


def _painter(size=(60, 60), assets_base_dir=None) -> IRPainter:
    return IRPainter(
        size,
        assets_base_dir=str(assets_base_dir or ASSETS_BASE_DIR),
        font_dir=str(FONT_DIR),
        default_font=DEF,
        bold_font=DEFAULT_BOLD_FONT,
    )


def test_irpainter_encoded_image_ref_ships_bytes_untouched():
    from src.sekai.base.utils import get_encoded_image_ref

    buf = BytesIO()
    Image.new("RGBA", (16, 16), (0, 255, 0, 255)).save(buf, "PNG")
    ref = get_encoded_image_ref(buf.getvalue())

    p = _painter((40, 40))
    p.paste(ref, (8, 8), (24, 24))
    p.paste(ref, (0, 0), (8, 8))  # same ref → same mem entry
    scene, mem = p.build_scene()
    assert mem == {"m0": ref.data}  # raw encoded bytes, no Python decode/re-encode
    result = _native.render_scene(json.dumps(scene).encode(), mem)
    img = Image.open(BytesIO(result["image_bytes"])).convert("RGBA")
    assert img.getpixel((20, 20))[1] > 200  # green present


def test_irpainter_asset_image_ref_emits_relative_path(tmp_path):
    from src.sekai.base.utils import get_asset_image_ref

    asset = tmp_path / "icons" / "blue.png"
    asset.parent.mkdir(parents=True)
    Image.new("RGBA", (8, 8), (0, 0, 255, 255)).save(asset)
    ref = asyncio.run(get_asset_image_ref(tmp_path, "icons/blue.png"))

    p = _painter((40, 40), assets_base_dir=tmp_path)
    p.paste(ref, (8, 8), (24, 24))
    scene, mem = p.build_scene()
    assert mem == {}
    assert scene["root"]["children"][0]["path"] == "icons/blue.png"


def test_irpainter_clip_roundrect_group_relative_children():
    p = _painter((60, 60))
    p.push_clip_roundrect((10, 10), (40, 40), 12)
    p.rect((10, 10), (40, 40), fill=(255, 0, 0, 255))
    p.pop_clip()
    p.rect((0, 0), (5, 5), fill=(0, 0, 0, 255))  # after pop: absolute again
    scene, mem = p.build_scene()

    group, tail = scene["root"]["children"]
    assert group["type"] == "Group"
    assert group["offset"] == [10.0, 10.0]
    assert group["size"] == [40.0, 40.0]
    assert group["clip"] == {"kind": "rrect", "radius": 12.0, "corners": [True, True, True, True]}
    assert group["children"][0]["pos"] == [0.0, 0.0]  # group-relative
    assert tail["pos"] == [0.0, 0.0]

    result = _native.render_scene(json.dumps(scene).encode(), mem)
    img = Image.open(BytesIO(result["image_bytes"])).convert("RGBA")
    assert img.getpixel((30, 30))[3] == 255  # kept inside clip
    assert img.getpixel((11, 11))[3] < 128  # rounded corner clipped away


def test_irpainter_unbalanced_clip_raises_skia_unsupported():
    from src.sekai.skia_renderer.ir_painter import SkiaUnsupported

    p = _painter()
    p.push_clip_roundrect((0, 0), (10, 10), 2)
    with pytest.raises(SkiaUnsupported):
        p.build_scene()
    with pytest.raises(SkiaUnsupported):
        _painter().pop_clip()


def test_irpainter_shadow_roundrect_emits_shadow_node():
    p = _painter()
    p.shadow_roundrect((10, 10), (30, 30), 8, shadow_width=6, shadow_alpha=0.4)
    scene, _mem = p.build_scene()
    node = scene["root"]["children"][0]
    assert node["type"] == "Shadow"
    assert node["pos"] == [10.0, 10.0]
    assert node["radius"] == 8
    assert node["alpha"] == 0.4


def test_irpainter_paste_src_rect_emits_source_rect_and_renders(tmp_path):
    from src.sekai.base.utils import get_asset_image_ref

    asset = tmp_path / "icons" / "half.png"
    asset.parent.mkdir(parents=True)
    src = Image.new("RGBA", (20, 10), (255, 0, 0, 255))
    src.paste((0, 255, 0, 255), (10, 0, 20, 10))  # right half green
    src.save(asset)
    ref = asyncio.run(get_asset_image_ref(tmp_path, "icons/half.png"))

    p = _painter((16, 16), assets_base_dir=tmp_path)
    p.paste(ref, (0, 0), (16, 16), src_rect=(10, 0, 20, 10))
    scene, mem = p.build_scene()
    node = scene["root"]["children"][0]
    assert mem == {}  # still pure path transport
    assert node["path"] == "icons/half.png"
    assert node["source_rect"] == [10.0, 0.0, 20.0, 10.0]

    result = _native.render_scene(json.dumps(scene).encode(), mem)
    img = Image.open(BytesIO(result["image_bytes"])).convert("RGBA")
    px = img.getpixel((8, 8))
    assert px[1] > 200  # green slice present
    assert px[0] < 60  # red half cropped away


def test_native_self_image_samples_rendered_content():
    from src.sekai.skia_renderer.ir_builder import IRBuilder
    from src.settings import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT, FONT_DIR

    b = IRBuilder(
        20,
        30,
        assets_base_dir=str(ASSETS_BASE_DIR),
        font_dir=str(FONT_DIR),
        default_font=DEF,
        bold_font=DEFAULT_BOLD_FONT,
    )
    # Top 20x20: left half red, right half blue. Footer 20x10 stretches the bottom strip.
    b.rect((0, 0), (10, 20), fill=(255, 0, 0, 255))
    b.rect((10, 0), (10, 20), fill=(0, 0, 255, 255))
    b.self_image((0, 20), (20, 10), source_rect=(0, 10, 20, 20))
    result = _native.render_scene(json.dumps(b.build()).encode(), {})
    img = Image.open(BytesIO(result["image_bytes"])).convert("RGBA")
    assert img.getpixel((4, 25))[0] > 200  # footer left = red sample
    assert img.getpixel((15, 25))[2] > 200  # footer right = blue sample
