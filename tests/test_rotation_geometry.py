"""Alpha extent rotation must preserve the old expanded-image crop exactly."""

import numpy as np
from PIL import Image
import pytest

from src.sekai.profile.custom_profile.gray_field import GrayField
from src.sekai.profile.custom_profile.rotation_geometry import expanded_rotation


@pytest.mark.parametrize("size", [(1, 1), (3, 2), (20, 27), (31, 40)])
@pytest.mark.parametrize("angle", [0, 20, -37, 90, 180, 270, 360, 89.999, 90.001, -180, 720.05])
def test_rotation_alpha_matches_pillow_exactly(size, angle):
    native = pytest.importorskip("haruki_skia_renderer")
    if getattr(native, "GRAY_FIELD_CAPABILITY", 0) < 2:
        pytest.skip("native gray affine required")
    pixels = np.random.default_rng(73).integers(0, 256, (size[1], size[0]), dtype=np.uint8).tobytes()
    legacy = Image.frombytes("L", size, pixels).rotate(angle, Image.Resampling.BICUBIC, expand=True)
    plan = expanded_rotation(size, angle)
    assert plan.size == legacy.size
    actual = GrayField(*size, pixels).transform_bicubic(plan.size, plan.inverse)
    assert actual.pixels == legacy.tobytes()


@pytest.mark.parametrize("angle", [float("nan"), float("inf"), float("-inf")])
def test_rotation_rejects_nonfinite_angles(angle):
    with pytest.raises(ValueError, match="finite"):
        expanded_rotation((2, 3), angle)


def test_expansion_checks_output_capacity_before_allocating():
    # Source fits under 16 Mi pixels, but its 45-degree bounding square does not.
    with pytest.raises(ValueError, match="limits"):
        expanded_rotation((32760, 1), 45)
