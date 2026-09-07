"""Source-over must not silently replace source-pixel writes in translucent panels."""

from io import BytesIO
import json

import numpy as np
from PIL import Image, ImageDraw, ImageFilter
import pytest

from src.sekai.skia_renderer.canvas import REQUIRED_NATIVE_IR_CAPABILITY
from src.sekai.skia_renderer.ir_builder import IRBuilder

try:
    import haruki_skia_renderer as native
except ImportError:
    native = None

pytestmark = pytest.mark.skipif(
    native is None or getattr(native, "IR_CAPABILITY", 0) < REQUIRED_NATIVE_IR_CAPABILITY,
    reason="current native panel primitives required",
)


def _builder():
    return IRBuilder(120, 90, assets_base_dir=".", font_dir=".", default_font="unused", bold_font="unused")


def _render(builder):
    result = native.render_scene(json.dumps(builder.build()).encode(), {})
    return Image.open(BytesIO(result["image_bytes"])).convert("RGBA")


def test_discrete_roundrect_replaces_alpha_and_keeps_outline_inside():
    builder = _builder()
    builder.rect((0, 0), (120, 90), fill=(0, 0, 255, 255))
    builder.roundrect(
        (10, 10),
        (90, 60),
        10,
        fill=(255, 255, 255, 112),
        stroke=(255, 255, 255, 150),
        stroke_width=2,
        replace_pixels=True,
    )
    actual = _render(builder)
    assert actual.getpixel((50, 40)) == (255, 255, 255, 112)
    assert actual.getpixel((50, 10)) == (255, 255, 255, 150)
    assert actual.getpixel((50, 11)) == (255, 255, 255, 150)
    assert actual.getpixel((50, 9)) == (0, 0, 255, 255)
    assert actual.getpixel((10, 10)) == (0, 0, 255, 255)


def test_shadow_blurs_color_channels_against_transparent_white():
    layer = Image.new("RGBA", (120, 90), (255, 255, 255, 0))
    ImageDraw.Draw(layer).rounded_rectangle((24, 26, 103, 65), radius=10, fill=(72, 96, 128, 30))
    expected = Image.new("RGBA", layer.size, "white")
    expected.alpha_composite(layer.filter(ImageFilter.GaussianBlur(6)))
    builder = _builder()
    builder.rect((0, 0), (120, 90), fill=(255, 255, 255, 255))
    builder.shadow((20, 20), (80, 40), 10, alpha=1, offset=(4, 6), sigma=6, color=(72, 96, 128, 30), straight_rgba=True)
    diff = np.abs(np.asarray(_render(builder)).astype(int) - np.asarray(expected).astype(int))
    assert diff.mean() < 0.3
    assert np.percentile(diff, 99) <= 1


def test_shadow_reserves_its_layer_before_drawing():
    builder = _builder()
    builder.shadow((20, 20), (80, 40), 10, sigma=6, straight_rgba=True)
    scene = builder.build()
    scene["limits"] = {"max_scene_bytes": 120 * 90 * 4 + 100}
    with pytest.raises(RuntimeError, match="straight RGBA shadow"):
        native.render_scene(json.dumps(scene).encode(), {})
