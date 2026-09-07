"""Last-resort TMP character coverage keeps the legacy floating SDF."""

from dataclasses import replace
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image, ImageDraw, ImageFont
import pytest

from src.sekai.profile.custom_profile.renderer import (
    PNGRenderer,
    TextRun,
    TextStyle,
    alpha_mask_to_sdf_field,
    tmp_dynamic_sdf_alpha_threshold,
)
from src.settings import CUSTOM_PROFILE_ASSETS_DIR, CUSTOM_PROFILE_FONTS_DIR

ROOT = Path(__file__).resolve().parents[1]
PAYLOAD = ROOT / "out/parity-payloads/custom_profile_card_static_missing_glyphs.json"
METADATA = ROOT / "data/custom_profile/tmp-font-assets/cn/metadata.json"
STYLE = TextStyle("#7030b0", 0.7, 45, 1, 0, None, 0, 0, None, 0, 0, None, False, False, False, False)


@pytest.fixture
def renderer():
    native = pytest.importorskip("haruki_skia_renderer")
    if getattr(native, "TEXT_MASK_CAPABILITY", 0) < 1 or not PAYLOAD.is_file() or not METADATA.is_file():
        pytest.skip("native mask and extracted TMP fixture required")
    raw = json.loads(PAYLOAD.read_text())
    return PNGRenderer(
        masterdata=None,
        assets=Path(str(CUSTOM_PROFILE_ASSETS_DIR).format(region="cn")),
        fonts=Path(str(CUSTOM_PROFILE_FONTS_DIR).format(region="cn")),
        tmp_font_metadata=METADATA,
        resources=raw["resources"],
    )


def _old_field(renderer, name, path, char, style, size, dilate):
    # Unmodified legacy mask painters are the oracle. No new field helper participates.
    font = ImageFont.truetype(str(path), max(1, round(size)), layout_engine=ImageFont.Layout.BASIC)
    run = TextRun(char, style)
    fx = renderer.tmp_scale_mode in {"fx-center", "fx-native"}
    bbox = renderer.run_fx_bbox(font, run, name, size) if fx else renderer.run_bbox(font, run, name, size)
    asset = renderer.tmp_sdf_asset(name)
    pad = renderer.tmp_display_padding(asset, dilate, size)
    mask = Image.new("L", (max(1, bbox[2] - bbox[0] + pad * 2), max(1, bbox[3] - bbox[1] + pad * 2)), 0)
    if fx:
        renderer.draw_text_mask_run_fx(mask, (pad - bbox[0], pad - bbox[1]), run, font, name, size)
    else:
        renderer.draw_text_mask_run(ImageDraw.Draw(mask), (pad - bbox[0], pad - bbox[1]), run, font, name, size)
        if renderer.tmp_scale_mode == "x" and style.scale_x != 1:
            mask = mask.resize((max(1, round(mask.width * style.scale_x)), mask.height), Image.Resampling.BICUBIC)
    spread = renderer.tmp_sdf_spread(asset, size, renderer.tmp_fx_scale_x(style))
    return alpha_mask_to_sdf_field(mask, spread, tmp_dynamic_sdf_alpha_threshold(asset)), bbox, pad


@pytest.mark.parametrize("mode", ["fx-native", "fx-center", "x", "uniform"])
@pytest.mark.parametrize("scale", [0.7, 1, 1.4])
@pytest.mark.parametrize("size", [12.5, 45, 150.3])
def test_fallback_fields_match_old_mask_distance_pipeline(renderer, monkeypatch, mode, scale, size):
    renderer.tmp_scale_mode = mode
    name = "FOT-RodinNTLGPro-EB-OnDemand"
    path = renderer.font_path_for(name)
    style = replace(STYLE, scale_x=scale, size=size)
    font_size = renderer.tmp_run_font_size(style)
    chars = ["🙂", "𠮷", "́", "\u200d", "\t", "\u200b", "j"]
    expected = {ch: _old_field(renderer, name, path, ch, style, font_size, 0.25) for ch in chars}

    def unexpected(*args, **kwargs):
        pytest.fail("Pillow mask or metrics reached")

    monkeypatch.setattr(ImageDraw, "Draw", unexpected)
    monkeypatch.setattr(ImageFont, "truetype", unexpected)
    for ch in chars:
        field, _, bbox, pad = renderer.prepare_tmp_fallback_sdf_character(name, path, ch, style, font_size, 0.25)
        old_field, old_bbox, old_pad = expected[ch]
        assert (bbox, pad) == (old_bbox, old_pad)
        assert field.dtype == np.float32
        assert np.array_equal(field, old_field), ch


def test_fallback_mask_budget_is_checked_before_native_glyph_allocation(renderer, monkeypatch):
    from src.sekai.profile.custom_profile import font_field

    def unexpected(*args, **kwargs):
        pytest.fail("glyph mask allocated before layer limit")

    monkeypatch.setattr(font_field, "basic_text_field", unexpected)
    renderer.max_layer_pixels = 1
    name = "FOT-RodinNTLGPro-EB-OnDemand"
    with pytest.raises(ValueError, match="would allocate"):
        renderer.prepare_tmp_fallback_sdf_character(name, renderer.font_path_for(name), "🙂", STYLE, 90, 0.25)


def test_fallback_fields_execute_in_fresh_interpreter_without_pillow(renderer):
    code = r"""
import sys, json
from pathlib import Path
from scripts.skia_no_pillow import _NoPillow
guard = _NoPillow()
sys.meta_path.insert(0, guard)
import haruki_skia_renderer as native
calls = []
original = native.basic_text_mask
def mask(*args):
    result = original(*args)
    calls.append(args[2])
    return result
native.basic_text_mask = mask
from src.sekai.profile.custom_profile.renderer import PNGRenderer, TextStyle
from src.settings import CUSTOM_PROFILE_ASSETS_DIR, CUSTOM_PROFILE_FONTS_DIR
raw = json.loads(Path(sys.argv[1]).read_text())
r = PNGRenderer(masterdata=None,
    assets=Path(str(CUSTOM_PROFILE_ASSETS_DIR).format(region="cn")),
    fonts=Path(str(CUSTOM_PROFILE_FONTS_DIR).format(region="cn")),
    tmp_font_metadata=Path(sys.argv[2]), resources=raw["resources"])
style = TextStyle("#7030b0", .7, 45, 1, 0, None, 0, 0, None, 0, 0, None, False, False, False, False)
name = "FOT-RodinNTLGPro-EB-OnDemand"
for char in ["🙂", "𠮷", "́", "\u200d", "\t"]:
    field, _, _, _ = r.prepare_tmp_fallback_sdf_character(name, r.font_path_for(name), char, style, 90, .25)
    assert field.dtype.name == "float32"
assert len(calls) == 5
assert not guard.rejected and "PIL" not in sys.modules
print("Five fallback float SDFs built through native masks without Pillow")
"""
    result = subprocess.run(
        [sys.executable, "-X", "gil=0", "-c", code, str(PAYLOAD), str(METADATA)],
        cwd=ROOT,
        text=True,
        capture_output=True,
        timeout=60,
    )
    assert result.returncode == 0, result.stdout + result.stderr
