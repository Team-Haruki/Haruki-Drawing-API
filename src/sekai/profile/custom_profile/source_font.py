"""Request-local FontTools readers for exact source glyph geometry."""

from io import BytesIO
from itertools import pairwise
from pathlib import Path
import struct
from typing import Any


def open_source_font(path: Path) -> Any:
    from fontTools.ttLib import TTFont

    # Keep lazy table decoding without holding a live OS descriptor through CFF
    # object cycles. The old eager TTFont reader also retained the file bytes.
    font = TTFont(BytesIO(path.read_bytes()), lazy=True)
    # Glyph names are only lookup labels here. Index-based labels avoid synthesizing
    # thousands of Unicode names from every cmap when a font has no PostScript names.
    # Keep named CFF fonts intact: Type 2 seac components use StandardEncoding names.
    if any(tag in font for tag in ("VARC", "CFF2", "fvar")):
        return font
    cff = font["CFF "].cff.topDictIndex[0] if "CFF " in font else None
    if cff is not None and not hasattr(cff, "ROS"):
        return font
    if cff is not None or "glyf" in font:
        order = list(map(str, range(max(1, font["maxp"].numGlyphs))))
        order[0] = ".notdef"
        font.setGlyphOrder(order)
        if cff is not None:
            cff.charset = order
    return font


def outline_glyph_set(font: Any) -> Any:
    # CFF glyphSet.draw delegates straight to these same charstrings, with no
    # bearing translation. Its eager hmtx/vmtx dictionaries are unused for outlines.
    # TrueType must retain getGlyphSet's lsb - xMin correction and component rules.
    if "CFF " in font and "CFF2" not in font and "VARC" not in font:
        return font["CFF "].cff.topDictIndex[0].CharStrings
    if "glyf" in font and not any(tag in font for tag in ("CFF2", "VARC", "fvar")):
        _prepare_glyf_tables(font)
    return font.getGlyphSet()


class _GlyphValues(dict):
    """FontTools still expands/draws each glyph, but only on first access."""

    def __init__(self, font: Any, data: bytes, offsets: Any):
        super().__init__(dict.fromkeys(font.getGlyphOrder()))
        self.glyph_ids = font.getReverseGlyphMap()
        self.data = data
        self.offsets = offsets

    def __getitem__(self, name: str) -> Any:
        from fontTools.ttLib.tables._g_l_y_f import Glyph

        value = super().__getitem__(name)
        if value is None:
            index = self.glyph_ids[name]
            start, end = self.offsets[index : index + 2]
            value = Glyph(self.data[start:end])
            self[name] = value
        return value


class _MetricValues(dict):
    def __init__(self, font: Any, tag: str, count: int):
        super().__init__(dict.fromkeys(font.getGlyphOrder()))
        self.glyph_ids = font.getReverseGlyphMap()
        self.data = font.reader[tag]
        self.count = min(count, len(self))
        if self.count <= 0 or len(self.data) < 4 * self.count + 2 * (len(self) - self.count):
            raise ValueError(f"Invalid {tag} metric table")

    def __getitem__(self, name: str) -> tuple[int, int]:
        value = super().__getitem__(name)
        if value is None:
            index = self.glyph_ids[name]
            if index < self.count:
                value = struct.unpack_from(">Hh", self.data, index * 4)
            else:
                advance = struct.unpack_from(">H", self.data, (self.count - 1) * 4)[0]
                bearing = struct.unpack_from(">h", self.data, 4 * self.count + 2 * (index - self.count))[0]
                value = (advance, bearing)
            self[name] = value
        return value


def _prepare_glyf_tables(font: Any) -> None:
    from fontTools.ttLib import newTable

    if not font.isLoaded("glyf"):
        data = font.reader["glyf"]
        offsets = font["loca"].locations
        if len(offsets) != font["maxp"].numGlyphs + 1 or any(
            start > end or end > len(data) for start, end in pairwise(offsets)
        ):
            raise ValueError("Invalid glyf location table")
        table = newTable("glyf")
        table.glyphOrder = font.getGlyphOrder()
        table.glyphs = _GlyphValues(font, data, offsets)
        font["glyf"] = table
    for tag, header, field in (("hmtx", "hhea", "numberOfHMetrics"), ("vmtx", "vhea", "numberOfVMetrics")):
        if tag in font and not font.isLoaded(tag):
            count = getattr(font[header], field) if header in font else font["maxp"].numGlyphs
            table = newTable(tag)
            table.metrics = _MetricValues(font, tag, count)
            font[tag] = table


def native_outline_sdf(contours, width, height, origin, denominator):
    from importlib import import_module

    import numpy as np

    count = sum(len(contour) for contour in contours)
    if count > 262144 or width * height * count > 500000000:
        return None
    if not all(abs(value) <= 1.0e9 for value in origin) or not 1 <= denominator <= 3.4e38:
        return None
    if any(not np.isfinite(contour).all() or np.abs(contour).max(initial=0) > 1.0e9 for contour in contours):
        return None
    try:
        render = import_module("haruki_skia_renderer").source_outline_sdf
    except (ImportError, AttributeError):
        return None
    data = b"".join(contour.astype("<f4", copy=False).tobytes() for contour in contours)
    ends = []
    total = 0
    for contour in contours:
        total += len(contour)
        ends.append(total)
    return render(data, ends, width, height, origin, denominator)
