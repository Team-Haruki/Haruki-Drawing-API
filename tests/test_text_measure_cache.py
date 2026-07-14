"""The text-measurement cache.

``Font.getbbox`` was 84% of the whole draw pass — 6816 calls on a single inventory list — because
every widget measures its text to lay itself out, and the widget tree measures repeatedly while
sizing. Caching it took the Skia sweep from 9.79s to 5.75s.

Measurement feeds LAYOUT, so a wrong cache is not a slow render but a scrambled one: every string
placed at the wrong width, silently. These tests pin the cache key. They are gated on ``real_fonts``
because without the fonts Pillow degrades to a 10px bitmap face and the metrics being measured are
not the ones that ship.
"""

from __future__ import annotations

import pytest

from src.sekai.base import painter


@pytest.fixture(autouse=True)
def clear_measure_caches():
    painter._text_bbox_cache.clear()
    painter._text_emoji_size_cache.clear()
    yield
    painter._text_bbox_cache.clear()
    painter._text_emoji_size_cache.clear()


def test_the_cache_key_separates_font_sizes(real_fonts):
    """The key must carry the SIZE. Without it the first measurement of a string would be reused at
    every other size — every heading laid out to body-text width, on every image."""
    small = painter.get_text_size(painter.get_font(painter.DEFAULT_FONT, 12), "Haruki")
    large = painter.get_text_size(painter.get_font(painter.DEFAULT_FONT, 48), "Haruki")

    assert large[0] > small[0] * 2, f"48px text measured {large} vs 12px {small}"


def test_the_cache_key_separates_fonts(real_fonts):
    """Same string, same size, different FACE — bold is wider than regular, and a key that dropped
    the face would hand back whichever was measured first."""
    regular = painter.get_text_size(painter.get_font(painter.DEFAULT_FONT, 32), "Haruki Drawing")
    bold = painter.get_text_size(painter.get_font(painter.DEFAULT_BOLD_FONT, 32), "Haruki Drawing")

    assert regular != bold


def test_cached_measurements_match_an_uncached_probe(real_fonts):
    """The cache must return what Pillow would have. Measure cold, then warm, then compare both
    against the raw getbbox the cache is standing in front of."""
    font = painter.get_font(painter.DEFAULT_FONT, 24)
    text = "プロセカ Haruki 123"

    cold_size = painter.get_text_size(font, text)
    cold_offset = painter.get_text_offset(font, text)
    assert painter._text_bbox_cache, "nothing was cached — the cache is not on the measuring path"

    warm_size = painter.get_text_size(font, text)
    warm_offset = painter.get_text_offset(font, text)

    bbox = font.getbbox(text)
    assert cold_size == warm_size == (bbox[2] - bbox[0], bbox[3] - bbox[1])
    assert cold_offset == warm_offset == (bbox[0], bbox[1])


def test_emoji_text_is_measured_by_the_emoji_path_and_cached_separately(real_fonts):
    """Emoji strings go through ``getsize_emoji`` (which composes the emoji faces), not ``getbbox``.
    They get their own cache so an emoji string never lands in the plain-bbox pool, where its width
    would be measured as tofu."""
    font = painter.get_font(painter.DEFAULT_FONT, 24)
    text = "hello 🎵"

    first = painter.get_text_size(font, text)
    assert painter._text_emoji_size_cache, "emoji string did not take the emoji measuring path"
    assert painter.get_text_size(font, text) == first

    plain = painter.get_text_size(font, "hello ")
    assert first[0] > plain[0], "the emoji contributed no width"
