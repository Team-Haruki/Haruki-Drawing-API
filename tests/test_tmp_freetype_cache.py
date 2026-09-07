"""TMP source metrics and A8 rasters must follow font asset replacement."""

from concurrent.futures import ThreadPoolExecutor
import os
import shutil
import threading

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.ttGlyphPen import TTGlyphPen
import pytest

from src.sekai.profile.custom_profile.renderer import FreeTypeMetrics


def _font(path, width):
    builder = FontBuilder(1000, isTTF=True)
    builder.setupGlyphOrder([".notdef", "A"])
    builder.setupCharacterMap({65: "A"})
    empty = TTGlyphPen(None).glyph()
    pen = TTGlyphPen(None)
    pen.moveTo((0, 0))
    pen.lineTo((width, 0))
    pen.lineTo((width, 700))
    pen.lineTo((0, 700))
    pen.closePath()
    builder.setupGlyf({".notdef": empty, "A": pen.glyph()})
    builder.setupHorizontalMetrics({".notdef": (500, 0), "A": (width + 100, 0)})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "Cache Test", "styleName": "Regular"})
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    builder.setupMaxp()
    builder.save(path)


@pytest.fixture
def fonts(tmp_path):
    paths = [tmp_path / name for name in ("narrow.ttf", "wide.ttf")]
    for path, width in zip(paths, (400, 700), strict=True):
        _font(path, width)
    return paths


@pytest.fixture
def metrics():
    try:
        service = FreeTypeMetrics()
    except OSError as exc:
        pytest.skip(str(exc))
    yield service
    service.close()


def _replace(source, target):
    stamp = target.stat().st_mtime_ns if target.exists() else 0
    staged = target.with_suffix(".next")
    shutil.copyfile(source, staged)
    new_stamp = max(stamp + 1_000_000, staged.stat().st_mtime_ns)
    os.utime(staged, ns=(new_stamp, new_stamp))
    os.replace(staged, target)


@pytest.mark.parametrize("operation", ["glyph_metrics", "glyph_bitmap"])
def test_same_service_observes_replaced_deleted_and_recreated_font(metrics, fonts, tmp_path, operation):
    path = tmp_path / "live.ttf"
    read = getattr(metrics, operation)
    expected = [read(font, "A", 90) for font in fonts]
    assert expected[0] != expected[1]
    _replace(fonts[0], path)
    assert read(path, "A", 90) == expected[0]
    _replace(fonts[1], path)
    assert read(path, "A", 90) == expected[1]
    path.unlink()
    with pytest.raises(FileNotFoundError):
        read(path, "A", 90)
    _replace(fonts[0], path)
    assert read(path, "A", 90) == expected[0]


def test_broken_replacement_does_not_keep_old_face_or_poison_recovery(metrics, fonts, tmp_path):
    path = tmp_path / "live.ttf"
    _replace(fonts[0], path)
    before = metrics.glyph_metrics(path, "A", 90)
    corrupt = tmp_path / "corrupt.ttf"
    corrupt.write_bytes(b"not a font")
    _replace(corrupt, path)
    with pytest.raises(OSError, match="FT_New_Face"):
        metrics.glyph_metrics(path, "A", 90)
    _replace(fonts[0], path)
    assert metrics.glyph_metrics(path, "A", 90) == before


@pytest.mark.parametrize("change", ["replace", "delete"])
def test_change_while_opening_closes_face_and_retries_on_next_call(metrics, fonts, tmp_path, monkeypatch, change):
    path = tmp_path / "live.ttf"
    _replace(fonts[0], path)
    opened, freed = [], []
    new_face, done_face = metrics.lib.FT_New_Face, metrics.lib.FT_Done_Face

    def open_then_change(*args):
        result = new_face(*args)
        opened.append(result)
        if change == "replace":
            _replace(fonts[1], path)
        else:
            path.unlink()
        return result

    def close_face(face):
        freed.append(True)
        return done_face(face)

    monkeypatch.setattr(metrics.lib, "FT_New_Face", open_then_change)
    monkeypatch.setattr(metrics.lib, "FT_Done_Face", close_face)
    with pytest.raises(OSError, match=r"Font changed while opening|No such file"):
        metrics.glyph_metrics(path, "A", 90)
    assert opened == [0]
    assert len(freed) == 1
    monkeypatch.setattr(metrics.lib, "FT_New_Face", new_face)
    _replace(fonts[1], path)
    assert metrics.glyph_metrics(path, "A", 90) == metrics.glyph_metrics(fonts[1], "A", 90)


def test_face_lru_closes_evicted_faces_and_reopens_without_pixel_drift(metrics, fonts, tmp_path, monkeypatch):
    monkeypatch.setattr(metrics, "MAX_CACHED_FACES", 3)
    freed = []
    done_face = metrics.lib.FT_Done_Face

    def close_face(face):
        freed.append(True)
        return done_face(face)

    monkeypatch.setattr(metrics.lib, "FT_Done_Face", close_face)
    paths = [tmp_path / f"font-{i}.ttf" for i in range(4)]
    for path in paths:
        shutil.copyfile(fonts[0], path)
    reference = metrics.glyph_bitmap(paths[0], "A", 90)
    for path in paths[1:3]:
        assert metrics.glyph_bitmap(path, "A", 90) == reference
    assert metrics.glyph_bitmap(paths[0], "A", 90) == reference  # refresh recency
    assert metrics.glyph_bitmap(paths[3], "A", 90) == reference
    assert list(metrics._faces) == [paths[2], paths[0], paths[3]]
    assert len(freed) == 1
    assert metrics.glyph_bitmap(paths[1], "A", 90) == reference
    assert len(freed) == 2
    metrics.close()
    assert len(freed) == 5
    metrics.close()
    assert len(freed) == 5
    assert metrics.glyph_bitmap(paths[0], "A", 90) == reference


def test_concurrent_replacement_metrics_rasters_and_close_are_consistent(metrics, fonts, tmp_path):
    path = tmp_path / "live.ttf"
    sizes = (12, 45, 90, 150)
    expected = {(font, size): metrics.glyph_bitmap(font, "A", size) for font in fonts for size in sizes}
    _replace(fonts[0], path)
    barrier = threading.Barrier(5)

    def reader(offset):
        barrier.wait(timeout=10)
        for i in range(60):
            size = sizes[(i + offset) % len(sizes)]
            try:
                bitmap = metrics.glyph_bitmap(path, "A", size)
                layout = metrics.glyph_metrics(path, "A", size)
            except OSError as exc:
                if "Font changed while opening" not in str(exc):
                    raise
                continue
            assert bitmap in [expected[font, size] for font in fonts]
            assert layout in [expected[font, size][3] for font in fonts]

    def updater():
        barrier.wait(timeout=10)
        for i in range(60):
            _replace(fonts[i % 2], path)
            metrics.close()

    with ThreadPoolExecutor(5) as pool:
        futures = [pool.submit(reader, offset) for offset in range(4)] + [pool.submit(updater)]
        for future in futures:
            future.result(timeout=30)
    for size in sizes:
        assert metrics.glyph_bitmap(path, "A", size) == expected[fonts[1], size]
