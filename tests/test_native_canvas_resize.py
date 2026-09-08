"""Canvas output scaling preserves the completed logical raster, including straight alpha."""

import asyncio
from io import BytesIO
import json

import numpy as np
from PIL import Image
import pytest

from src.sekai.base.plot import Canvas, FillBg, TextBox, TextStyle
from src.sekai.skia_renderer.canvas import REQUIRED_NATIVE_IR_CAPABILITY, render_canvas_payload
from src.sekai.skia_renderer.ir_builder import IRBuilder
from src.settings import DEFAULT_FONT

try:
    import haruki_skia_renderer as native
except ImportError:
    native = None

pytestmark = pytest.mark.skipif(
    native is None or native.IR_CAPABILITY < REQUIRED_NATIVE_IR_CAPABILITY, reason="current native renderer required"
)


def _scene():
    builder = IRBuilder(47, 31, assets_base_dir=".", font_dir=".", default_font="unused", bold_font="unused")
    builder.rect((0, 0), (47, 31), fill=(40, 80, 120, 85), blend="src")
    builder.roundrect((5, 3), (35, 23), 5, fill=(220, 30, 80, 160))
    builder.rect((23, 0), (1, 31), fill=(250, 200, 10, 255))
    return builder.build()


def _render(scene):
    return native.render_scene(json.dumps(scene).encode(), {})


def _image(payload):
    return Image.open(BytesIO(payload["image_bytes"])).convert("RGBA")


@pytest.mark.parametrize("encoder", ["mtpng", "skia"])
@pytest.mark.parametrize("size", [(70, 46), (17, 13), (48, 30)])
def test_post_resize_matches_pillow_on_native_logical_pixels(monkeypatch, encoder, size):
    monkeypatch.setenv("HARUKI_SKIA_PNG_ENCODER", encoder)
    scene = _scene()
    logical = _image(_render(scene))
    scene["post_resize"] = dict(zip(("width", "height"), size))
    actual = _image(_render(scene))
    expected = logical.resize(size, Image.Resampling.BILINEAR)
    np.testing.assert_array_equal(np.asarray(actual), np.asarray(expected))


def test_resized_jpeg_respects_output_dimensions_and_quality():
    scene = _scene()
    scene.update(post_resize={"width": 70, "height": 46}, export_format="jpg", jpg_quality=95)
    fine = _render(scene)
    scene["jpg_quality"] = 20
    coarse = _render(scene)
    assert fine["media_type"] == "image/jpeg"
    assert _image(fine).size == (70, 46)
    assert fine["image_bytes"] != coarse["image_bytes"]
    # JPEG discards alpha without darkening the straight RGB channels against black.
    expected = _image(_render(_scene())).resize((70, 46), Image.Resampling.BILINEAR).getpixel((3, 3))[:3]
    actual = _image(fine).getpixel((3, 3))[:3]
    assert max(abs(a - b) for a, b in zip(actual, expected)) <= 3


@pytest.mark.parametrize("size", [(0, 10), (-1, 10), (32768, 10), (10, 32768)])
def test_resize_rejects_invalid_dimensions_before_render(size):
    scene = _scene()
    scene["post_resize"] = dict(zip(("width", "height"), size))
    with pytest.raises(RuntimeError, match="post_resize dimensions"):
        _render(scene)


def test_resize_rejects_conflicting_transform_scale():
    scene = _scene()
    scene.update(post_resize={"width": 70, "height": 46}, scale=2)
    with pytest.raises(RuntimeError, match="cannot be combined"):
        _render(scene)


def test_resize_accounts_for_output_copy_and_source_readback():
    scene = _scene()
    scene.update(post_resize={"width": 200, "height": 100}, limits={"max_scene_bytes": 200 * 100 * 4})
    with pytest.raises(RuntimeError, match=r"post_resize.*scene byte limit"):
        _render(scene)
    scene.update(post_resize={"width": 2, "height": 2}, limits={"max_scene_bytes": 47 * 31 * 4})
    with pytest.raises(RuntimeError, match=r"post_resize.*scene byte limit"):
        _render(scene)


def test_canvas_fractional_scale_preserves_dimension_truncation():
    canvas = Canvas(w=2100, h=8, bg=FillBg((80, 120, 40, 255))).set_padding(0)
    result = asyncio.run(render_canvas_payload(canvas, endpoint="test_resize", scale=1.0005))
    assert result is not None
    assert (result.image_width, result.image_height) == (2101, 8)


def test_resize_scratch_uses_remaining_scene_budget():
    scene = _scene()
    scene.update(post_resize={"width": 17, "height": 13}, limits={"max_scene_bytes": 12000})
    with pytest.raises(RuntimeError, match=r"post_resize failed:.*scratch"):
        _render(scene)


def test_scaled_native_text_matches_logical_pillow_resize(real_fonts):
    def canvas():
        with Canvas(w=170, h=55, bg=FillBg((255, 255, 255, 255))).set_padding(4) as page:
            TextBox("瑞希 779", TextStyle(font=DEFAULT_FONT, size=24))
        return page

    # Ordinary widget glyphs follow main's Skia appearance. Verify the independent
    # Pillow resize oracle on those logical pixels, rather than changing glyphs
    # to Pillow BASIC while testing the post-render resize contract.
    logical_payload = asyncio.run(render_canvas_payload(canvas(), endpoint="test_resize", export_format="png"))
    assert logical_payload is not None
    logical = Image.open(BytesIO(logical_payload.image_bytes)).convert("RGBA")
    expected = logical.resize((int(logical.width * 1.5), int(logical.height * 1.5)), Image.Resampling.BILINEAR)
    payload = asyncio.run(render_canvas_payload(canvas(), endpoint="test_resize", scale=1.5, export_format="png"))
    assert payload is not None
    actual = Image.open(BytesIO(payload.image_bytes)).convert("RGBA")
    np.testing.assert_array_equal(np.asarray(actual), np.asarray(expected))


def test_large_parallel_post_resize_matches_pillow():
    scene = _scene()
    scene["canvas"] = {"width": 1024, "height": 1200}
    scene["root"]["children"][0]["size"] = [1024, 1200]
    logical = _image(_render(scene))
    size = (730, 850)  # Both passes exceed the native parallel-row threshold.
    scene["post_resize"] = dict(zip(("width", "height"), size))
    actual = _image(_render(scene))
    expected = logical.resize(size, Image.Resampling.BILINEAR)
    np.testing.assert_array_equal(np.asarray(actual), np.asarray(expected))
