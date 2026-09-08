"""Shared TMP text-layer geometry and sampled glyph descriptions."""

from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any

from .float_field import FloatField
from .gray_field import GrayField


@dataclass(frozen=True)
class TMPTextBoxLayout:
    size: tuple[int, int]
    pivot: tuple[float, float]
    mesh: Any
    baselines: list[float] | None
    horizontal_align: str
    box_width: float
    rect_origin: tuple[float, float]
    content_y: float
    outline_color: str
    outline_width: int
    outline_dilate: float


@dataclass(frozen=True)
class TMPLocalSdfGlyph:
    field: GrayField | FloatField
    left: int
    top: int
    scalars: Any
    rotation: float = 0.0
    rotated_size: tuple[int, int] | None = None


@dataclass(frozen=True)
class TMPSdfTextLayer:
    layout: TMPTextBoxLayout
    glyphs: tuple[TMPLocalSdfGlyph, ...]
    crop: tuple[int, int, int, int]


@dataclass(frozen=True)
class TMPFallbackMetrics:
    """BASIC metrics consulted only when TMP tables/source glyphs cannot answer.

    The supported free-threaded baseline uses Pillow BASIC. Preserve its hinted
    missing-glyph advance and ink bounds, even for invisible characters. A stale
    extension still uses the explicit legacy adapter. This is measurement only;
    the object is never passed to an image drawing API.
    """

    path: Path
    size: float

    def _font(self):
        from src.sekai.base.font_metrics import get_native_font

        native = get_native_font("", str(self.path), self.size)
        return native if native is not None else self._legacy_font()

    def _legacy_font(self):
        from src.core.pillow_telemetry import PILLOW_TOUCH_TEXT_METRIC, record_pillow_touch

        from .cache import get_render_font

        record_pillow_touch(PILLOW_TOUCH_TEXT_METRIC)
        return get_render_font(self.path, self.size)

    def getbbox(self, text: str):
        return self._font().getbbox(text)

    def getlength(self, text: str):
        return self._font().getlength(text)

    def getmetrics(self):
        return self._font().getmetrics()


def text_layer_crop(size, pivot, bbox, pad=4) -> tuple[int, int, int, int]:
    """Legacy alpha trim retains the pivot and four pixels of surrounding padding."""
    if bbox is None:
        return 0, 0, size[0], size[1]
    left, top, right, bottom = bbox
    return (
        max(0, math.floor(min(left, pivot[0])) - pad),
        max(0, math.floor(min(top, pivot[1])) - pad),
        min(size[0], math.ceil(max(right, pivot[0])) + pad),
        min(size[1], math.ceil(max(bottom, pivot[1])) + pad),
    )
