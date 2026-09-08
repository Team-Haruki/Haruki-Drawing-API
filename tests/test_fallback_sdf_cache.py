"""Fallback float32 SDF reuse shares the process glyph budget without changing math."""

from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
import sys
import threading
from types import SimpleNamespace
import warnings

import numpy as np
import pytest

from src.sekai.profile.custom_profile import cache, renderer
from src.sekai.profile.custom_profile.float_field import FloatField
from src.sekai.profile.custom_profile.gray_field import GrayField


@pytest.fixture
def glyph_pool(monkeypatch):
    pool = cache.BoundedCache("glyph_sdf", 16, 1024 * 1024, cache._glyph_sdf_bytes)
    monkeypatch.setattr(cache, "GLYPH_SDF_CACHE", pool)
    monkeypatch.setattr(renderer, "GLYPH_SDF_CACHE", pool)
    # Pin the normal locked production environment: OpenCV is not a dependency.
    monkeypatch.setitem(sys.modules, "cv2", None)
    return pool


@pytest.fixture
def mask():
    return GrayField(4, 3, bytes([0, 20, 150, 0, 60, 160, 255, 20, 0, 30, 80, 0]))


def counted_transform(monkeypatch):
    original = renderer.alpha_mask_to_sdf_field
    calls = []

    def measure(mask, spread, threshold):
        calls.append((mask, spread, threshold))
        return original(mask, spread, threshold)

    monkeypatch.setattr(renderer, "alpha_mask_to_sdf_field", measure)
    return calls


@pytest.mark.parametrize("pixels", [bytes(12), bytes([255] * 12), bytes(range(0, 240, 20))])
def test_float_sdf_cache_preserves_exact_samples(glyph_pool, pixels):
    mask = GrayField(4, 3, pixels)
    expected = renderer.alpha_mask_to_sdf_field(mask, 2.3, 160)
    first = renderer.cached_fallback_sdf_field(mask, 2.3, 160)
    second = renderer.cached_fallback_sdf_field(mask, 2.3, 160)
    assert first.dtype == second.dtype == np.float32
    assert np.array_equal(expected, first)
    assert first.tobytes() == second.tobytes() == expected.tobytes()
    assert glyph_pool.stats()["hits"] == 1
    assert glyph_pool.stats()["bytes"] == first.nbytes + cache.FLOAT_SDF_CACHE_ENTRY_OVERHEAD


def test_equal_new_masks_reuse_cached_content(glyph_pool, mask, monkeypatch):
    calls = counted_transform(monkeypatch)
    renderer.cached_fallback_sdf_field(mask, 1.5, 160)
    renderer.cached_fallback_sdf_field(GrayField(mask.width, mask.height, bytes(bytearray(mask.pixels))), 1.5, 160)
    assert len(calls) == 1
    assert glyph_pool.stats()["entries"] == 1


@pytest.mark.parametrize("change", ["pixel", "shape", "spread", "threshold"])
def test_sdf_inputs_are_distinct_cache_keys(glyph_pool, mask, monkeypatch, change):
    calls = counted_transform(monkeypatch)
    original = renderer.alpha_mask_to_sdf_field
    renderer.cached_fallback_sdf_field(mask, 1.5, 160)
    changed, spread, threshold = mask, 1.5, 160
    if change == "pixel":
        pixels = bytearray(mask.pixels)
        pixels[5] = 159
        changed = GrayField(mask.width, mask.height, bytes(pixels))
    elif change == "shape":
        changed = GrayField(3, 4, mask.pixels)
    elif change == "spread":
        spread = 1.5000001  # Do not round away a numerically meaningful setting.
    else:
        threshold = 161
    actual = renderer.cached_fallback_sdf_field(changed, spread, threshold)
    assert len(calls) == 2
    assert glyph_pool.stats()["entries"] == 2
    assert np.array_equal(actual, original(changed, spread, threshold))


def test_sdf_algorithm_is_part_of_key(glyph_pool, mask, monkeypatch):
    # This is a key-isolation check, not an approximation of OpenCV's math.
    calls = []

    def compute(mask, spread, threshold):
        calls.append(1)
        return np.full((mask.height, mask.width), 0.5, dtype=np.float32)

    monkeypatch.setattr(renderer, "alpha_mask_to_sdf_field", compute)
    renderer.cached_fallback_sdf_field(mask, 1, 160)
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(__version__="test-v1"))
    renderer.cached_fallback_sdf_field(mask, 1, 160)
    monkeypatch.setattr(renderer, "TMP_DYNAMIC_SDF_DISTANCE_MASK_SIZE", 3)
    renderer.cached_fallback_sdf_field(mask, 1, 160)
    monkeypatch.setitem(sys.modules, "cv2", SimpleNamespace(__version__="test-v2"))
    renderer.cached_fallback_sdf_field(mask, 1, 160)
    assert len(calls) == 4
    assert glyph_pool.stats()["entries"] == 4


def test_shared_float_samples_cannot_be_made_writeable(glyph_pool, mask):
    first = renderer.cached_fallback_sdf_field(mask, 2, 160)
    expected = first.copy()
    with pytest.raises(ValueError, match="read-only"):
        first[0, 0] = 1
    with pytest.raises(ValueError, match="WRITEABLE"):
        first.setflags(write=True)
    with pytest.raises(ValueError, match="WRITEABLE"):
        first.base.setflags(write=True)
    # ndarray metadata is mutable even when its bytes are not. Returning fresh
    # views prevents one caller's reshape from changing another caller's result.
    with warnings.catch_warnings():
        warnings.simplefilter("ignore", DeprecationWarning)
        first.shape = (first.size,)
    second = renderer.cached_fallback_sdf_field(mask, 2, 160)
    assert second.shape == (mask.height, mask.width)
    assert np.array_equal(second, expected)
    assert not second.flags.writeable


def test_sdf_cache_uses_existing_clear_and_stats(glyph_pool, mask, monkeypatch):
    calls = counted_transform(monkeypatch)
    renderer.cached_fallback_sdf_field(mask, 1, 160)
    assert cache.get_custom_profile_cache_stats()["glyph_sdf"]["entries"] == 1
    cache.clear_custom_profile_caches()
    assert glyph_pool.stats()["entries"] == 0
    assert glyph_pool.stats()["bytes"] == 0
    renderer.cached_fallback_sdf_field(mask, 1, 160)
    assert len(calls) == 2


@pytest.mark.parametrize("limit", ["max_entries", "max_bytes"])
def test_disabled_sdf_cache_does_not_hash_or_store(glyph_pool, mask, monkeypatch, limit):
    setattr(glyph_pool, limit, 0)
    calls = counted_transform(monkeypatch)

    def unexpected(*args):
        pytest.fail("disabled cache hashed a mask")

    monkeypatch.setattr(renderer.hashlib, "sha256", unexpected)
    for _ in range(2):
        renderer.cached_fallback_sdf_field(mask, 1, 160)
    assert len(calls) == 2
    assert glyph_pool.stats()["entries"] == 0


def test_oversized_sdf_is_not_copied_or_allowed_to_evict_pool(glyph_pool, mask, monkeypatch):
    glyph_pool.max_bytes = mask.width * mask.height * 4 + cache.FLOAT_SDF_CACHE_ENTRY_OVERHEAD
    renderer.cached_fallback_sdf_field(mask, 1, 160)
    big = GrayField(5, 3, bytes(15))

    def unexpected(*args):
        pytest.fail("oversized result was copied to immutable cache transport")

    monkeypatch.setattr(FloatField, "from_array", unexpected)
    result = renderer.cached_fallback_sdf_field(big, 1, 160)
    assert result.shape == (3, 5)
    assert glyph_pool.stats()["entries"] == 1
    assert glyph_pool.stats()["evictions"] == 0


def test_float_and_dynamic_glyphs_share_one_byte_budget(glyph_pool, mask):
    @dataclass
    class DynamicGlyph:
        field: GrayField

    old = DynamicGlyph(GrayField(2, 2, bytes(4)))
    field_bytes = mask.width * mask.height * 4 + cache.FLOAT_SDF_CACHE_ENTRY_OVERHEAD
    glyph_pool.max_bytes = field_bytes
    glyph_pool.set("dynamic-glyph", old)
    assert glyph_pool.stats()["bytes"] == 4 + 256
    renderer.cached_fallback_sdf_field(mask, 1, 160)
    assert glyph_pool.stats()["bytes"] == field_bytes
    assert glyph_pool.stats()["entries"] == 1
    assert glyph_pool.stats()["evictions"] == 1
    assert glyph_pool.get("dynamic-glyph") is cache.MISSING


def test_sdf_entry_bound_evicts_old_field(glyph_pool, mask, monkeypatch):
    glyph_pool.max_entries = 1
    calls = counted_transform(monkeypatch)
    renderer.cached_fallback_sdf_field(mask, 1, 160)
    renderer.cached_fallback_sdf_field(mask, 2, 160)
    renderer.cached_fallback_sdf_field(mask, 1, 160)
    assert len(calls) == 3
    assert glyph_pool.stats()["entries"] == 1
    assert glyph_pool.stats()["evictions"] == 2


def test_concurrent_misses_publish_immutable_complete_fields(glyph_pool, mask, monkeypatch):
    original = renderer.alpha_mask_to_sdf_field
    barrier = threading.Barrier(4)

    def simultaneous(mask, spread, threshold):
        barrier.wait(timeout=10)
        return original(mask, spread, threshold)

    monkeypatch.setattr(renderer, "alpha_mask_to_sdf_field", simultaneous)
    with ThreadPoolExecutor(max_workers=4) as pool:
        fields = list(pool.map(lambda _: renderer.cached_fallback_sdf_field(mask, 1, 160), range(4)))
    assert glyph_pool.stats()["entries"] == 1
    for field in fields:
        assert field.tobytes() == fields[0].tobytes()
        with pytest.raises(ValueError, match="WRITEABLE"):
            field.setflags(write=True)


def test_sdf_exception_is_not_cached(glyph_pool, mask, monkeypatch):
    original = renderer.alpha_mask_to_sdf_field

    def fail(*args):
        raise RuntimeError("temporary transform failure")

    monkeypatch.setattr(renderer, "alpha_mask_to_sdf_field", fail)
    with pytest.raises(RuntimeError, match="temporary"):
        renderer.cached_fallback_sdf_field(mask, 1, 160)
    assert glyph_pool.stats()["entries"] == 0
    assert glyph_pool.stats()["sets"] == 0
    monkeypatch.setattr(renderer, "alpha_mask_to_sdf_field", original)
    renderer.cached_fallback_sdf_field(mask, 1, 160)
    assert glyph_pool.stats()["entries"] == 1
