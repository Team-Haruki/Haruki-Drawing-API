"""Unquantized TMP fields must retain precision and fail closed on bad transport."""

from io import BytesIO
import json
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

from src.sekai.profile.custom_profile.float_field import FloatField
from src.sekai.profile.custom_profile.renderer import PNGRenderer, TMPSdfShadingScalars, TMPSdfUnderlayScalars
from src.sekai.skia_renderer.ir_builder import IRBuilder


@pytest.fixture
def native():
    module = pytest.importorskip("haruki_skia_renderer")
    if getattr(module, "IR_CAPABILITY", 0) < 28:
        pytest.skip("float32 SdfQuad support required")
    return module


def _scene(width=17, height=13):
    root = Path(__file__).resolve().parents[1] / "data"
    return IRBuilder(
        width,
        height,
        assets_base_dir=str(root),
        font_dir=str(root),
        default_font="SourceHanSansSC-Regular.otf",
        bold_font="SourceHanSansSC-Regular.otf",
    )


def _raw(field):
    return (field.width, field.height, field.width * 4, "f32le", "unpremul", field.pixels)


@pytest.mark.parametrize("underlay", [None, TMPSdfUnderlayScalars(14.0, 6.7, -2, 3, (17, 190, 50))])
@pytest.mark.parametrize("alpha", [0.0, 0.1, 0.7, 1.0])
def test_native_float_shading_matches_legacy_without_a8_quantization(native, underlay, alpha):
    samples = np.linspace(0.495, 0.505, 221, dtype=np.float32).reshape(13, 17)
    field = FloatField.from_array(samples)
    scalars = TMPSdfShadingScalars(200.0, 99.5, alpha, (71, 30, 201), underlay)
    renderer = object.__new__(PNGRenderer)
    expected = Image.new("RGBA", field.size, (255, 255, 255, 255))
    expected.alpha_composite(renderer._shade_field_with_scalars(samples, scalars))
    scene = _scene()
    scene.rect((0, 0), field.size, fill=(255, 255, 255, 255))
    u = (
        None
        if underlay is None
        else {
            "color": list(underlay.color),
            "scale": underlay.scale,
            "w": underlay.w,
            "shift": [underlay.shift_x, underlay.shift_y],
        }
    )
    scene.sdf_quad((0, 0), "mem:f", scalars.face_color, scalars.face_scale, scalars.face_w, alpha, u)
    result = native.render_scene(json.dumps(scene.build()).encode(), {"f": _raw(field)})
    actual = Image.open(BytesIO(result["image_bytes"])).convert("RGBA")
    diff = np.abs(np.asarray(expected).astype(int) - np.asarray(actual).astype(int))
    assert diff.max() <= 1
    assert result["native_metrics"]["sdf_quad_count"] == 1
    if alpha:
        quantized = np.rint(samples * 255).astype(np.uint8).astype(np.float32) / 255
        wrong = Image.new("RGBA", field.size, (255, 255, 255, 255))
        wrong.alpha_composite(renderer._shade_field_with_scalars(quantized, scalars))
        assert np.abs(np.asarray(wrong).astype(int) - np.asarray(actual).astype(int)).max() > 2


@pytest.mark.parametrize("value", [float("nan"), float("inf"), -0.001, 1.001])
def test_nonfinite_or_out_of_range_samples_are_rejected(native, value):
    pixels = np.array([value], dtype="<f4").tobytes()
    with pytest.raises(ValueError, match="finite"):
        FloatField(1, 1, pixels)
    with pytest.raises(ValueError, match="finite"):
        native.render_scene(json.dumps(_scene().build()).encode(), {"bad": (1, 1, 4, "f32le", "unpremul", pixels)})


@pytest.mark.parametrize("change", ["stride", "length", "mutable", "view", "alpha", "size"])
def test_invalid_float_transport_is_never_silently_skipped(native, change):
    raw = [1, 1, 4, "f32le", "unpremul", bytes(4)]
    if change == "stride":
        raw[2] = 8
    elif change == "length":
        raw[5] = bytes(8)
    elif change == "mutable":
        raw[5] = bytearray(4)
    elif change == "view":
        raw[5] = memoryview(bytearray(4)).toreadonly()
    elif change == "alpha":
        raw[4] = "premul"
    else:
        raw[0] = -1
    with pytest.raises((ValueError, TypeError), match=r"f32le|bytes|dimensions"):
        native.render_scene(json.dumps(_scene().build()).encode(), {"bad": tuple(raw)})


def test_float_field_ownership_and_endianness():
    source = np.array([[0.1, 0.51]], dtype=">f4")
    field = FloatField.from_array(source)
    source[:] = 0
    view = np.asarray(field)
    assert not view.flags.writeable
    assert np.array_equal(view, np.array([[0.1, 0.51]], dtype=np.float32))
    with pytest.raises(ValueError, match="read-only"):
        view[0, 0] = 1


def test_float_fields_cannot_masquerade_as_images(native):
    scene = _scene(1, 1)
    scene.image("mem:f", (0, 0), (1, 1))
    with pytest.raises(RuntimeError, match="cannot be drawn as images"):
        native.render_scene(json.dumps(scene.build()).encode(), {"f": _raw(FloatField(1, 1, bytes(4)))})


@pytest.mark.parametrize("limit", ["node", "scratch"])
def test_shading_checks_node_and_scratch_budgets(native, limit):
    scene = _scene(1, 1)
    scene.sdf_quad((0, 0), "mem:f", (255, 255, 255), 1, 0, 1)
    ir = scene.build()
    ir["limits"] = {
        "max_node_pixels": 1 if limit == "node" else 100,
        "max_scene_bytes": 1024 if limit == "node" else 32,
    }
    field = FloatField(2, 2, bytes(16))
    with pytest.raises(RuntimeError, match=r"SdfQuad.*limit|SdfQuad scratch|SDF shading patch runtime.*scene limit"):
        native.render_scene(json.dumps(ir).encode(), {"f": _raw(field)})
