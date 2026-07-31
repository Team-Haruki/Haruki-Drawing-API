"""Focused contract tests for the standalone native text-metrics boundary."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

try:
    import haruki_skia_renderer as _native
except ImportError:  # pragma: no cover - extension is optional outside native CI
    _native = None


REPO_ROOT = Path(__file__).resolve().parent.parent
TEST_FONT = REPO_ROOT / "data" / "SourceHanSansSC-Regular.otf"
HAS_TEXT_METRICS = _native is not None and getattr(_native, "TEXT_METRICS_CAPABILITY", 0) >= 1


@pytest.mark.skipif(
    not HAS_TEXT_METRICS or not TEST_FONT.is_file(),
    reason="native text metrics + fixture font required",
)
def test_native_text_metrics_preserve_order_and_expose_anchor_inputs():
    requests = [("Haruki", 32.0), ("未来", 24.0), ("", 18.0)]
    results = _native.measure_text_batch("", str(TEST_FONT), requests)

    assert len(results) == len(requests)
    for result in results:
        assert {
            "advance",
            "ink_bbox",
            "pillow_bbox",
            "ascent",
            "descent",
            "leading",
            "line_spacing",
            "font_top",
            "font_bottom",
            "cap_height",
            "x_height",
        } <= result.keys()
        assert all(math.isfinite(float(value)) for value in result["ink_bbox"])
        assert all(math.isfinite(float(value)) for value in result["pillow_bbox"])
        assert result["ascent"] > 0
        assert result["descent"] >= 0
        ink = result["ink_bbox"]
        pillow = result["pillow_bbox"]
        assert pillow[0] == pytest.approx(ink[0])
        assert pillow[2] == pytest.approx(ink[2])
        assert pillow[1] == pytest.approx(ink[1] + result["ascent"])
        assert pillow[3] == pytest.approx(ink[3] + result["ascent"])

    assert results[0]["advance"] > 0
    assert results[1]["advance"] > 0
    assert results[2]["advance"] == pytest.approx(0.0)


@pytest.mark.skipif(not HAS_TEXT_METRICS, reason="native text metrics required")
def test_native_text_metrics_reject_missing_font_instead_of_sans_serif(tmp_path):
    with pytest.raises(ValueError, match="without fallback"):
        _native.measure_text_batch(str(tmp_path), "missing-font.ttf", [("wrong face", 20)])


@pytest.mark.skipif(
    not HAS_TEXT_METRICS or not TEST_FONT.is_file(),
    reason="native text metrics + fixture font required",
)
@pytest.mark.parametrize("size", [0.0, -1.0, float("nan"), float("inf"), 2049.0])
def test_native_text_metrics_reject_invalid_sizes(size):
    with pytest.raises(ValueError, match="invalid font size"):
        _native.measure_text_batch("", str(TEST_FONT), [("x", size)])


@pytest.mark.skipif(
    not HAS_TEXT_METRICS or not TEST_FONT.is_file(),
    reason="native text metrics + fixture font required",
)
def test_native_text_metrics_enforce_batch_and_text_limits():
    with pytest.raises(ValueError, match="maximum is 1024"):
        _native.measure_text_batch("", str(TEST_FONT), [("x", 16.0)] * 1025)
    with pytest.raises(ValueError, match="exceeds 4096 characters"):
        _native.measure_text_batch("", str(TEST_FONT), [("x" * 4097, 16.0)])
    with pytest.raises(TypeError, match="list or tuple"):
        _native.measure_text_batch("", str(TEST_FONT), {"text": "x", "size": 16.0})
