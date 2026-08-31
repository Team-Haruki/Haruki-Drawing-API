from __future__ import annotations

from PIL import Image, ImageChops
import pytest

from src.sekai.base.painter import LinearGradient, Painter


@pytest.mark.parametrize(
    ("fill", "blur", "radius", "edge_strength"),
    [
        ((255, 255, 255), 0, 10, 0),
        ((255, 255, 255, 255), 1, 10, None),
        ((255, 255, 255, 80), 1, 10, 0.6),
        ((255, 255, 255, 80), 6, 10, 0.6),
        (LinearGradient((255, 255, 255, 80), (80, 160, 255, 120), (0, 0), (1, 1)), 4, 10, 0.6),
        ((255, 255, 255, 80), 4, 0, 0.6),
    ],
)
def test_blurglass_roundrect_background_and_edge_modes(fill, blur, radius, edge_strength) -> None:
    background = Image.new("RGBA", (100, 72), (20, 80, 140, 255))
    painter = Painter(background.copy())

    painter._impl_blurglass_roundrect(
        (18, 14),
        (64, 44),
        fill,
        radius,
        blur=blur,
        shaodow_width=6,
        shadow_alpha=0.3,
        corners=(True, False, True, False),
        edge_strength=edge_strength,
    )

    assert ImageChops.difference(painter.img, background).convert("RGB").getbbox() is not None


def test_blurglass_roundrect_ignores_non_positive_sizes() -> None:
    background = Image.new("RGBA", (40, 40), (20, 80, 140, 255))
    painter = Painter(background.copy())

    result = painter._impl_blurglass_roundrect((10, 10), (0, 20), (255, 255, 255, 80), 8)

    assert result is painter
    assert ImageChops.difference(painter.img, background).convert("RGB").getbbox() is None
