"""Compare the native float32 loop against the retained NumPy field calculation."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from src.sekai.profile.custom_profile import source_font
from src.sekai.profile.custom_profile.renderer import PNGRenderer


def native():
    module = pytest.importorskip("haruki_skia_renderer")
    if not hasattr(module, "source_outline_sdf"):
        pytest.skip("source outline SDF helper not built")
    return module


@pytest.mark.parametrize("seed", range(16))
def test_native_field_matches_numpy_bytes(monkeypatch, seed):
    native()
    rng = np.random.default_rng(seed)
    contours = [rng.uniform(-25, 25, size=(seed + 3, 2)).astype(np.float32)]
    if seed % 2:
        # Opposite winding, coincident points and zero-length edges are all legal.
        contours.append(contours[0][::-1].copy())
        contours[0][1] = contours[0][0]
    box = (-12, -15, 13, 11)
    pad = seed % 4
    width, height = box[2] - box[0] + pad * 2, box[3] - box[1] + pad * 2
    asset = SimpleNamespace(gradient_scale=1.5 + seed / 3)
    from src.sekai.profile.custom_profile.renderer import TMP_DYNAMIC_SDF_VECTOR_SPREAD_BIAS

    denominator = 2.0 * max(1.0, asset.gradient_scale - TMP_DYNAMIC_SDF_VECTOR_SPREAD_BIAS)
    actual = source_font.native_outline_sdf(contours, width, height, (box[0] - pad, box[1] - pad), denominator)
    monkeypatch.setattr(source_font, "native_outline_sdf", lambda *_args: None)
    owner = SimpleNamespace(tmp_vector_glyph_contours=lambda *_args: (contours, np), max_layer_pixels=10000)
    expected = PNGRenderer.tmp_vector_glyph_sdf_field(owner, Path("unused"), "A", 30, box, pad, asset)
    assert actual == expected.pixels


@pytest.mark.parametrize(
    ("data", "ends", "width", "height", "origin", "denominator"),
    [
        (b"", [], 1, 1, (0, 0), 2),
        (b"\0" * 16, [1, 2], 1, 1, (0, 0), 2),
        (b"\0" * 16, [3], 1, 1, (0, 0), 2),
        (b"\0" * 16, [2], 0, 1, (0, 0), 2),
        (b"\0" * 16, [2], 16777217, 1, (0, 0), 2),
        (b"\0" * 16, [2], 1, 1, (float("nan"), 0), 2),
        (b"\0" * 16, [2], 1, 1, (0, 0), 0),
        (np.array([[float("nan"), 0], [1, 2]], dtype="<f4").tobytes(), [2], 1, 1, (0, 0), 2),
    ],
)
def test_native_field_rejects_invalid_inputs(data, ends, width, height, origin, denominator):
    with pytest.raises(ValueError, match=r"source|SDF"):
        native().source_outline_sdf(data, ends, width, height, origin, denominator)


def test_optional_helper_and_work_limit_keep_numpy_available(monkeypatch):
    import importlib

    monkeypatch.setattr(importlib, "import_module", lambda _: SimpleNamespace())
    contours = [np.zeros((2, 2), dtype=np.float32)]
    assert source_font.native_outline_sdf(contours, 2, 2, (0, 0), 2) is None
    assert source_font.native_outline_sdf(contours, 16777216, 16777216, (0, 0), 2) is None
