"""BASIC glyph coverage as immutable A8 data, with an explicit legacy boundary."""

from importlib import import_module
from pathlib import Path

from .gray_field import MAX_FIELD_PIXELS, GrayField, field_size


def basic_text_field(
    path: Path, text: str, size: float, *, max_pixels: int = MAX_FIELD_PIXELS
) -> tuple[GrayField, tuple[int, int, int, int]]:
    if not 0 < max_pixels <= MAX_FIELD_PIXELS:
        raise ValueError("text mask pixel limit must be 1..=16777216")
    size = max(1, round(size))
    try:
        native = import_module("haruki_skia_renderer")
        if getattr(native, "TEXT_MASK_CAPABILITY", 0) < 1:
            raise ImportError("native BASIC text masks unavailable")
        result = native.basic_text_mask("", str(path), text, size, max_pixels)
    except (ImportError, AttributeError, RuntimeError):
        from .pillow_fields import basic_text_field as legacy_text_field

        return legacy_text_field(path, text, size, max_pixels=max_pixels)
    width, height = result["size"]
    bbox = tuple(int(value) for value in result["bbox"])
    if not width or not height:
        # Empty ink is a legal FreeType result. GrayField has positive dimensions;
        # preserve the separate layout bbox, including non-zero whitespace advance.
        return GrayField(1, 1, b"\0"), bbox
    field_size((width, height))
    return GrayField(width, height, result["pixels"]), bbox
