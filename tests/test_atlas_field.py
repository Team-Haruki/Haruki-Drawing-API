"""Static atlas decode parity, bounded allocation and cross-request cache validity."""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
import struct
from types import SimpleNamespace
import zlib

from PIL import Image
import pytest

from src.core.pillow_telemetry import begin_pillow_touch_scope, end_pillow_touch_scope, take_pillow_touch_snapshot
from src.sekai.profile.custom_profile import atlas_field, cache
from src.sekai.profile.custom_profile.gray_field import GrayField
from src.sekai.profile.custom_profile.renderer import PNGRenderer


@pytest.fixture
def native():
    module = pytest.importorskip("haruki_skia_renderer")
    if getattr(module, "ALPHA_FIELD_CAPABILITY", 0) < 1:
        pytest.skip("native alpha field API required")
    return module


@pytest.fixture(autouse=True)
def atlas_cache(monkeypatch):
    pool = cache.BoundedCache("atlas_test", 16, 8 * 1024 * 1024, cache._image_bytes)
    monkeypatch.setattr(atlas_field, "SPRITE_ATLAS_CACHE", pool)
    return pool


def _unexpected(*args):
    pytest.fail("legacy atlas decoder reached")


@pytest.mark.parametrize("mode", ["RGBA", "RGB", "LA", "L", "P"])
@pytest.mark.parametrize("format_name", ["PNG", "WEBP"])
def test_native_alpha_matches_legacy_decode(native, tmp_path, mode, format_name):
    # Exercise every uint8 alpha level; palette tRNS and implicit opaque alpha matter.
    source = Image.new("RGBA", (32, 8), (12, 120, 230, 255))
    source.putalpha(Image.frombytes("L", source.size, bytes(range(256))))
    image = source.convert(mode)
    if mode == "P":
        image = Image.frombytes("P", source.size, bytes(range(256)))
        image.putpalette([v for i in range(256) for v in (i, 255 - i, 75)])
        image.info["transparency"] = bytes(range(256))
    path = tmp_path / f"atlas.{format_name.lower()}"
    image.save(path, format_name, lossless=True)
    with Image.open(path) as legacy:
        expected = legacy.convert("RGBA").getchannel("A").tobytes()
    actual = atlas_field.load_atlas_alpha(path, max_pixels=256, legacy_decode=_unexpected)
    assert actual == GrayField(32, 8, expected)
    assert native.asset_alpha_field(str(tmp_path), path.name, 256) == (32, 8, expected)


def test_native_rejects_invalid_oversize_and_escape_before_decode(native, tmp_path):
    path = tmp_path / "atlas.png"
    Image.new("RGBA", (1, 1)).save(path)
    original = path.read_bytes()
    header = struct.pack(">IIBBBBB", 5000, 5000, 8, 6, 0, 0, 0)
    path.write_bytes(original[:16] + header + struct.pack(">I", zlib.crc32(b"IHDR" + header)) + original[33:])
    with pytest.raises(ValueError, match="pixel/dimension limit"):
        native.asset_alpha_field(str(tmp_path), path.name, 16 * 1024 * 1024)
    path.write_bytes(b"corrupt")
    with pytest.raises(ValueError, match="invalid"):
        native.asset_alpha_field(str(tmp_path), path.name, 10)
    root = tmp_path / "confined"
    root.mkdir()
    (root / path.name).symlink_to(path)
    with pytest.raises(ValueError, match="escapes"):
        native.asset_alpha_field(str(root), path.name, 10)


def test_same_renderer_and_other_request_observe_replacement_and_deletion(native, tmp_path, atlas_cache):
    path = tmp_path / "atlas.png"
    Image.new("RGBA", (9, 7), (12, 45, 100, 33)).save(path)
    renderer = object.__new__(PNGRenderer)
    renderer.max_layer_pixels = 100
    first = renderer.tmp_atlas_alpha(path)
    assert first.pixels == bytes([33]) * 63
    assert renderer.tmp_atlas_alpha(path) is first
    assert atlas_cache.stats()["bytes"] == 63
    stamp = path.stat().st_mtime_ns
    Image.new("RGBA", (9, 7), (12, 45, 100, 77)).save(path)
    os.utime(path, ns=(stamp + 1_000_000, stamp + 1_000_000))
    second = renderer.tmp_atlas_alpha(path)
    assert second.pixels == bytes([77]) * 63
    other = object.__new__(PNGRenderer)
    other.max_layer_pixels = 100
    assert other.tmp_atlas_alpha(path) is second
    with ThreadPoolExecutor(4) as pool:
        assert all(field is second for field in pool.map(renderer.tmp_atlas_alpha, [path] * 12))
    other.max_layer_pixels = 50
    with pytest.raises(ValueError, match="would allocate"):
        other.tmp_atlas_alpha(path)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        renderer.tmp_atlas_alpha(path)


def test_replacement_during_decode_does_not_poison_cache(native, monkeypatch, tmp_path, atlas_cache):
    path = tmp_path / "atlas.png"
    Image.new("RGBA", (3, 2), (12, 45, 100, 33)).save(path)
    decode = native.asset_alpha_field

    def replace(root, name, limit):
        result = decode(root, name, limit)
        stamp = path.stat().st_mtime_ns
        Image.new("RGBA", (3, 2), (12, 45, 100, 77)).save(path)
        os.utime(path, ns=(stamp + 1_000_000, stamp + 1_000_000))
        return result

    monkeypatch.setattr(native, "asset_alpha_field", replace)
    first = atlas_field.load_atlas_alpha(path, max_pixels=6, legacy_decode=_unexpected)
    assert first.pixels == bytes([33]) * 6
    assert atlas_cache.stats()["bytes"] == 0
    monkeypatch.setattr(native, "asset_alpha_field", decode)
    assert atlas_field.load_atlas_alpha(path, max_pixels=6, legacy_decode=_unexpected).pixels == bytes([77]) * 6


@pytest.mark.parametrize("failure", ["missing", "stale", "broken"])
def test_legacy_recovery_stays_visible_on_every_call(monkeypatch, tmp_path, atlas_cache, failure):
    path = tmp_path / "atlas.png"
    image = Image.new("L", (3, 2), 100)
    image.save(path)

    def broken(*args):
        raise RuntimeError("broken native decoder")

    def module(name):
        if failure == "missing":
            raise ImportError("missing extension")
        return SimpleNamespace(ALPHA_FIELD_CAPABILITY=0 if failure == "stale" else 1, asset_alpha_field=broken)

    monkeypatch.setattr(atlas_field.importlib, "import_module", module)
    for _ in range(2):
        token = begin_pillow_touch_scope()
        try:
            field = atlas_field.load_atlas_alpha(path, max_pixels=6, legacy_decode=lambda _: image)
            assert field == GrayField(3, 2, bytes([100]) * 6)
            assert take_pillow_touch_snapshot().native_purity == "hybrid"
        finally:
            end_pillow_touch_scope(token)
    assert atlas_cache.stats()["bytes"] == 0


def test_native_honors_cold_layer_limit(native, tmp_path):
    path = tmp_path / "atlas.png"
    Image.new("RGBA", (9, 7)).save(path)
    with pytest.raises(ValueError, match="pixel/dimension limit"):
        native.asset_alpha_field(str(tmp_path), path.name, 62)
    with pytest.raises(ValueError, match="pixel/dimension limit"):
        atlas_field.load_atlas_alpha(path, max_pixels=62, legacy_decode=_unexpected)
    for limit in (0, 16 * 1024 * 1024 + 1):
        with pytest.raises(ValueError, match="max_pixels"):
            native.asset_alpha_field(str(tmp_path), path.name, limit)


def test_disabled_or_too_small_pool_does_not_keep_fields(native, monkeypatch, tmp_path):
    path = tmp_path / "atlas.png"
    Image.new("RGBA", (3, 2)).save(path)
    for entries, byte_limit in ((0, 100), (10, 5)):
        pool = cache.BoundedCache("small", entries, byte_limit, cache._image_bytes)
        monkeypatch.setattr(atlas_field, "SPRITE_ATLAS_CACHE", pool)
        atlas_field.load_atlas_alpha(path, max_pixels=6, legacy_decode=_unexpected)
        assert pool.stats()["bytes"] == 0


@pytest.mark.parametrize("font", ["DB", "EB"])
def test_extracted_static_atlas_exact_alpha(native, font):
    from src.sekai.profile.custom_profile.renderer import TMPFontLibrary

    metadata = Path("data/custom_profile/tmp-font-assets/cn/metadata.json")
    if not metadata.exists():
        pytest.skip("extracted static TMP font required")
    asset = TMPFontLibrary.load(metadata).active_asset(f"FOT-RodinNTLGPro-{font}-OnDemand")
    if asset is None or not asset.atlas_paths:
        pytest.skip("static TMP atlas required")
    for path in asset.atlas_paths:
        with Image.open(path) as image:
            expected = image.convert("RGBA").getchannel("A").tobytes()
        actual = atlas_field.load_atlas_alpha(path, max_pixels=16 * 1024 * 1024, legacy_decode=_unexpected)
        assert actual.pixels == expected


@pytest.mark.parametrize(("color_type", "channels"), [(6, 4), (4, 2)])
def test_sixteen_bit_png_alpha_downconversion_matches_legacy(native, tmp_path, color_type, channels):
    def chunk(kind, data):
        return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))

    values = [0, 1, 255, 256, 257, 32767, 32768, 65000, 65534, 65535]
    row = b"\0" + b"".join(
        struct.pack(">H", value) for alpha in values for value in ([10000] * (channels - 1) + [alpha])
    )
    data = (
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", 10, 1, 16, color_type, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(row))
        + chunk(b"IEND", b"")
    )
    path = tmp_path / "atlas16.png"
    path.write_bytes(data)
    with Image.open(path) as image:
        expected = image.convert("RGBA").getchannel("A").tobytes()
    assert native.asset_alpha_field(str(tmp_path), path.name, 10) == (10, 1, expected)
