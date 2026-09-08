from __future__ import annotations

import math

import pytest

from src.sekai.profile.custom_profile import limits as limits_module
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


@pytest.mark.parametrize(
    ("card", "message"),
    [
        ({}, "customProfileCard must be an object"),
        ({"customProfileCard": {"shapes": {}}}, "shapes must be an array"),
        ({"customProfileCard": {"shapes": [None]}}, r"shapes\[0\] must be an object"),
        ({"customProfileCard": {"shapes": [{}]}}, r"objectData must be an object"),
        (
            {"customProfileCard": {"shapes": [{"objectData": {"scale": [1]}}]}},
            "scale must be an object",
        ),
        (
            {"customProfileCard": {"shapes": [{"objectData": {"position": [1], "rotation": {}}}]}},
            "position must be an object",
        ),
    ],
)
def test_custom_profile_rejects_invalid_scene_shapes(card, message) -> None:
    with pytest.raises(ValueError, match=message):
        validate_custom_profile_card(card, **LIMITS)


def test_custom_profile_accepts_explicitly_empty_bucket() -> None:
    validate_custom_profile_card({"customProfileCard": {"shapes": None}}, **LIMITS)


def test_tmp_validation_ignores_parser_tokens_without_a_style(monkeypatch) -> None:
    monkeypatch.setattr(limits_module, "parse_tmp_text", lambda *_args: [object()])

    limits_module._validate_tmp_text_styles(
        "ignored",
        12,
        label="card.customProfileCard.texts[0]",
        max_scale=LIMITS["max_scale"],
        max_text_size=LIMITS["max_text_size"],
    )


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
        ("<mspace=1e308>hello</mspace>", r"richText\.mspace"),
        ("<line-height=1e308>hello</line-height>", r"richText\.line_height"),
        ("<voffset=1e308>hello</voffset>", r"richText\.voffset"),
    ],
)
def test_custom_profile_rejects_unbounded_effective_rich_text_style(text: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        validate_custom_profile_card(_text_card(text), **LIMITS)


def test_custom_profile_accepts_bounded_effective_rich_text_style() -> None:
    validate_custom_profile_card(_text_card("<size=300><scale=6>hello</scale></size>"), **LIMITS)


def test_custom_profile_rejects_text_length_and_line_spacing_limits() -> None:
    with pytest.raises(ValueError, match="characters"):
        validate_custom_profile_card(_text_card("hello"), **{**LIMITS, "max_text_length": 4})

    card = _text_card("hello")
    card["customProfileCard"]["texts"][0]["lineSpacing"] = 1e308
    with pytest.raises(ValueError, match="lineSpacing"):
        validate_custom_profile_card(card, **LIMITS)


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


def test_custom_profile_raster_budget_rejects_nonpositive_and_accepts_valid_size() -> None:
    with pytest.raises(ValueError, match="positive dimensions"):
        ensure_raster_size((0, 1), max_pixels=8 * 1024 * 1024, label="shape")

    assert ensure_raster_size((8, 6), max_pixels=48, label="shape") == (8, 6)
