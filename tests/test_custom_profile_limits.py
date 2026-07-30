from __future__ import annotations

import math

import pytest

from src.sekai.profile.custom_profile.limits import ensure_raster_size, validate_custom_profile_card

LIMITS = {
    "max_elements": 4,
    "max_scale": 8.0,
    "max_text_size": 1024.0,
    "max_text_length": 4096,
}


def _card(*, scale: object = 1.0) -> dict:
    return {
        "customProfileCard": {
            "shapes": [
                {
                    "id": 1,
                    "objectData": {
                        "visible": True,
                        "layer": 1,
                        "position": {"x": 0, "y": 0, "z": 0},
                        "scale": {"x": scale, "y": scale, "z": 1},
                        "rotation": {"x": 0, "y": 0, "z": 0, "w": 1},
                    },
                }
            ]
        }
    }


@pytest.mark.parametrize("scale", [math.nan, math.inf, -1.0, 0.0, 8.01, 1.0e308, "nan", "inf"])
def test_custom_profile_rejects_non_finite_or_unbounded_scale(scale: object) -> None:
    with pytest.raises(ValueError, match="scale"):
        validate_custom_profile_card(_card(scale=scale), **LIMITS)


@pytest.mark.parametrize(
    ("target", "key", "value"),
    [
        ("position", "x", math.nan),
        ("position", "y", math.inf),
        ("rotation", "z", "nan"),
        ("item", "alpha", math.inf),
        ("item", "outlineSize", "inf"),
    ],
)
def test_custom_profile_rejects_non_finite_layout_scalars(target: str, key: str, value: object) -> None:
    card = _card()
    item = card["customProfileCard"]["shapes"][0]
    if target == "item":
        item[key] = value
    else:
        item["objectData"][target][key] = value

    with pytest.raises(ValueError, match=key):
        validate_custom_profile_card(card, **LIMITS)


def test_custom_profile_rejects_too_many_elements() -> None:
    card = _card()
    card["customProfileCard"]["shapes"] *= 5

    with pytest.raises(ValueError, match="elements"):
        validate_custom_profile_card(card, **LIMITS)


def test_custom_profile_rejects_oversized_text_before_rendering() -> None:
    card = _card()
    card["customProfileCard"]["shapes"] = []
    card["customProfileCard"]["texts"] = [
        {
            "text": "hello",
            "size": 1025,
            "objectData": {
                "visible": True,
                "layer": 1,
                "position": {"x": 0, "y": 0},
                "scale": {"x": 1, "y": 1},
                "rotation": {"z": 0, "w": 1},
            },
        }
    ]

    with pytest.raises(ValueError, match=r"\.size"):
        validate_custom_profile_card(card, **LIMITS)


def test_custom_profile_raster_budget_rejects_allocation_before_it_happens() -> None:
    with pytest.raises(ValueError, match="16000000 pixels"):
        ensure_raster_size((4000, 4000), max_pixels=8 * 1024 * 1024, label="shape")
