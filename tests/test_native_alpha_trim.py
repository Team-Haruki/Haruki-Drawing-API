"""Alpha bounds and crop/remap/resize ordering for lazy character silhouettes."""

import asyncio
from io import BytesIO
import json
import os

import numpy as np
from PIL import Image
import pytest

from src.sekai.base.image_info import probe_alpha_bounds
from src.sekai.base.plot import AlphaTrimImageBox, Canvas
from src.sekai.base.utils import get_asset_image_ref, get_encoded_image_ref
from src.sekai.skia_renderer.canvas import REQUIRED_NATIVE_IR_CAPABILITY, render_canvas_payload
from src.sekai.skia_renderer.ir_builder import IRBuilder

try:
    import haruki_skia_renderer as native
except ImportError:
    native = None

pytestmark = pytest.mark.skipif(
    native is None or getattr(native, "IR_CAPABILITY", 0) < REQUIRED_NATIVE_IR_CAPABILITY,
    reason="current native alpha crop required",
)


def _source():
    pixels = np.zeros((32, 40, 4), dtype=np.uint8)
    pixels[:, :, :3] = (90, 160, 210)
    pixels[4:28, 5:35, 3] = np.arange(24 * 30, dtype=np.uint16).reshape(24, 30) % 256
    pixels[2, 2, 3] = 1  # Bounds include alpha BELOW the later floor.
    return Image.fromarray(pixels)


def _ref(image):
    buffer = BytesIO()
    image.save(buffer, "PNG")
    return get_encoded_image_ref(buffer.getvalue())


def test_bounds_match_pillow_before_threshold_and_without_pillow_decode(monkeypatch):
    image = _source()
    ref = _ref(image)
    expected = image.getbbox()
    monkeypatch.setattr(Image, "open", lambda *a, **k: pytest.fail("Pillow decoder reached"))
    assert probe_alpha_bounds(ref) == expected == (2, 2, 35, 28)


def test_bounds_are_not_stale_after_same_size_asset_replacement(tmp_path):
    path = tmp_path / "silhouette.png"
    image = _source()
    image.save(path)
    ref = asyncio.run(get_asset_image_ref(tmp_path, path.name))
    assert probe_alpha_bounds(ref) == image.getbbox()
    Image.new("RGBA", image.size, (100, 200, 50, 0)).save(path)
    os.utime(path, ns=(ref.mtime_ns + 1_000_000, ref.mtime_ns + 1_000_000))
    assert probe_alpha_bounds(asyncio.run(get_asset_image_ref(tmp_path, path.name))) is None
    path.unlink()
    with pytest.raises(OSError, match="resolve asset"):
        probe_alpha_bounds(ref)


def test_native_bounds_confines_symlinks_and_rejects_invalid_data(tmp_path):
    root = tmp_path / "assets"
    root.mkdir()
    source = tmp_path / "outside.png"
    _source().save(source)
    (root / "escape.png").symlink_to(source)
    with pytest.raises(ValueError, match="escapes"):
        native.asset_alpha_bounds(str(root), "escape.png")
    with pytest.raises(ValueError, match="invalid"):
        native.encoded_alpha_bounds(b"corrupt")


@pytest.mark.parametrize("floor", [0, 36, 128, 254])
@pytest.mark.parametrize("size", [(33, 26), (19, 15), (67, 53)])
def test_lazy_alpha_trim_preserves_legacy_pixels_and_native_visible_parity(floor, size):
    image = _source()
    ref = _ref(image)
    bounds = probe_alpha_bounds(ref)
    expected = image.crop(bounds)
    expected.putalpha(
        expected.getchannel("A").point(
            [0 if value <= floor else min(255, int((value - floor) * 255 / (255 - floor))) for value in range(256)]
        )
    )
    expected = expected.resize(size, Image.Resampling.BICUBIC)
    background = Image.new("RGBA", size, (80, 120, 180, 255))
    background.alpha_composite(expected)

    def build():
        with Canvas(w=size[0], h=size[1]).set_padding(0) as canvas:
            canvas.add_draw_func(lambda _widget, painter: painter.rect((0, 0), size, (80, 120, 180, 255)))
            AlphaTrimImageBox(ref, bounds, floor, size=size, image_size_mode="fill", use_alpha_blend=True)
        return canvas

    legacy = build().get_img_sync()
    assert np.array_equal(np.asarray(legacy), np.asarray(background))
    result = asyncio.run(render_canvas_payload(build(), endpoint="test_alpha_trim", export_format="png"))
    assert result is not None
    actual = Image.open(BytesIO(result.image_bytes)).convert("RGBA")
    diff = np.abs(np.asarray(actual).astype(int) - np.asarray(background).astype(int))
    # One premultiplied source round-trip precedes LUT+resampling, and another
    # precedes SrcOver. Their RGB rounding accumulates; alpha itself is tested below.
    assert diff.max() <= 3
    assert diff.mean() < 0.25
    assert np.all(diff[:, :, 3] == 0)


def test_native_rejects_alpha_remap_after_resizing_before_loading_asset():
    builder = IRBuilder(20, 20, assets_base_dir=".", font_dir=".", default_font="unused", bold_font="unused")
    builder.image(
        "missing.png", (0, 0), (20, 20), source_rect=(0, 0, 40, 40), sampling="nearest", blend="src", alpha_floor=36
    )
    with pytest.raises(RuntimeError, match="natural-size"):
        native.render_scene(json.dumps(builder.build()).encode(), {})


def test_alpha_lut_preserves_all_256_alpha_values_exactly():
    pixels = np.zeros((1, 256, 4), dtype=np.uint8)
    pixels[:, :, :3] = 255
    pixels[0, :, 3] = np.arange(256)
    source = _ref(Image.fromarray(pixels))
    builder = IRBuilder(256, 1, assets_base_dir=".", font_dir=".", default_font="unused", bold_font="unused")
    builder.image(
        "mem:source", (0, 0), (256, 1), source_rect=(0, 0, 256, 1), sampling="nearest", blend="src", alpha_floor=36
    )
    result = native.render_scene(json.dumps(builder.build()).encode(), {"source": source.data})
    actual = np.asarray(Image.open(BytesIO(result["image_bytes"])))[:, :, 3]
    assert actual.tolist() == [[0 if v <= 36 else (v - 36) * 255 // 219 for v in range(256)]]


def test_native_alpha_bounds_rejects_oversize_before_pixel_allocation():
    import struct
    import zlib

    data = _ref(Image.new("RGBA", (1, 1))).data
    header = struct.pack(">IIBBBBB", 5000, 5000, 8, 6, 0, 0, 0)
    data = data[:16] + header + struct.pack(">I", zlib.crc32(b"IHDR" + header)) + data[33:]
    with pytest.raises(RuntimeError, match="64 MiB"):
        native.encoded_alpha_bounds(data)


def test_absent_native_alpha_bounds_uses_isolated_legacy_adapter(monkeypatch):
    from src.sekai.base import image_info

    source = _source()
    ref = _ref(source)
    monkeypatch.setattr(image_info, "_native", lambda: None)
    assert probe_alpha_bounds(ref) == source.getbbox()


def test_native_alpha_capacity_failure_keeps_legacy_recovery(monkeypatch):
    source = _source()
    ref = _ref(source)

    def unsupported(*args):
        raise RuntimeError("image analysis exceeds native capacity")

    monkeypatch.setattr(native, "encoded_alpha_bounds", unsupported)
    assert probe_alpha_bounds(ref) == source.getbbox()


def test_alpha_scan_reuses_metadata_but_restats_same_reference(tmp_path, monkeypatch):
    path = tmp_path / "alpha.png"
    image = _source()
    image.save(path)
    ref = asyncio.run(get_asset_image_ref(tmp_path, path.name))
    original = native.asset_alpha_bounds
    calls = []

    def scan(root, name):
        calls.append(name)
        return original(root, name)

    monkeypatch.setattr(native, "asset_alpha_bounds", scan)
    assert probe_alpha_bounds(ref) == image.getbbox()
    assert probe_alpha_bounds(ref) == image.getbbox()
    assert len(calls) == 1
    Image.new("RGBA", image.size, (0, 0, 0, 0)).save(path)
    os.utime(path, ns=(ref.mtime_ns + 1_000_000, ref.mtime_ns + 1_000_000))
    assert probe_alpha_bounds(ref) is None
    assert len(calls) == 2
