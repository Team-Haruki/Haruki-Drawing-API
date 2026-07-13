"""Shared test fixtures.

The fonts are OFL/CC licensed and too large to vendor, so ``data/`` is gitignored and CI's
lint-test job runs with NO fonts at all (only the native-tests job downloads them). Any test whose
subject is real font metrics — text placement, the FreeType font cache, the startup font check — is
meaningless there: Pillow silently degrades to a 10px built-in bitmap face, and the test would
either fail or, worse, pass while measuring the wrong thing.

So such tests must be explicitly gated, not left to luck:

    def test_something(real_fonts): ...          # fixture: skips when the fonts are missing
"""

from __future__ import annotations

import pytest


def fonts_available() -> bool:
    """Whether the CONFIGURED fonts actually resolve to their font FILES.

    NOT `isinstance(font, FreeTypeFont)`: Pillow's load_default() fallback is a FreeTypeFont too
    (its bundled Aileron), so that would report every font as present even with an empty font dir.
    """
    from pathlib import Path

    from src.sekai.base.painter import get_font
    from src.settings import DEFAULT_BOLD_FONT, DEFAULT_FONT

    def resolved(name: str) -> bool:
        path = getattr(get_font(name, 20), "path", None)
        return isinstance(path, str) and Path(path).stem == Path(name).stem

    return all(resolved(name) for name in (DEFAULT_FONT, DEFAULT_BOLD_FONT))


@pytest.fixture
def real_fonts():
    """Skip a test that is only meaningful with the real fonts present."""
    if not fonts_available():
        pytest.skip("configured fonts are not installed (CI lint-test has no data/ fonts)")
