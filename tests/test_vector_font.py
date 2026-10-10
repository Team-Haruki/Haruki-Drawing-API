"""Hinted vector text shares one geometry across both raster backends."""

from pathlib import Path
from types import SimpleNamespace

import pytest

from src.sekai.base import vector_font
from src.sekai.base.vector import VectorText
from src.settings import DEFAULT_FONT, FONT_DIR

try:
    import haruki_skia_renderer as native
except ImportError:
    native = None

FONT = Path(FONT_DIR) / (DEFAULT_FONT + ".otf")
pytestmark = pytest.mark.skipif(
    native is None or getattr(native, "TEXT_METRICS_CAPABILITY", 0) < 3 or not FONT.is_file(),
    reason="native vector text geometry and Source Han fixture required",
)


@pytest.mark.parametrize("size", [10 * 100 / 72, 12 * 100 / 72])
@pytest.mark.parametrize("text", ["07-09 20:00", "123.4567w", "分数线", "排名", " ", ""])
def test_hinted_advances_match_legacy_chart_at_100_dpi(size, text):
    from src.sekai.base.pillow_vector import text_geometry

    result = native.vector_text_geometry(str(FONT_DIR), DEFAULT_FONT, text, size)
    expected = text_geometry(DEFAULT_FONT, size, text)
    assert result["advance"] == expected["advance"]
    assert result["ascent"] == pytest.approx(expected["ascent"], abs=1 / 64)
    assert result["descent"] == pytest.approx(expected["descent"], abs=1 / 64)
    assert bool(result["commands"]) == bool(text.strip())
    assert all(op in ("move", "line", "quad", "cubic", "close") for op, _ in result["commands"])


def test_outline_decomposition_handles_truetype_implied_points():
    matplotlib = pytest.importorskip("matplotlib")
    font = Path(matplotlib.get_data_path()) / "fonts/ttf/DejaVuSans.ttf"
    result = native.vector_text_geometry("", str(font), "éÅg", 18)
    assert any(op == "quad" for op, _ in result["commands"])
    assert sum(op == "move" for op, _ in result["commands"]) == sum(op == "close" for op, _ in result["commands"])
    assert result["advance"] > 20
    assert result["ink_bbox"][1] < -10


def test_plot_sizing_does_not_pollute_basic_face_cache():
    before = native.measure_text_batch(str(FONT_DIR), DEFAULT_FONT, [("未来 123", 14)], engine="freetype_basic")
    native.vector_text_geometry(str(FONT_DIR), DEFAULT_FONT, "未来 123", 10 * 100 / 72)
    after = native.measure_text_batch(str(FONT_DIR), DEFAULT_FONT, [("未来 123", 14)], engine="freetype_basic")
    assert before == after


@pytest.mark.parametrize("size", [0, -1, float("inf"), float("nan"), 3000])
def test_invalid_size_rejected_before_font_access(size):
    with pytest.raises(ValueError, match="font size"):
        native.vector_text_geometry("missing", "missing", "test", size)


def test_text_geometry_is_bounded():
    with pytest.raises(ValueError, match="characters"):
        native.vector_text_geometry("missing", "missing", "x" * 4097, 14)
    with pytest.raises(ValueError, match="command limit"):
        native.vector_text_geometry(str(FONT_DIR), DEFAULT_FONT, "哇" * 1000, 14)


def test_missing_geometry_api_keeps_legacy_fallback(monkeypatch):
    monkeypatch.setattr(vector_font, "import_module", lambda _: SimpleNamespace())
    run = vector_font.glyph_run(DEFAULT_FONT, 10 * 100 / 72, "07-09 20:00")
    assert run.advance == 76
    assert run.commands


def test_vector_text_reuses_its_immutable_layout(monkeypatch):
    original = vector_font.glyph_run
    calls = []

    def measure(*args):
        calls.append(args)
        return original(*args)

    monkeypatch.setattr(vector_font, "glyph_run", measure)
    text = VectorText("123", DEFAULT_FONT, 14, align="right", angle=30)
    first = text.path((30, 40))
    second = text.path((50, 60))
    assert len(calls) == 1
    assert first.commands[0][1][0] + 20 == pytest.approx(second.commands[0][1][0])
    assert first.commands[0][1][1] + 20 == pytest.approx(second.commands[0][1][1])
