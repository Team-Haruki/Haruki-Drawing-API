"""Missing TMP metadata uses native BASIC metrics without losing .notdef advances."""

from pathlib import Path
from types import SimpleNamespace

from PIL import ImageFont
import pytest

from src.core.pillow_telemetry import begin_pillow_touch_scope, end_pillow_touch_scope, take_pillow_touch_snapshot
from src.sekai.base import font_metrics
from src.sekai.profile.custom_profile.tmp_sdf_text import TMPFallbackMetrics

FONT = Path(__file__).resolve().parents[1] / "data/SourceHanSansSC-Regular.otf"
TEXTS = ("\u200b", "\u200d", "\t", "\u00ad", "🙂", "𠮷", "á", "Ag日\u200b本語", "", " ")


@pytest.fixture
def native():
    module = pytest.importorskip("haruki_skia_renderer")
    if getattr(module, "TEXT_METRICS_CAPABILITY", 0) < 2 or not FONT.is_file():
        pytest.skip("native BASIC metrics and fixture font required")
    return module


@pytest.mark.parametrize("size", [-1, 1, 7.5, 12.5, 45, 90, 150.3])
def test_fallback_metrics_preserve_basic_glyph_bounds_and_advances(native, monkeypatch, size):
    legacy = ImageFont.truetype(str(FONT), max(1, round(size)), layout_engine=ImageFont.Layout.BASIC)
    expected = {text: (legacy.getbbox(text), legacy.getlength(text)) for text in TEXTS}

    def unexpected(*args):
        pytest.fail("Pillow metrics reached")

    monkeypatch.setattr(TMPFallbackMetrics, "_legacy_font", unexpected)
    monkeypatch.setattr(ImageFont, "truetype", unexpected)
    metrics = TMPFallbackMetrics(FONT, size)
    token = begin_pillow_touch_scope()
    try:
        for text in TEXTS:
            assert (metrics.getbbox(text), metrics.getlength(text)) == expected[text]
        assert take_pillow_touch_snapshot().counts == {}
    finally:
        end_pillow_touch_scope(token)


@pytest.mark.parametrize("failure", ["missing", "stale", "broken"])
def test_failed_native_metrics_remain_visible_in_pillow_telemetry(native, monkeypatch, failure):
    if failure == "broken":

        def reject(*args, **kwargs):
            raise RuntimeError("broken native BASIC scaler")

        monkeypatch.setattr(native, "measure_text_batch", reject)
    elif failure == "missing":
        monkeypatch.setattr(font_metrics, "_native", lambda: None)
    else:
        monkeypatch.setattr(native, "TEXT_METRICS_CAPABILITY", 0)
    for _ in range(2):
        token = begin_pillow_touch_scope()
        try:
            metric = TMPFallbackMetrics(FONT, 20)
            assert metric.getlength("A") > 0
            assert take_pillow_touch_snapshot().native_purity == "hybrid"
        finally:
            end_pillow_touch_scope(token)


def test_absent_font_uses_explicit_legacy_adapter(monkeypatch, tmp_path):
    from src.sekai.profile.custom_profile import cache

    path = tmp_path / "missing.ttf"
    calls = []

    def legacy(font_path, size):
        calls.append((font_path, size))
        return SimpleNamespace(getlength=lambda text: 9.0)

    monkeypatch.setattr(cache, "get_render_font", legacy)
    metric = TMPFallbackMetrics(path, 12)
    assert metric.getlength("A") == 9
    assert calls == [(path, 12)]
