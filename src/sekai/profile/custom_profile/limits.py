"""Shape and numeric validity checks for custom-profile Unity scenes."""

from __future__ import annotations

import math
from typing import Any

from src.sekai.base.render_errors import RenderContentTooLargeError
from src.sekai.profile.custom_profile.svg import TextStyle, parse_tmp_text

CONTENT_BUCKETS = (
    "generals",
    "generalBackgrounds",
    "storyBackgrounds",
    "standMembers",
    "cardMembers",
    "honors",
    "bondsHonors",
    "collections",
    "others",
    "characterIcons",
    "materials",
    "userInterfaceIcons",
    "stamps",
    "shapes",
    "texts",
    "miniCharas",
    "screenFilters",
)

# These editor-only buckets require a live Unity DynamicAtlasStudio render target and uvRect.
# GetAnotherProfileResponse (the production input) does not expose them, Haruki-Cloud's typed
# ProfileCardData cannot forward them, and the compatibility renderer has always skipped them as
# unresolved. Reject a handcrafted request explicitly instead of claiming a successful image
# that silently omitted content or reviving Pillow for an input it cannot render either.
UNSUPPORTED_DYNAMIC_BUCKETS = frozenset({"miniCharas", "screenFilters"})

_TMP_LAYOUT_FIELDS = (
    "cspace",
    "mspace",
    "indent",
    "indent_percent",
    "line_indent",
    "line_indent_percent",
    "line_height",
    "voffset",
    "pos",
    "pos_percent",
)


class RasterSizeLimitError(RenderContentTooLargeError):
    """A named raster allocation exceeded the configured per-layer pixel budget."""

    def __init__(self, *, label: str, width: int, height: int, max_pixels: int) -> None:
        self.label = label
        self.width = width
        self.height = height
        self.pixels = width * height
        self.max_pixels = max_pixels
        super().__init__(f"{label} would allocate {width}x{height} ({self.pixels} pixels); limit is {max_pixels}")


def _finite_number(value: Any, label: str) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must be a finite number") from exc
    if not math.isfinite(number):
        raise ValueError(f"{label} must be a finite number")
    return number


def _validate_tmp_layout_fields(style: TextStyle, *, label: str) -> None:
    for key in _TMP_LAYOUT_FIELDS:
        value = getattr(style, key)
        if value is None:
            continue
        _finite_number(value, f"{label}.richText.{key}")


def _validate_tmp_style(
    style: TextStyle,
    *,
    label: str,
) -> None:
    size = _finite_number(style.size, f"{label}.richText.size")
    if size <= 0:
        raise ValueError(f"{label}.richText.size must be positive")

    _finite_number(style.scale_x, f"{label}.richText.scale")

    _finite_number(style.alpha, f"{label}.richText.alpha")
    _finite_number(style.rotate, f"{label}.richText.rotate")
    _validate_tmp_layout_fields(
        style,
        label=label,
    )


def _validate_tmp_text_styles(
    text: str,
    base_size: float,
    *,
    label: str,
) -> None:
    """Validate values after TMP rich-text tags have been applied.

    The request-level ``size`` and object scale are only defaults.  TMP ``<size>`` and
    ``<scale>`` tags replace them inside individual runs, so checking the outer fields alone
    does not establish the effective values. Both must be valid finite numbers.
    """

    base_style = TextStyle(
        color="#ffffff",
        alpha=1.0,
        size=base_size,
        scale_x=1.0,
        cspace=0.0,
        mspace=None,
        indent=0.0,
        line_indent=0.0,
        line_height=None,
        rotate=0.0,
        voffset=0.0,
        mark_color=None,
        bold=False,
        italic=False,
        underline=False,
        strike=False,
    )
    for token in parse_tmp_text(text, base_style):
        style = getattr(token, "style", None)
        if style is None:
            continue
        _validate_tmp_style(style, label=label)


def _content_bucket_items(layout: dict[str, Any], bucket: str) -> list:
    items = layout.get(bucket, [])
    if items is None:
        return []
    if not isinstance(items, list):
        raise ValueError(f"card.customProfileCard.{bucket} must be an array")
    if bucket in UNSUPPORTED_DYNAMIC_BUCKETS and items:
        raise ValueError(
            f"card.customProfileCard.{bucket} is unsupported without Unity DynamicAtlasStudio texture data"
        )
    return items


def _validate_object_transform(object_data: object, *, label: str) -> None:
    if not isinstance(object_data, dict):
        raise ValueError(f"{label}.objectData must be an object")

    scale = object_data.get("scale") or {}
    if not isinstance(scale, dict):
        raise ValueError(f"{label}.objectData.scale must be an object")
    sx = _finite_number(scale.get("x", 1.0), f"{label}.objectData.scale.x")
    sy = _finite_number(scale.get("y", sx), f"{label}.objectData.scale.y")
    if sx <= 0 or sy <= 0:
        raise ValueError(f"{label}.objectData.scale must be positive on both axes")

    for group in ("position", "rotation"):
        values = object_data.get(group) or {}
        if not isinstance(values, dict):
            raise ValueError(f"{label}.objectData.{group} must be an object")
        for axis, value in values.items():
            _finite_number(value, f"{label}.objectData.{group}.{axis}")


def _validate_text_content(
    item: dict[str, Any],
    *,
    label: str,
) -> None:
    text = str(item.get("text", "") or "")
    size = _finite_number(item.get("size", 1.0), f"{label}.size")
    if size <= 0:
        raise ValueError(f"{label}.size must be positive")
    _validate_tmp_text_styles(text, size, label=label)


def _validate_item_scalars(
    item: dict[str, Any],
    *,
    bucket: str,
    label: str,
) -> None:
    for numeric_key in ("alpha", "outlineAlpha", "outlineSize", "lineSpacing"):
        if numeric_key not in item or item[numeric_key] is None:
            continue
        _finite_number(item[numeric_key], f"{label}.{numeric_key}")


def _validate_content_item(
    item: object,
    *,
    bucket: str,
    label: str,
) -> None:
    if not isinstance(item, dict):
        raise ValueError(f"{label} must be an object")
    _validate_object_transform(item.get("objectData"), label=label)
    if bucket == "texts":
        _validate_text_content(
            item,
            label=label,
        )
    _validate_item_scalars(
        item,
        bucket=bucket,
        label=label,
    )


def validate_custom_profile_card(card: dict[str, Any]) -> None:
    """Check structure and finite numeric values without content-size quotas."""

    layout = card.get("customProfileCard")
    if not isinstance(layout, dict):
        raise ValueError("card.customProfileCard must be an object")

    for bucket in CONTENT_BUCKETS:
        items = _content_bucket_items(layout, bucket)

        for index, item in enumerate(items):
            label = f"card.customProfileCard.{bucket}[{index}]"
            _validate_content_item(
                item,
                bucket=bucket,
                label=label,
            )


def ensure_raster_size(size: tuple[int, int], *, max_pixels: int, label: str) -> tuple[int, int]:
    """Validate a raster allocation and return normalized integer dimensions."""

    try:
        width, height = int(size[0]), int(size[1])
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError(f"{label} must have finite integer dimensions") from exc
    if width <= 0 or height <= 0:
        raise ValueError(f"{label} must have positive dimensions, got {width}x{height}")
    pixels = width * height
    if pixels > max_pixels:
        raise RasterSizeLimitError(label=label, width=width, height=height, max_pixels=max_pixels)
    return width, height
