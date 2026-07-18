"""Generate the SdfQuad golden fixtures for the Rust pixel-loop test.

Runs the PYTHON reference (``PNGRenderer.shade_tmp_sdf_field``) over synthetic SDF fields and
dumps (field L-PNG, shading-scalars JSON, expected straight-alpha RGBA PNG) into
``rust/haruki_skia_renderer/tests/fixtures/``. The Rust test re-runs its pixel loop over the
same field + scalars and asserts per-channel |delta| <= 1 (identical formulas; the only slack
is float32 rint boundaries).

Regenerate (and re-run cargo test) whenever the shading math changes:
    uv run python scripts/gen_sdf_quad_golden.py
"""

from __future__ import annotations

import json
from pathlib import Path
import sys

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

import numpy as np
from PIL import Image

from src.sekai.profile.custom_profile.renderer import PNGRenderer, TMPFontAsset
from src.sekai.profile.custom_profile.svg import TextStyle

FIXTURE_DIR = REPO_ROOT / "rust" / "haruki_skia_renderer" / "tests" / "fixtures"


def _asset(**overrides) -> TMPFontAsset:
    base: dict = {
        "name": "golden",
        "bundle": "",
        "source_font_path": None,
        "atlas_paths": [],
        "atlas_population_mode": 1,
        "atlas_width": 0.0,
        "atlas_height": 0.0,
        "atlas_padding": 5.0,
        "point_size": 36.0,
        "face_scale": 1.0,
        "line_height": 0.0,
        "ascent_line": 0.0,
        "descent_line": 0.0,
        "tab_width": 0.0,
        "gradient_scale": 6.0,
        "weight_normal": 0.0,
        "weight_bold": 0.75,
        "face_dilate": 0.12,
        "outline_width": 0.1,
        "outline_softness": 0.08,
        "sharpness": 0.0,
        "normal_spacing_offset": 0.0,
        "bold_spacing": 0.0,
        "scale_ratio_a": 1.0,
        "scale_ratio_b": 1.0,
        "scale_ratio_c": 1.0,
        "glow_offset": 0.0,
        "glow_outer": 0.0,
        "underlay_softness": 0.25,
        "underlay_offset_x": 0.6,
        "underlay_offset_y": -0.6,
        "fallback_names": [],
        "glyphs": {},
    }
    base.update(overrides)
    return TMPFontAsset(**base)


def _disc_field(size: int = 64, radius: float = 20.0, spread: float = 5.9) -> Image.Image:
    ys, xs = np.mgrid[0:size, 0:size].astype(np.float32)
    cx = cy = (size - 1) / 2.0
    signed = radius - np.sqrt((xs - cx) ** 2 + (ys - cy) ** 2)  # positive inside
    field = np.clip(0.5 + signed / (2.0 * spread), 0.0, 1.0)
    return Image.fromarray(np.clip(np.rint(field * 255.0), 0, 255).astype(np.uint8), "L")


def _gradient_field(size: int = 48) -> Image.Image:
    """Covers the full 0..255 field range including the clamp regions."""
    row = np.linspace(0.0, 1.0, size, dtype=np.float32)
    field = np.tile(row, (size, 1))
    return Image.fromarray(np.clip(np.rint(field * 255.0), 0, 255).astype(np.uint8), "L")


def _style(color: str, alpha: float, scale_x: float = 1.0, bold: bool = False) -> TextStyle:
    return TextStyle(
        color=color,
        alpha=alpha,
        size=36.0,
        scale_x=scale_x,
        cspace=0.0,
        mspace=None,
        indent=0.0,
        line_indent=0.0,
        line_height=None,
        rotate=0.0,
        voffset=0.0,
        mark_color=None,
        bold=bold,
        italic=False,
        underline=False,
        strike=False,
    )


def _scalars_json(scalars) -> dict:
    data = {
        "face_scale": scalars.face_scale,
        "face_w": scalars.face_w,
        "alpha": scalars.alpha,
        "face_color": list(scalars.face_color),
    }
    if scalars.underlay is not None:
        u = scalars.underlay
        data["underlay"] = {
            "scale": u.scale,
            "w": u.w,
            "shift": [u.shift_x, u.shift_y],
            "color": list(u.color),
        }
    return data


def generate() -> None:
    FIXTURE_DIR.mkdir(parents=True, exist_ok=True)
    renderer = PNGRenderer.__new__(PNGRenderer)  # shading needs no construction state...
    renderer.tmp_scale_mode = "fx-native"  # ...except the texcoord mode read by tmp_mesh_texcoord1_y

    cases = [
        # (name, field, style, outline_color, outline_dilate)
        ("face_only", _disc_field(), _style("ffcc00", 0.9), "444466", 0.0),
        ("underlay", _disc_field(), _style("ff3366", 1.0, scale_x=1.4), "102040", 0.35),
        ("gradient_bold", _gradient_field(), _style("ffffff", 0.75, bold=True), "000000", 0.2),
    ]
    for name, field_img, style, outline_color, outline_dilate in cases:
        asset = _asset()
        scalars = renderer.tmp_sdf_shading_scalars(asset, style, outline_color, outline_dilate)
        field = np.asarray(field_img, dtype=np.float32) / 255.0
        expected = renderer.shade_tmp_sdf_field(field, asset, style, outline_color, outline_dilate)
        field_img.save(FIXTURE_DIR / f"sdf_quad_{name}_field.png")
        expected.save(FIXTURE_DIR / f"sdf_quad_{name}_expected.png")
        (FIXTURE_DIR / f"sdf_quad_{name}_scalars.json").write_text(
            json.dumps(_scalars_json(scalars), indent=1) + "\n", encoding="utf-8"
        )
        print(f"{name}: field={field_img.size} scalars={_scalars_json(scalars)}")  # noqa: T201


if __name__ == "__main__":
    generate()
