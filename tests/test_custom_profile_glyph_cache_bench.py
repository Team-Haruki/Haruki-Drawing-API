from pathlib import Path

from fontTools.ttLib import TTFont

from scripts.bench_custom_profile_glyph_cache import GLYPHS, build_benchmark_font


def test_build_benchmark_font_contains_every_glyph(tmp_path: Path) -> None:
    font_path = tmp_path / "benchmark.ttf"

    build_benchmark_font(font_path)

    font = TTFont(font_path)
    cmap = font.getBestCmap()
    assert cmap is not None
    assert set(map(ord, GLYPHS)) <= cmap.keys()
