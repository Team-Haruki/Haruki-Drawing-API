"""Shared hinted font geometry for vector text, with a legacy-only fallback."""

from dataclasses import dataclass
from importlib import import_module

from src.settings import FONT_DIR


@dataclass(frozen=True, slots=True)
class VectorGlyphRun:
    commands: tuple
    advance: float
    ascent: float
    descent: float


def glyph_run(font: str, size: float, text: str) -> VectorGlyphRun:
    try:
        native = import_module("haruki_skia_renderer")
        result = native.vector_text_geometry(str(FONT_DIR), font, text, size)
    except (ImportError, AttributeError, OSError, RuntimeError, ValueError):
        from src.core.pillow_telemetry import PILLOW_TOUCH_TEXT_METRIC, record_pillow_touch

        from .pillow_vector import text_geometry

        record_pillow_touch(PILLOW_TOUCH_TEXT_METRIC)
        result = text_geometry(font, size, text)
    return VectorGlyphRun(
        tuple((op, tuple(points)) for op, points in result["commands"]),
        result["advance"],
        result["ascent"],
        result["descent"],
    )
