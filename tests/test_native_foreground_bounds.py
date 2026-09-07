"""Native foreground scans preserve crop decisions without creating Python pixel images."""

import asyncio
from io import BytesIO
from types import SimpleNamespace

import numpy as np
from PIL import Image, ImageDraw
import pytest

from src.sekai.base import image_info
from src.sekai.base.pillow_image_info import probe_foreground_bounds as legacy_probe
from src.sekai.base.utils import get_asset_image_ref, get_encoded_image_ref
from src.sekai.costume.drawer import _costume_preview_cover_crop_box


@pytest.fixture
def native():
    module = image_info._native()
    if module is None or getattr(module, "FOREGROUND_BOUNDS_CAPABILITY", 0) < 1:
        pytest.skip("native foreground metadata required")
    return module


def _ref(image):
    buf = BytesIO()
    image.save(buf, "PNG")
    return get_encoded_image_ref(buf.getvalue())


def _source(size, kind):
    width, height = size
    image = Image.new("RGBA", size, (250, 252, 254, 0 if kind in {"alpha", "empty"} else 255))
    if kind == "empty":
        return image
    if kind == "gradient":
        pixels = np.array(image)
        pixels[:, :, 0] = np.linspace(90, 210, height).astype(np.uint8)[:, None]
        pixels[:, :, 1] = np.linspace(100, 220, height).astype(np.uint8)[:, None]
        pixels[:, :, 2] = np.linspace(110, 230, height).astype(np.uint8)[:, None]
        image = Image.fromarray(pixels)
    ImageDraw.Draw(image).rectangle((width // 3, height // 7, width * 2 // 3, height * 6 // 7), fill=(80, 60, 55, 255))
    return image


@pytest.mark.parametrize("size", [(31, 27), (701, 499), (1401, 1003), (2800, 2000)])
@pytest.mark.parametrize("kind", ["opaque", "alpha", "empty", "gradient"])
def test_foreground_and_cover_crop_match_legacy_exactly(native, size, kind, monkeypatch):
    image = _source(size, kind)
    ref = _ref(image)
    expected = legacy_probe(image)
    crop = _costume_preview_cover_crop_box(image)
    monkeypatch.setattr(Image, "open", lambda *a, **k: pytest.fail("Pillow decode reached"))
    assert image_info.probe_foreground_bounds(ref) == expected
    assert _costume_preview_cover_crop_box(ref) == crop


def test_foreground_threshold_uses_strict_comparison(native):
    image = Image.new("RGBA", (100, 100), (100, 100, 100, 255))
    draw = ImageDraw.Draw(image)
    draw.rectangle((10, 10, 29, 29), fill=(136, 136, 136, 255))
    draw.rectangle((50, 50, 69, 69), fill=(137, 137, 137, 255))
    assert image_info.probe_foreground_bounds(_ref(image)) == (50, 50, 70, 70)


def test_one_pixel_wide_source_is_valid(native):
    ref = _ref(Image.new("RGBA", (1, 29), "white"))
    assert image_info.probe_foreground_bounds(ref) is None
    assert legacy_probe(ref) is None


def test_foreground_asset_replacement_changes_bounds_and_confines_symlinks(native, tmp_path):
    root = tmp_path / "assets"
    root.mkdir()
    path = root / "preview.png"
    _source((80, 60), "opaque").save(path)
    ref = asyncio.run(get_asset_image_ref(root, path.name))
    assert image_info.probe_foreground_bounds(ref) is not None
    Image.new("RGBA", (80, 60), "white").save(path)
    assert image_info.probe_foreground_bounds(ref) is None
    outside = tmp_path / "outside.png"
    Image.new("RGB", (7, 5)).save(outside)
    (root / "escape.png").symlink_to(outside)
    with pytest.raises(ValueError, match="escapes"):
        native.asset_foreground_bounds(str(root), "escape.png", 700)


@pytest.mark.parametrize("error", [AttributeError, RuntimeError, OSError])
def test_broken_native_foreground_probe_uses_isolated_fallback(native, monkeypatch, error):
    image = _source((81, 63), "opaque")
    ref = _ref(image)

    def broken(*args):
        raise error("broken foreground API")

    monkeypatch.setattr(
        image_info, "_native", lambda: SimpleNamespace(FOREGROUND_BOUNDS_CAPABILITY=1, encoded_foreground_bounds=broken)
    )
    assert image_info.probe_foreground_bounds(ref) == legacy_probe(image)


def test_foreground_rejects_invalid_bytes_without_fallback(native, monkeypatch):
    monkeypatch.setattr(Image, "open", lambda *a, **k: pytest.fail("Pillow decode reached"))
    with pytest.raises(ValueError, match="invalid"):
        native.encoded_foreground_bounds(b"corrupt", 700)


@pytest.mark.parametrize("format_name", ["JPEG", "WEBP"])
def test_encoded_photo_formats_keep_foreground_decisions(native, format_name):
    buffer = BytesIO()
    _source((1401, 1003), "gradient").convert("RGB").save(buffer, format=format_name, quality=90)
    data = buffer.getvalue()
    expected = legacy_probe(Image.open(BytesIO(data)))
    assert image_info.probe_foreground_bounds(get_encoded_image_ref(data)) == expected
