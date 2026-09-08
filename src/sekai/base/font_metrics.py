"""Native BASIC font metrics for layout, independent of Pillow font objects."""

from dataclasses import dataclass
from importlib import import_module
from pathlib import Path
from stat import S_ISREG


def _native():
    try:
        native = import_module("haruki_skia_renderer")
    except ImportError:
        return None
    return native if getattr(native, "TEXT_METRICS_CAPABILITY", 0) >= 2 else None


@dataclass(frozen=True, slots=True)
class NativeFontMetrics:
    path: str
    size: float
    signature: tuple[int, int]
    # This value is Pillow's BASIC enum value, exposed only for compatibility
    # with callers that also accept a caller-owned legacy font.
    layout_engine: int = 0

    def _measure(self, text: str):
        native = _native()
        if native is not None:
            try:
                return native.measure_text_batch("", self.path, [(text, self.size)], engine="freetype_basic")[0]
            except (AttributeError, OSError, RuntimeError, ValueError):
                pass
        # Layout runs before render_canvas_payload's fail-open boundary. Keep a
        # broken extension from breaking the legacy renderer's layout as well.
        from PIL import ImageFont

        from src.core.pillow_telemetry import PILLOW_TOUCH_TEXT_METRIC, record_pillow_touch

        from .painter import get_font

        record_pillow_touch(PILLOW_TOUCH_TEXT_METRIC)
        font = get_font(self.path, self.size)
        if font.layout_engine != ImageFont.Layout.BASIC:
            font = ImageFont.truetype(
                font.path, font.size, index=font.index, encoding=font.encoding, layout_engine=ImageFont.Layout.BASIC
            )
        ascent, descent = font.getmetrics()
        return {
            "pillow_bbox": font.getbbox(text),
            "advance": font.getlength(text),
            "ascent": ascent,
            "descent": descent,
        }

    def getbbox(self, text: str):
        return tuple(int(value) for value in self._measure(text)["pillow_bbox"])

    def getlength(self, text: str):
        return self._measure(text)["advance"]

    def getmetrics(self):
        measured = self._measure("")
        return int(measured["ascent"]), int(measured["descent"])


def get_native_font(font_dir: str, name: str, size: float) -> NativeFontMetrics | None:
    if _native() is None:
        return None
    # Match IRBuilder's established suffix search order, including arbitrary
    # absolute font paths. A missing font is not negatively cached.
    for suffix in (".otf", ".ttf", ".ttc", ""):
        path = Path(font_dir) / f"{name}{suffix}"
        try:
            stat = path.stat()
        except OSError:
            continue
        if S_ISREG(stat.st_mode):
            return NativeFontMetrics(str(path), max(1, round(float(size))), (stat.st_mtime_ns, stat.st_size))
    return None


def get_layout_font(path: str, size: int):
    """Shared BASIC layout metrics; Pillow is used only when native metrics are absent."""
    from src.settings import FONT_DIR

    font = get_native_font(str(FONT_DIR), path, size)
    if font is not None:
        return font
    from .painter import get_font

    return get_font(path, size)
