"""TMP sampled fields must preserve L-mode pixels without an image dependency."""

import importlib.util
from pathlib import Path
import subprocess
import sys
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

import src.sekai.profile.custom_profile.gray_field as field_module
from src.sekai.profile.custom_profile.gray_field import GrayField


@pytest.fixture
def native():
    module = pytest.importorskip("haruki_skia_renderer")
    if getattr(module, "GRAY_FIELD_CAPABILITY", 0) < 1:
        pytest.skip("native gray8 resize API is required")
    return module


@pytest.mark.parametrize(
    ("source_size", "target_size"),
    [
        ((1, 1), (13, 9)),
        ((17, 13), (17, 13)),
        ((17, 13), (3, 5)),
        ((5, 7), (23, 29)),
        ((43, 31), (71, 11)),
        ((2, 405), (17, 11)),
        ((405, 2), (11, 17)),
        ((17, 13), (1, 1)),
    ],
)
def test_native_gray_bicubic_matches_pillow_exactly(native, source_size, target_size):
    rng = np.random.default_rng(827)
    for _ in range(8):
        pixels = rng.integers(0, 256, source_size[0] * source_size[1], dtype=np.uint8).tobytes()
        expected = Image.frombytes("L", source_size, pixels).resize(target_size, Image.Resampling.BICUBIC)
        assert native.resize_gray8_bicubic(pixels, source_size, target_size) == expected.tobytes()
        field = GrayField(*source_size, pixels).resize_bicubic(target_size)
        assert field.size == target_size
        assert field.pixels == expected.tobytes()


@pytest.mark.parametrize("box", [(1, 1, 4, 3), (-2, -3, 8, 7), (6, 7, 9, 10), (-8, -9, -1, -2)])
def test_gray_crop_zero_padding_matches_legacy(box):
    pixels = bytes(range(20))
    field = GrayField(5, 4, pixels)
    expected = Image.frombytes("L", field.size, pixels).crop(box)
    actual = field.crop(box)
    assert actual.size == expected.size
    assert actual.pixels == expected.tobytes()
    assert field.pixels == pixels


def test_gray_cached_fields_are_immutable():
    field = GrayField(3, 2, b"abcdef")
    view = np.asarray(field)
    assert not view.flags.writeable
    with pytest.raises(ValueError, match="read-only"):
        view[0, 0] = 1
    with pytest.raises(ValueError, match="WRITEABLE"):
        view.flags.writeable = True
    converted = np.asarray(field, dtype=np.float32)
    converted[0, 0] = 1
    assert field.pixels == b"abcdef"
    assert field.crop((0, 0, 3, 2)) is field
    assert field.resize_bicubic(field.size) is field
    with pytest.raises(TypeError, match="immutable"):
        GrayField(1, 1, bytearray([1]))


@pytest.mark.parametrize(
    ("pixels", "source", "target"),
    [
        (b"", (0, 1), (1, 1)),
        (b"", (1, 1), (1, 1)),
        (b"x", (1, 1), (0, 1)),
        (b"x", (1, 1), (32768, 1)),
        (b"", (8192, 8192), (1, 1)),
        (b"x", (1, 1), (8192, 8192)),
    ],
)
def test_native_gray_rejects_invalid_allocations(native, pixels, source, target):
    with pytest.raises(ValueError, match=r"dimensions|length"):
        native.resize_gray8_bicubic(pixels, source, target)


@pytest.mark.parametrize("failure", ["missing", "stale", "broken"])
def test_gray_resize_has_explicit_pillow_recovery(monkeypatch, failure):
    def load(name):
        if failure == "missing":
            raise ImportError("missing wheel")
        if failure == "stale":
            return SimpleNamespace(GRAY_FIELD_CAPABILITY=0)

        def broken(*args):
            raise RuntimeError("broken native resize")

        return SimpleNamespace(GRAY_FIELD_CAPABILITY=1, resize_gray8_bicubic=broken)

    monkeypatch.setattr(field_module.importlib, "import_module", load)
    field = GrayField(2, 1, bytes([0, 255])).resize_bicubic((3, 1))
    assert field.pixels == bytes([0, 128, 255])


def test_real_tmp_dynamic_fields_without_pillow(native):
    root = Path(__file__).resolve().parents[1]
    metadata = root / "data/custom_profile/tmp-font-assets/cn/metadata.json"
    payload_file = root / "out/parity-payloads/custom_profile_card.json"
    if not metadata.is_file() or not payload_file.is_file() or importlib.util.find_spec("fontTools") is None:
        pytest.skip("extracted TMP metadata and source font tools are required")
    code = r"""
import sys
import json
from pathlib import Path
from scripts.skia_no_pillow import _NoPillow
guard = _NoPillow()
sys.meta_path.insert(0, guard)
from src.sekai.profile.custom_profile.renderer import PNGRenderer, TextStyle
from src.sekai.profile.custom_profile.gray_field import GrayField
from src.settings import CUSTOM_PROFILE_ASSETS_DIR, CUSTOM_PROFILE_FONTS_DIR
payload = json.loads(Path(sys.argv[2]).read_text())
assets = Path(str(CUSTOM_PROFILE_ASSETS_DIR).format(region=payload["region"]))
fonts = Path(str(CUSTOM_PROFILE_FONTS_DIR).format(region=payload["region"]))
metadata = Path(sys.argv[1])
renderers = [PNGRenderer(masterdata=None, assets=assets, fonts=fonts,
    tmp_font_metadata=metadata, resources=payload["resources"]) for _ in range(2)]
first = renderers[0]
font_name = first.text_fonts.get(payload["card"]["customProfileCard"]["texts"][0]["fontId"], "FOT-RodinNTLGPro-DB")
font_path = first.font_path_for(font_name)
style = TextStyle(size=24, color="#ffffff", alpha=1, scale_x=1, cspace=0, mspace=None,
    indent=0, line_indent=0, line_height=None, rotate=0, voffset=0, mark_color=None,
    bold=False, italic=False, underline=False, strike=False)
for char in "描边Ag":
    original, asset = first.tmp_dynamic_glyph_sdf(font_name, font_path, char)
    assert isinstance(original.field, GrayField)
    assert min(original.field.pixels) < max(original.field.pixels)
    again, _ = renderers[1].tmp_dynamic_glyph_sdf(font_name, font_path, char)
    assert again is original, "second request must reuse the bounded immutable field cache"
    field = first.render_tmp_sdf_character_field(font_name, font_path, char, style, 24, "#000000", .25)
    assert field is not None and isinstance(field[0], GrayField)
# Also cover the bitmap-to-distance fallback by making contours unavailable.
first.tmp_vector_glyph_sdf_field = lambda *args: None
fallback, _ = first.tmp_dynamic_glyph_sdf(font_name, font_path, "Z")
assert isinstance(fallback.field, GrayField)
assert min(fallback.field.pixels) < max(fallback.field.pixels)
assert not guard.rejected
assert not any(name == "PIL" or name.startswith("PIL.") for name in sys.modules)
print("Dynamic TMP vector and bitmap fields, cache hits and bicubic placement passed without Pillow")
"""
    result = subprocess.run(
        [sys.executable, "-X", "gil=0", "-c", code, str(metadata), str(payload_file)],
        cwd=root,
        text=True,
        capture_output=True,
        timeout=90,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_native_field_limit_errors_do_not_retry_an_unbounded_legacy_resize(monkeypatch):
    from src.sekai.profile.custom_profile import pillow_fields

    def reject(*args):
        raise ValueError("gray8 scratch exceeds working limit")

    def unexpected(*args):
        pytest.fail("native allocation rejection must not enter Pillow resize")

    monkeypatch.setattr(
        field_module.importlib,
        "import_module",
        lambda name: SimpleNamespace(GRAY_FIELD_CAPABILITY=1, resize_gray8_bicubic=reject),
    )
    monkeypatch.setattr(pillow_fields, "resize_gray_bicubic", unexpected)
    with pytest.raises(ValueError, match="working limit"):
        GrayField(2, 1, bytes([0, 255])).resize_bicubic((3, 1))


def test_gray_windows_validate_size_before_allocation():
    field = GrayField(1, 1, b"x")
    for box in ((0, 0, 0, 1), (0, 0, 8192, 8192), (0, 0, 32768, 1)):
        with pytest.raises(ValueError, match="raster limits"):
            field.crop(box)


@pytest.mark.parametrize("source_size", [(1, 1), (2, 13), (17, 3), (19, 23)])
def test_gray_affine_matches_pillow_byte_for_byte(native, source_size):
    if getattr(native, "GRAY_FIELD_CAPABILITY", 0) < 2:
        pytest.skip("native gray8 affine API is required")
    rng = np.random.default_rng(8381)
    pixels = rng.integers(0, 256, source_size[0] * source_size[1], dtype=np.uint8).tobytes()
    matrices = [
        (1, 0, 0, 0, 1, 0),
        (1, 0, -0.5, 0, 1, 0.5),
        (0, 0, 1, 0, 0, 0.5),
        (0.43, 0.27, -2.4, -0.13, 1.51, 0.8),
        (-1, 0, source_size[0], 0, 1, 0),
    ]
    matrices.extend(tuple(rng.uniform(-2, 2, 6)) for _ in range(16))
    image = Image.frombytes("L", source_size, pixels)
    for matrix in matrices:
        size = (37, 31)
        expected = image.transform(size, Image.Transform.AFFINE, matrix, Image.Resampling.BICUBIC, fillcolor=0)
        actual = native.transform_gray8_bicubic(pixels, source_size, size, matrix)
        assert actual == expected.tobytes(), (source_size, matrix)
        assert GrayField(*source_size, pixels).transform_bicubic(size, matrix).pixels == actual


@pytest.mark.parametrize("matrix", [(1, 2), (1, 0, float("inf"), 0, 1, 0), (1, 0, 0, 0, float("nan"), 0)])
def test_gray_affine_rejects_invalid_matrices(matrix):
    with pytest.raises(ValueError, match="six finite"):
        GrayField(1, 1, b"x").transform_bicubic((3, 3), matrix)


def test_gray_affine_native_and_legacy_recovery(native, monkeypatch):
    if getattr(native, "GRAY_FIELD_CAPABILITY", 0) < 2:
        pytest.skip("native gray8 affine API is required")
    field = GrayField(3, 2, bytes([0, 200, 15, 99, 30, 255]))
    matrix = (1.2, -0.1, 0.3, 0.05, 0.8, -0.4)
    reference = field.transform_bicubic((7, 9), matrix)
    monkeypatch.setattr(field_module.importlib, "import_module", lambda name: SimpleNamespace(GRAY_FIELD_CAPABILITY=1))
    assert field.transform_bicubic((7, 9), matrix) == reference
    with pytest.raises(ValueError, match="finite"):
        native.transform_gray8_bicubic(b"x", (1, 1), (1, 1), [float("nan")] * 6)
    with pytest.raises(ValueError, match="limits"):
        native.transform_gray8_bicubic(b"x", (1, 1), (8192, 8192), [0] * 6)
