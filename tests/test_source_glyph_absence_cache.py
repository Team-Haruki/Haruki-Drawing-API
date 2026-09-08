"""Only a parsed source cmap may establish a persistent missing-glyph verdict."""

from concurrent.futures import ThreadPoolExecutor
import os
from pathlib import Path
from types import SimpleNamespace

import pytest

from src.sekai.profile.custom_profile import cache, renderer


@pytest.fixture
def pool(monkeypatch):
    value = cache.BoundedCache("test_contours", 4, 8192, cache._contours_bytes)
    monkeypatch.setattr(cache, "GLYPH_CONTOUR_CACHE", value)
    monkeypatch.setattr(renderer, "GLYPH_CONTOUR_CACHE", value)
    monkeypatch.setattr(renderer, "freetype_metrics", lambda: None)
    return value


def write_font(path: Path, include_wave=False):
    from fontTools.fontBuilder import FontBuilder
    from fontTools.pens.ttGlyphPen import TTGlyphPen

    names = [".notdef", "A"] + (["wave"] if include_wave else [])
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder(names)
    builder.setupCharacterMap({65: "A", **({0x301C: "wave"} if include_wave else {})})
    glyphs = {}
    for name in names:
        pen = TTGlyphPen(None)
        pen.moveTo((10, 0))
        pen.lineTo((400, 0))
        pen.lineTo((400, 700))
        pen.closePath()
        glyphs[name] = pen.glyph()
    builder.setupGlyf(glyphs)
    builder.setupHorizontalMetrics(dict.fromkeys(names, (500, 10)))
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "CacheTest", "styleName": "Regular"})
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    builder.save(path)


@pytest.fixture
def font(tmp_path):
    path = tmp_path / "test.ttf"
    write_font(path)
    return path


def library():
    return renderer.TMPFontLibrary({})


def test_confirmed_absence_reuses_across_requests_and_sizes(pool, font, monkeypatch):
    import fontTools.ttLib

    opened = []
    original = fontTools.ttLib.TTFont

    def load(*args, **kwargs):
        opened.append(args[0])
        return original(*args, **kwargs)

    monkeypatch.setattr(fontTools.ttLib, "TTFont", load)
    assert library()._load_source_glyph_metrics(font, "〜", 75) is None
    assert library()._load_source_glyph_metrics(font, "〜", 100) is None
    assert len(opened) == 1
    assert pool.stats()["hits"] == 1
    assert pool.stats()["bytes"] >= 512 + 4 * len(str(font.resolve()))
    assert library()._load_source_glyph_metrics(font, "A", 75) is not None


def test_replacement_invalidates_even_the_same_library_font_handle(pool, font):
    lib = library()
    assert lib._load_source_glyph_metrics(font, "〜", 75) is None
    write_font(font, include_wave=True)
    assert lib._load_source_glyph_metrics(font, "〜", 75) is not None


def test_deletion_and_recreation_do_not_reuse_old_absence(pool, font):
    assert library()._load_source_glyph_metrics(font, "〜", 75) is None
    font.unlink()
    with pytest.raises(FileNotFoundError):
        library()._load_source_glyph_metrics(font, "〜", 75)
    write_font(font, include_wave=True)
    assert library()._load_source_glyph_metrics(font, "〜", 75) is not None


def test_transient_fonttools_failure_is_never_cached(pool, font, monkeypatch):
    import fontTools.ttLib

    original = fontTools.ttLib.TTFont

    def fail(*args, **kwargs):
        raise OSError("temporary font read failure")

    monkeypatch.setattr(fontTools.ttLib, "TTFont", fail)
    with pytest.raises(OSError, match="temporary font read failure"):
        library()._load_source_glyph_metrics(font, "〜", 75)
    assert pool.stats()["entries"] == 0
    monkeypatch.setattr(fontTools.ttLib, "TTFont", original)
    assert library()._load_source_glyph_metrics(font, "〜", 75) is None
    assert pool.stats()["entries"] == 1


def test_freetype_recovery_gets_first_refusal_over_fonttools_absence(pool, font, monkeypatch):
    assert library()._load_source_glyph_metrics(font, "〜", 75) is None
    recovered = object()
    monkeypatch.setattr(renderer, "freetype_metrics", lambda: SimpleNamespace(glyph_metrics=lambda *_args: recovered))
    assert library()._load_source_glyph_metrics(font, "〜", 75) is recovered


def test_disabled_clear_and_eviction_preserve_recomputation(pool, font):
    assert library()._load_source_glyph_metrics(font, "〜", 75) is None
    cache.clear_custom_profile_caches()
    assert pool.stats()["entries"] == 0
    pool.max_entries = 0
    assert library()._load_source_glyph_metrics(font, "〜", 75) is None
    assert pool.stats()["entries"] == 0
    pool.max_entries = 1
    for ch in "〜☆〜":
        assert library()._load_source_glyph_metrics(font, ch, 75) is None
    assert pool.stats()["entries"] == 1
    assert pool.stats()["evictions"] == 2


def test_changed_file_during_cmap_read_cannot_publish_a_verdict(pool, font, monkeypatch):
    import fontTools.ttLib

    original = fontTools.ttLib.TTFont

    class ReplacingFont(original):
        def getBestCmap(self, *args, **kwargs):
            result = super().getBestCmap(*args, **kwargs)
            stat = font.stat()
            os.utime(font, ns=(stat.st_atime_ns, stat.st_mtime_ns + 1_000_000))
            return result

    monkeypatch.setattr(fontTools.ttLib, "TTFont", ReplacingFont)
    assert library()._load_source_glyph_metrics(font, "〜", 75) is None
    assert pool.stats()["entries"] == 0


def test_concurrent_requests_share_only_immutable_verdicts(pool, font):
    def query(_):
        return library()._load_source_glyph_metrics(font, "〜", 75)

    with ThreadPoolExecutor(max_workers=4) as executor:
        assert list(executor.map(query, range(16))) == [None] * 16
    assert pool.stats()["entries"] == 1
