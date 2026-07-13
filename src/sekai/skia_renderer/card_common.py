"""Shared card helpers.

What is left here after the Card List scene builder was retired: the whole hand-written IR
builder (and the layout constants it needed) is gone — card/list now draws the same plot.py
widget tree both backends share, exactly as card/box already did.
"""

from __future__ import annotations

import re


def rare_count(rare: str) -> int:
    """Number of rarity stars to draw. Accepts "rarity_4", "4_star", "4"; birthday is a single icon."""
    if rare == "rarity_birthday":
        return 1
    match = re.search(r"(\d+)", rare or "")
    return int(match.group(1)) if match else 0
