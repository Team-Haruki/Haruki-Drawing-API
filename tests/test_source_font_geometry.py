"""Selective FontTools loading must preserve its exact pen commands and metrics."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from fontTools.fontBuilder import FontBuilder
from fontTools.pens.recordingPen import DecomposingRecordingPen
from fontTools.pens.t2CharStringPen import T2CharStringPen
from fontTools.pens.ttGlyphPen import TTGlyphPen
from fontTools.ttLib import TTFont
import pytest

from src.sekai.profile.custom_profile.renderer import TMPFontLibrary, TMPGlyphTable, _glyph_metrics_from_rows
from src.sekai.profile.custom_profile.source_font import open_source_font, outline_glyph_set


def make_font(path: Path, kind: str):
    names = [".notdef", "A", "acute", "Aacute", "empty", "unused"]
    builder = FontBuilder(1000, isTTF=kind == "ttf")
    builder.setupGlyphOrder(names)
    builder.setupCharacterMap({65: "A", 193: "Aacute", 32: "empty", 33: "unused"})
    glyphs = {}
    for name in names:
        pen = TTGlyphPen(glyphs) if kind == "ttf" else T2CharStringPen(600, None, roundTolerance=0)
        if name == "Aacute" and kind == "ttf":
            pen.addComponent("A", (0.5, 0, 0, 1, 21, 0))
            pen.addComponent("acute", (1, 0, 0, 1, -7, 700))
        elif name != "empty":
            pen.moveTo((20, 0))
            if kind == "ttf":
                pen.qCurveTo((201, 701), (401, 700), (520, 0))
            else:
                # Fractional CFF control points must not become FreeType integer outlines.
                pen.curveTo((201.25, 701.5), (401.75, 700.25), (520, 0))
            pen.closePath()
        glyphs[name] = pen.glyph() if kind == "ttf" else pen.getCharString()
    if kind == "ttf":
        builder.setupGlyf(glyphs)
    else:
        if kind == "seac":
            from fontTools.misc.psCharStrings import T2CharString

            glyphs["Aacute"] = T2CharString(program=[600, 0, 500, 65, 194, "endchar"])
        builder.setupCFF("GeometryTest", {}, glyphs, {})
    # Deliberately differ from xMin, and use repeated advances (compressed hmtx).
    builder.setupHorizontalMetrics({name: (600, 7 + i) for i, name in enumerate(names)})
    builder.setupHorizontalHeader(ascent=800, descent=-200)
    builder.setupVerticalMetrics({name: (900, -10 - i) for i, name in enumerate(names)})
    builder.setupVerticalHeader(ascent=800, descent=-200)
    builder.setupNameTable({"familyName": "GeometryTest", "styleName": "Regular"})
    builder.setupOS2(sTypoAscender=800, sTypoDescender=-200, usWinAscent=800, usWinDescent=200)
    builder.setupPost()
    if kind == "cid":
        from fontTools.cffLib import FDArrayIndex, FDSelect, FontDict

        font = builder.font
        top = font["CFF "].cff.topDictIndex[0]
        aliases = {name: ".notdef" if i == 0 else f"cid{i:05d}" for i, name in enumerate(names)}
        order = [aliases[name] for name in names]
        font.setGlyphOrder(order)
        for cmap in font["cmap"].tables:
            cmap.cmap = {key: aliases[value] for key, value in cmap.cmap.items()}
        for tag in ("hmtx", "vmtx"):
            font[tag].metrics = {aliases[key]: value for key, value in font[tag].metrics.items()}
        top.charset = order
        top.CharStrings.charStrings = {aliases[key]: value for key, value in top.CharStrings.charStrings.items()}
        top.ROS = ("Adobe", "Identity", 0)
        top.CIDCount = len(names)
        top.FDArray = FDArrayIndex()
        fd = FontDict()
        fd.Private = top.Private
        fd.FontName = "GeometryTest"
        top.FDArray.append(fd)
        top.FDSelect = FDSelect(format=3)
        top.FDSelect.gidArray = [0] * len(names)
        del top.Private
    builder.save(path)


def recording(font, codepoint, *, optimized):
    name = font.getBestCmap().get(codepoint)
    if name is None:
        return None
    glyphs = outline_glyph_set(font) if optimized else font.getGlyphSet()
    pen = DecomposingRecordingPen(glyphs)
    glyphs[name].draw(pen)
    return pen.value


@pytest.mark.parametrize("kind", ["ttf", "cff", "seac", "cid"])
def test_geometry_matches_original_including_composites_bearings_and_fractional_cff(tmp_path, kind):
    path = tmp_path / "font.otf"
    make_font(path, kind)
    with TTFont(path) as original, open_source_font(path) as optimized:
        if kind != "ttf":
            assert any(
                float(value) % 1
                for _op, points in recording(original, 65, optimized=False)
                for point in points
                for value in point
            )
        for codepoint in (65, 193, 32, 0x301C):
            assert recording(optimized, codepoint, optimized=True) == recording(original, codepoint, optimized=False)
        if kind == "ttf":
            unused = optimized.getBestCmap()[33]
            assert dict.__getitem__(optimized["glyf"].glyphs, unused) is None
            assert dict.__getitem__(optimized["hmtx"].metrics, unused) is None
            for codepoint in (65, 193, 32):
                old_name = original.getBestCmap()[codepoint]
                new_name = optimized.getBestCmap()[codepoint]
                assert optimized["hmtx"][new_name] == original["hmtx"][old_name]
                assert optimized["vmtx"][new_name] == original["vmtx"][old_name]


def test_source_reader_shared_only_within_request_and_replaced_with_font(tmp_path, monkeypatch):
    from src.sekai.profile.custom_profile import source_font

    path = tmp_path / "font.ttf"
    make_font(path, "ttf")
    real_open = source_font.open_source_font
    calls = []

    def counted(path):
        calls.append(path)
        return real_open(path)

    monkeypatch.setattr(source_font, "open_source_font", counted)
    library = TMPFontLibrary({})

    def load(library):
        with library.source_font(path) as font:
            return font

    first = load(library)
    assert load(library) is first
    assert len(calls) == 1
    path.write_bytes(path.read_bytes() + b"\0")
    assert load(library) is not first
    assert len(calls) == 2
    assert load(TMPFontLibrary({})) is not load(library)
    path.unlink()
    with pytest.raises(FileNotFoundError):
        load(library)


def test_glyph_table_decodes_only_read_values_and_preserves_mapping_semantics():
    char = {"m_Scale": 0}
    glyph = {"m_Metrics": {"m_Width": 13.5}, "m_GlyphRect": {"m_X": 7}, "m_Scale": 1.5, "m_AtlasIndex": 2}
    rows = {65: (char, glyph), 66: ({}, {"m_Metrics": {"m_Width": "invalid"}})}
    table = TMPGlyphTable(rows)
    assert bool(table)
    assert len(table) == 2
    assert list(table) == [65, 66]
    assert 66 in table
    assert table.get(999) is None
    assert table[65] == _glyph_metrics_from_rows(char, glyph)
    assert table[65] is table[65]
    assert isinstance(table._entries[66], tuple)
    with pytest.raises(ValueError, match="invalid"):
        table[66]
    assert isinstance(table._entries[66], tuple)
    with pytest.raises(TypeError):
        table[65] = table[65]


def test_glyph_table_concurrent_first_read_publishes_one_immutable_value():
    table = TMPGlyphTable({65: ({}, {"m_Metrics": {"m_Width": 10}})})
    with ThreadPoolExecutor(max_workers=8) as executor:
        results = list(executor.map(lambda _: table[65], range(64)))
    assert all(result is results[0] for result in results)


def test_parallel_layers_share_reader_without_racing_table_cursors(tmp_path):
    path = tmp_path / "font.ttf"
    make_font(path, "ttf")
    library = TMPFontLibrary({})
    with TTFont(path) as font:
        expected = {codepoint: recording(font, codepoint, optimized=False) for codepoint in (65, 193, 32)}

    def draw(codepoint):
        with library.source_font(path) as font:
            return recording(font, codepoint, optimized=True)

    codepoints = [65, 193, 32] * 16
    with ThreadPoolExecutor(max_workers=8) as executor:
        values = list(executor.map(draw, codepoints))
    assert values == [expected[codepoint] for codepoint in codepoints]
