from __future__ import annotations

import math

import pytest

from src.sekai.profile.custom_profile import limits as limits_module
from src.sekai.profile.custom_profile.limits import ensure_raster_size, validate_custom_profile_card


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
    [math.nan, math.inf, -1.0, 0.0, 10**400, "nan", "inf"],
)
def test_custom_profile_rejects_invalid_scale(scale: object) -> None:
    with pytest.raises(ValueError, match="scale"):
        validate_custom_profile_card(_card(scale=scale))


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
        validate_custom_profile_card(card)


def test_custom_profile_accepts_many_elements() -> None:
    card = _card()
    card["customProfileCard"]["shapes"] *= 1000

    validate_custom_profile_card(card)


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
        validate_custom_profile_card(card)


def test_custom_profile_accepts_explicitly_empty_bucket() -> None:
    validate_custom_profile_card({"customProfileCard": {"shapes": None}})


def test_tmp_validation_ignores_parser_tokens_without_a_style(monkeypatch) -> None:
    monkeypatch.setattr(limits_module, "parse_tmp_text", lambda *_args: [object()])

    limits_module._validate_tmp_text_styles(
        "ignored",
        12,
        label="card.customProfileCard.texts[0]",
    )


@pytest.mark.parametrize("bucket", ["characterIcons", "materials", "userInterfaceIcons"])
def test_custom_profile_v67_image_buckets_accept_many_elements(bucket: str) -> None:
    card = _card()
    card["customProfileCard"][bucket] = card["customProfileCard"]["shapes"] * 1000

    validate_custom_profile_card(card)


def test_custom_profile_accepts_large_editor_font_size() -> None:
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

    validate_custom_profile_card(card)


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
        ("<size=nan>hello</size>", r"richText\.size"),
        ("<size=0>hello</size>", r"richText\.size"),
        ("<scale=inf>hello</scale>", r"richText\.scale"),
        ("<rotate=inf>hello</rotate>", r"richText\.rotate"),
        ("<cspace=inf>hello</cspace>", r"richText\.cspace"),
        ("<mspace=inf>hello</mspace>", r"richText\.mspace"),
        ("<line-height=inf>hello</line-height>", r"richText\.line_height"),
        ("<voffset=inf>hello</voffset>", r"richText\.voffset"),
    ],
)
def test_custom_profile_rejects_unbounded_effective_rich_text_style(text: str, match: str) -> None:
    with pytest.raises(ValueError, match=match):
        validate_custom_profile_card(_text_card(text))


def test_custom_profile_accepts_bounded_effective_rich_text_style() -> None:
    validate_custom_profile_card(_text_card("<size=300><scale=6>hello</scale></size>"))


def test_custom_profile_accepts_long_text_and_large_layout_offsets() -> None:
    card = _text_card("hello" * 1000 + "<cspace=1e12><mspace=1e12><line-height=1e12><voffset=1e12>x")
    card["customProfileCard"]["texts"][0]["lineSpacing"] = 1e12
    validate_custom_profile_card(card)


@pytest.mark.parametrize("scale", [8.01, 1000, 1e100])
def test_custom_profile_accepts_large_editor_scale(scale):
    validate_custom_profile_card(_card(scale=scale))


@pytest.mark.parametrize("bucket", ["miniCharas", "screenFilters"])
def test_custom_profile_rejects_unrenderable_dynamic_atlas_buckets(bucket: str) -> None:
    card = _card()
    card["customProfileCard"][bucket] = card["customProfileCard"]["shapes"]

    with pytest.raises(ValueError, match=r"DynamicAtlasStudio"):
        validate_custom_profile_card(card)


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


@pytest.mark.parametrize(
    "text",
    [
        "<size=4097>hello</size>",
        "<size=100000>hello</size>",
        "<scale=64.01>hello</scale>",
        "<scale=-1000>hello</scale>",
        "<size=10000><scale=1000>hello</scale></size>",
    ],
)
def test_custom_profile_accepts_large_finite_rich_text(text: str) -> None:
    validate_custom_profile_card(_text_card(text))


def test_raster_limit_exposes_structured_render_reason() -> None:
    from src.sekai.base.render_errors import CONTENT_TOO_LARGE, render_error_code

    with pytest.raises(limits_module.RasterSizeLimitError) as error:
        ensure_raster_size((4000, 4000), max_pixels=8 * 1024 * 1024, label="glyph")
    assert render_error_code(error.value) == CONTENT_TOO_LARGE
