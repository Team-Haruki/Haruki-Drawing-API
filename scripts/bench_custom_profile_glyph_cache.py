"""Micro-benchmark for the Phase-0 process-level caches of the custom profile renderer.

Every request builds a fresh ``PNGRenderer``, so before Phase 0 the whole cold cost — fontTools
re-parsing the font per glyph (~104ms/char), ``load_font`` reopening the font file 200-400x per
request, TMP metadata re-parsed from JSON — was paid again on every render. The process pools in
``src.sekai.profile.custom_profile.cache`` survive across renderer instances; this bench builds a
FRESH renderer per iteration (simulating one request per iteration) and shows iteration 1 paying
the cold cost while iterations 2-3 collapse to near-zero on cache hits.

Runs entirely against a temp dir: a minimal synthetic TrueType font plus synthetic
``metadata.json``. No production assets or configurable paths are touched.

Run (repo root):
    uv run python scripts/bench_custom_profile_glyph_cache.py
"""

from __future__ import annotations

import json
from pathlib import Path
import sys
import tempfile
import time

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from src.sekai.profile.custom_profile.cache import clear_custom_profile_caches, get_custom_profile_cache_stats
from src.sekai.profile.custom_profile.renderer import PNGRenderer, TMPFontLibrary, load_font

GLYPHS = "春日影一二三四五六七八九十AB"  # CJK + ASCII, one TTFont parse each when cold
ITERATIONS = 3
FONT_CALLS = 300  # ~the 200-400 load_font calls a real request makes
METADATA_LOADS = 20


def build_benchmark_font(path: Path) -> None:
    """Create a tiny deterministic TrueType font containing the benchmark glyphs."""

    glyph_names = {char: f"uni{ord(char):04X}" for char in dict.fromkeys(GLYPHS)}
    glyph_order = [".notdef", *glyph_names.values()]
    glyphs = {}
    for name in glyph_order:
        pen = TTGlyphPen(None)
        pen.moveTo((80, 0))
        pen.lineTo((520, 0))
        pen.lineTo((520, 700))
        pen.lineTo((80, 700))
        pen.closePath()
        glyphs[name] = pen.glyph()

    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(glyph_order)
    builder.setupCharacterMap({ord(char): name for char, name in glyph_names.items()})
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics(dict.fromkeys(glyph_order, (600, 80)))
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "Haruki Benchmark", "styleName": "Regular"})
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    builder.setupMaxp()
    builder.save(path)


def make_renderer(workdir: Path) -> PNGRenderer:
    # Mirrors tests/test_custom_profile_renderer.py::_make_renderer — fully synthetic dirs.
    fonts = workdir / "fonts"
    assets = workdir / "asset" / "cn-assets" / "startapp" / "custom_profile"
    fonts.mkdir(exist_ok=True)
    assets.mkdir(parents=True, exist_ok=True)
    return PNGRenderer(
        masterdata=None,
        assets=assets,
        fonts=fonts,
        resources={},
        tmp_font_metadata=None,
        shape_sprite_dir=None,
        unity_ui_sprite_dir=None,
        profile_context={},
        region="cn",
    )


def main() -> int:
    with tempfile.TemporaryDirectory(prefix="bench_custom_profile_") as tmp:
        workdir = Path(tmp)
        font_path = workdir / "benchmark-font.ttf"
        build_benchmark_font(font_path)
        metadata_path = workdir / "metadata.json"
        metadata_path.write_text(json.dumps({"tmp_font_assets": []}), encoding="utf-8")

        clear_custom_profile_caches()

        print(f"font: synthetic   glyphs: {len(GLYPHS)}   fresh PNGRenderer per iteration")  # noqa: T201
        header = (
            f"{'iter':>4}  {'contours_ms':>12}  {'load_font_ms':>13}  {'metadata_ms':>12}"
            f"   ({len(GLYPHS)} glyphs / {FONT_CALLS} calls / {METADATA_LOADS} loads)"
        )
        print(header)  # noqa: T201
        print("-" * len(header))  # noqa: T201

        for iteration in range(1, ITERATIONS + 1):
            renderer = make_renderer(workdir)  # fresh instance == one request

            t0 = time.perf_counter()
            for ch in GLYPHS:
                renderer.tmp_vector_glyph_contours(font_path, ch, 48.0)
            contours_ms = (time.perf_counter() - t0) * 1000.0

            t0 = time.perf_counter()
            for i in range(FONT_CALLS):
                load_font(font_path, 10 + (i % 31))  # sizes 10..40, cycling
            load_font_ms = (time.perf_counter() - t0) * 1000.0

            t0 = time.perf_counter()
            for _ in range(METADATA_LOADS):
                TMPFontLibrary.load(metadata_path, source_metadata_path=None)
            metadata_ms = (time.perf_counter() - t0) * 1000.0

            print(f"{iteration:>4}  {contours_ms:>12.2f}  {load_font_ms:>13.2f}  {metadata_ms:>12.2f}")  # noqa: T201

        print()  # noqa: T201
        for name, pool in get_custom_profile_cache_stats().items():
            rate = pool["hit_rate"]
            rate_text = f"{rate * 100.0:.1f}%" if rate is not None else "n/a"
            print(  # noqa: T201
                f"{name:>14}: hits={pool['hits']:<4} misses={pool['misses']:<4} "
                f"entries={pool['entries']:<4} hit_rate={rate_text}"
            )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
