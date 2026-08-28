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


@pytest.mark.parametrize(
    "scale",
    [math.nan, math.inf, -1.0, 0.0, 8.01, 1.0e308, 10**400, "nan", "inf"],
)
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


@pytest.mark.parametrize("bucket", ["characterIcons", "materials", "userInterfaceIcons"])
def test_custom_profile_v67_image_buckets_count_toward_element_limit(bucket: str) -> None:
    card = _card()
    card["customProfileCard"][bucket] = card["customProfileCard"]["shapes"] * 4

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


def _text_card(text: str, *, size: float = 96.0) -> dict:
    card = _card()
    card["customProfileCard"]["shapes"] = []
    card["customProfileCard"]["texts"] = [
        {
            "text": text,
            "size": size,
            "objectData": {
                "visible": True,
                "layer": 1,
                "position": {"x": 0, "y": 0},
                "scale": {"x": 1, "y": 1},
                "rotation": {"z": 0, "w": 1},
            },
        }
    ]
    return card


@pytest.mark.parametrize(
    ("text", "match"),
    [
        ("<size=4097>hello</size>", r"richText\.size"),
        ("<scale=64.01>hello</scale>", r"richText\.scale"),
        ("<size=nan>hello</size>", r"richText\.size"),
        ("<rotate=inf>hello</rotate>", r"richText\.rotate"),
        ("<cspace=1e308>hello</cspace>", r"richText\.cspace"),
    ],
)
def test_custom_profile_rejects_unbounded_effective_rich_text_style(text: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        validate_custom_profile_card(_text_card(text), **LIMITS)


def test_custom_profile_accepts_bounded_effective_rich_text_style() -> None:
    validate_custom_profile_card(_text_card("<size=300><scale=6>hello</scale></size>"), **LIMITS)


@pytest.mark.parametrize("bucket", ["miniCharas", "screenFilters"])
def test_custom_profile_rejects_unrenderable_dynamic_atlas_buckets(bucket: str) -> None:
    card = _card()
    card["customProfileCard"][bucket] = card["customProfileCard"]["shapes"]

    with pytest.raises(ValueError, match=r"DynamicAtlasStudio"):
        validate_custom_profile_card(card, **LIMITS)


def test_custom_profile_raster_budget_rejects_allocation_before_it_happens() -> None:
    with pytest.raises(ValueError, match="16000000 pixels"):
        ensure_raster_size((4000, 4000), max_pixels=8 * 1024 * 1024, label="shape")


def test_custom_profile_raster_budget_normalizes_nonfinite_dimension_error() -> None:
    with pytest.raises(ValueError, match="finite integer dimensions"):
        ensure_raster_size((math.inf, 1), max_pixels=8 * 1024 * 1024, label="shape")
