"""Shared text-layer bounds and native field planning must stay independent of page pixels."""

from pathlib import Path
from types import SimpleNamespace

import numpy as np
from PIL import Image
import pytest

from src.sekai.profile.custom_profile.renderer import PNGRenderer, TMPSdfShadingScalars, TMPSdfUnderlayScalars
from src.sekai.profile.custom_profile.tmp_sdf_text import text_layer_crop


@pytest.mark.parametrize("alpha", [0.0, 0.001, 0.5, 1.0])
@pytest.mark.parametrize("underlay", [None, TMPSdfUnderlayScalars(4.0, 1.3, -2, 3, (18, 45, 160))])
def test_trim_coverage_is_the_same_quantized_alpha_as_legacy_glyphs(alpha, underlay):
    renderer = object.__new__(PNGRenderer)
    field = np.random.default_rng(72).random((13, 17), dtype=np.float32)
    scalars = TMPSdfShadingScalars(5.0, 2.0, alpha, (35, 80, 110), underlay)
    legacy = renderer._shade_field_with_scalars(field, scalars)
    _, _, coverage = renderer.tmp_sdf_coverage(field, scalars)
    quantized = np.clip(np.rint(coverage * 255.0), 0, 255).astype(np.uint8)
    assert quantized.tobytes() == legacy.getchannel("A").tobytes()


@pytest.mark.parametrize("pivot", [(-3.4, 20.7), (13.5, 17.2), (60.4, -8.8)])
def test_crop_preserves_legacy_pivot_padding(pivot):
    size = (60, 40)
    alpha = Image.new("L", size)
    alpha.paste(255, (20, 12, 35, 24))
    bbox = alpha.getbbox()
    # Evaluate the old rule independently, including clipping a pivot outside the raster.
    import math

    expected = (
        max(0, math.floor(min(20, pivot[0])) - 4),
        max(0, math.floor(min(12, pivot[1])) - 4),
        min(60, math.ceil(max(35, pivot[0])) + 4),
        min(40, math.ceil(max(24, pivot[1])) + 4),
    )
    assert text_layer_crop(size, pivot, bbox) == expected
    assert text_layer_crop(size, pivot, None) == (0, 0, 60, 40)


def test_scene_field_budget_rejects_before_glyph_allocation(monkeypatch):
    renderer = object.__new__(PNGRenderer)
    character = SimpleNamespace(line_index=0, visible=True, char="A", style=SimpleNamespace(rotate=0))
    layout = SimpleNamespace(characters=[character], lines=[SimpleNamespace(index=0, width=100)])
    monkeypatch.setattr(renderer, "use_em_block", lambda run: False)
    monkeypatch.setattr(renderer, "tmp_native_unrotated_quad_size", lambda char: (100, 100))

    def unexpected(*args):
        pytest.fail("oversized field was allocated before the scene budget check")

    monkeypatch.setattr(renderer, "render_tmp_sdf_character_field", unexpected)
    with pytest.raises(ValueError, match="remaining native scene memory"):
        renderer.prepare_tmp_direct_sdf_glyphs(
            "unused",
            Path("unused"),
            layout,
            [0],
            "left",
            100,
            0,
            0,
            "#000000",
            0.2,
            max_field_bytes=9999,
        )


def test_legacy_metric_recovery_is_never_classified_as_pure_native(monkeypatch):
    from src.core.pillow_telemetry import (
        PILLOW_TOUCH_TEXT_METRIC,
        begin_pillow_touch_scope,
        end_pillow_touch_scope,
        take_pillow_touch_snapshot,
    )
    from src.sekai.profile.custom_profile import cache
    from src.sekai.profile.custom_profile.tmp_sdf_text import TMPFallbackMetrics

    monkeypatch.setattr(cache, "get_render_font", lambda *args: SimpleNamespace(getbbox=lambda text: (0, 1, 4, 8)))
    token = begin_pillow_touch_scope()
    try:
        metric = TMPFallbackMetrics(Path("unused"), 12)
        assert take_pillow_touch_snapshot().counts == {}
        assert metric.getbbox("A") == (0, 1, 4, 8)
        snapshot = take_pillow_touch_snapshot()
        assert snapshot.counts == {PILLOW_TOUCH_TEXT_METRIC: 1}
        assert snapshot.native_purity == "hybrid"
    finally:
        end_pillow_touch_scope(token)
