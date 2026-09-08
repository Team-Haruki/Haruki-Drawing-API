"""Emoji run widths for shared layout; no raster or Pillow imports.

Keep Pilmoji's layout contract: each recognized emoji occupies one font-size
square, each text run truncates its advance to an integer, and lines add four
pixels of spacing. Pixel rendering remains the selected backend's responsibility.
"""

from functools import lru_cache
import re

import emoji


@lru_cache(maxsize=1)
def _emoji_pattern() -> re.Pattern:
    # Match the renderer's fully-qualified vocabulary and longest-first runs.
    # emoji.emoji_list also recognizes unqualified forms that Pilmoji treats as
    # text, so it cannot replace this compatibility grammar.
    names = {
        data["en"]: symbol
        for symbol, data in emoji.EMOJI_DATA.items()
        if "en" in data and data["status"] <= emoji.STATUS["fully_qualified"]
    }
    symbols = "|".join(re.escape(value) for value in sorted(names.values(), key=len, reverse=True))
    return re.compile(f"({symbols}|<a?:[a-zA-Z0-9_]{{1,32}}:[0-9]{{17,22}}>)")


def emoji_text_size(font, text: str, *, spacing: int = 4) -> tuple[int, int]:
    lines = text.splitlines()
    pattern = _emoji_pattern()
    width = 0
    for line in lines:
        line_width = sum(
            int(font.size) if index % 2 else int(font.getlength(run))
            for index, run in enumerate(pattern.split(line))
            if run
        )
        width = max(width, line_width)
    return width, int(len(lines) * (spacing + font.size) - spacing)
