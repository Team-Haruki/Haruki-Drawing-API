"""JPEG output: quality 85 with full-resolution chroma (4:4:4) unless configured otherwise."""

import asyncio
from io import BytesIO
import json

from PIL import Image, JpegImagePlugin
from pydantic import ValidationError
import pytest

from src.core.utils import _encode_image
from src.sekai.base.plot import Canvas, FillBg
from src.sekai.skia_renderer.canvas import REQUIRED_NATIVE_IR_CAPABILITY, render_canvas_payload
from src.sekai.skia_renderer.ir_builder import IRBuilder
from src.settings import DrawingSettings, Settings, settings

try:
    import haruki_skia_renderer as native
except ImportError:
    native = None

needs_native = pytest.mark.skipif(
    native is None or native.IR_CAPABILITY < REQUIRED_NATIVE_IR_CAPABILITY, reason="current native renderer required"
)

# PIL's JpegImagePlugin.get_sampling() codes.
SAMPLING = {"444": 0, "422": 1, "420": 2}


def _sampling(data: bytes) -> int:
    with Image.open(BytesIO(data)) as image:
        return JpegImagePlugin.get_sampling(image)


def _striped_scene(**kwargs) -> dict:
    # One-pixel red/blue columns: exactly the chroma detail that 4:2:0 averages away.
    builder = IRBuilder(32, 16, assets_base_dir=".", font_dir=".", default_font="u", bold_font="u", **kwargs)
    builder.rect((0, 0), (32, 16), fill=(255, 255, 255, 255))
    for x in range(32):
        builder.rect((x, 0), (1, 16), fill=(255, 0, 0, 255) if x % 2 == 0 else (0, 0, 255, 255))
    return builder.build()


def _render(scene: dict) -> dict:
    return native.render_scene(json.dumps(scene).encode(), {})


def test_settings_default_to_q85_444():
    drawing = DrawingSettings()
    assert drawing.jpg_quality == 85
    assert drawing.jpg_subsampling == "444"


def test_subsampling_reads_the_environment_and_is_validated(monkeypatch):
    monkeypatch.setenv("HARUKI_DRAWING__JPG_SUBSAMPLING", "420")
    assert Settings().drawing.jpg_subsampling == "420"
    with pytest.raises(ValidationError):
        DrawingSettings(jpg_subsampling="411")


def test_ir_builder_emits_quality_and_subsampling():
    scene = IRBuilder(4, 4, assets_base_dir=".", font_dir=".", default_font="u", bold_font="u").build()
    assert (scene["jpg_quality"], scene["jpg_subsampling"]) == (85, "444")
    scene = _striped_scene(export_format="jpg", jpg_quality=70, jpg_subsampling="420")
    assert (scene["jpg_quality"], scene["jpg_subsampling"]) == (70, "420")
    with pytest.raises(ValueError, match="subsampling"):
        IRBuilder(4, 4, assets_base_dir=".", font_dir=".", default_font="u", bold_font="u", jpg_subsampling="4:4:4")


@needs_native
@pytest.mark.parametrize("mode", ["444", "422", "420"])
def test_native_jpeg_follows_scene_subsampling(mode):
    result = _render(_striped_scene(export_format="jpg", jpg_subsampling=mode))
    assert result["media_type"] == "image/jpeg"
    assert _sampling(result["image_bytes"]) == SAMPLING[mode]


@needs_native
def test_native_jpeg_without_scene_keys_is_444():
    # An IR that predates the keys (or a hand-built one) still gets the q85 4:4:4 default.
    scene = _striped_scene(export_format="jpg")
    del scene["jpg_quality"], scene["jpg_subsampling"]
    explicit = _render(_striped_scene(export_format="jpg", jpg_quality=85, jpg_subsampling="444"))
    assert _render(scene)["image_bytes"] == explicit["image_bytes"]


@needs_native
def test_native_444_keeps_alternating_column_colours():
    def pixels(mode: str):
        data = _render(_striped_scene(export_format="jpg", jpg_subsampling=mode))["image_bytes"]
        with Image.open(BytesIO(data)) as image:
            return image.convert("RGB").getpixel((8, 8)), image.convert("RGB").getpixel((9, 8))

    (red, blue) = pixels("444")
    assert red[0] > 200
    assert red[2] < 60
    assert blue[2] > 200
    assert blue[0] < 60
    # 4:2:0 blends each column pair towards purple: the regression this setting removes.
    (left, right) = pixels("420")
    assert abs(left[0] - right[0]) < abs(red[0] - blue[0]) / 2


@needs_native
def test_resized_native_jpeg_follows_scene_subsampling():
    scene = _striped_scene(export_format="jpg", jpg_subsampling="444")
    scene["post_resize"] = {"width": 64, "height": 32}
    assert _sampling(_render(scene)["image_bytes"]) == SAMPLING["444"]
    scene["jpg_subsampling"] = "420"
    assert _sampling(_render(scene)["image_bytes"]) == SAMPLING["420"]


@needs_native
def test_canvas_payload_uses_configured_subsampling(monkeypatch):
    import src.sekai.skia_renderer.canvas as skia_canvas

    canvas = Canvas(w=32, h=16, bg=FillBg((200, 30, 60, 255))).set_padding(0)
    payload = asyncio.run(render_canvas_payload(canvas, endpoint="test_jpeg", export_format="jpg"))
    assert payload is not None
    assert payload.media_type == "image/jpeg"
    assert _sampling(payload.image_bytes) == SAMPLING["444"]
    monkeypatch.setattr(skia_canvas, "JPG_SUBSAMPLING", "420")
    canvas = Canvas(w=32, h=16, bg=FillBg((200, 30, 60, 255))).set_padding(0)
    payload = asyncio.run(render_canvas_payload(canvas, endpoint="test_jpeg", export_format="jpg"))
    assert _sampling(payload.image_bytes) == SAMPLING["420"]


def test_pillow_reference_encode_follows_setting(monkeypatch):
    image = Image.new("RGB", (32, 16), (10, 120, 200))
    buffer, media_type, _ = _encode_image(image, "jpg", 85)
    assert media_type == "image/jpeg"
    assert _sampling(buffer.getvalue()) == SAMPLING["444"]
    monkeypatch.setattr(settings.drawing, "jpg_subsampling", "420")
    buffer, _, _ = _encode_image(Image.new("RGB", (32, 16), (10, 120, 200)), "jpg", 85)
    assert _sampling(buffer.getvalue()) == SAMPLING["420"]
    buffer, _, _ = _encode_image(Image.new("RGB", (32, 16), (10, 120, 200)), "jpg", 85, jpeg_subsampling="4:2:2")
    assert _sampling(buffer.getvalue()) == SAMPLING["422"]
