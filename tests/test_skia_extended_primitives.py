"""Functional checks for the reserve primitives added ahead of need: separate-method
gradients, pixelwise adaptive text, mix tint alpha preservation, and per-corner /
shadow-width blur glass. No endpoint calls these yet; the tests pin the semantics."""

from __future__ import annotations

from io import BytesIO
import json

import numpy as np
from PIL import Image
import pytest

try:
    import haruki_skia_renderer as _native
except ImportError:  # pragma: no cover - extension not built
    _native = None

from src.settings import DEFAULT_BOLD_FONT, DEFAULT_FONT, FONT_DIR

pytestmark = pytest.mark.skipif(_native is None, reason="haruki_skia_renderer not built")

W, H = 200, 120


def _render(children: list[dict], mem: dict | None = None, size=(W, H)) -> Image.Image:
    scene = {
        "version": 2,
        "assets_base_dir": "data",
        "export_format": "png",
        "jpg_quality": 90,
        "fonts": {"dir": str(FONT_DIR), "default": DEFAULT_FONT, "bold": DEFAULT_BOLD_FONT},
        "canvas": {"width": size[0], "height": size[1]},
        "root": {"type": "Group", "offset": [0, 0], "size": list(size), "children": children},
    }
    result = _native.render_scene(json.dumps(scene).encode(), mem)
    return Image.open(BytesIO(result["image_bytes"])).convert("RGBA")


def test_separate_gradient_matches_painter_field():
    from src.sekai.base.painter import LinearGradient

    p1, p2 = (0.1, 0.2), (0.9, 0.8)
    c1, c2 = (255, 0, 0, 255), (0, 0, 255, 255)
    img = _render([
        {"type": "Rect", "pos": [0, 0], "size": [W, H],
         "fill": {"kind": "linear", "c1": list(c1), "c2": list(c2), "method": "separate",
                  "p1": [p1[0] * W, p1[1] * H], "p2": [p2[0] * W, p2[1] * H]}},
    ])
    expected = LinearGradient(c1, c2, p1, p2, method="separate").get_colors((W, H))
    got = np.asarray(img)[:, :, :3].astype(np.float32)
    diff = np.abs(got - expected[:, :, :3].astype(np.float32))
    assert diff.mean() < 3.0, f"separate gradient field diverges (mean {diff.mean():.2f})"
    # And it must differ from combine for a diagonal gradient (that was the old behavior).
    combine = LinearGradient(c1, c2, p1, p2, method="combine").get_colors((W, H))
    assert np.abs(expected.astype(np.float32) - combine.astype(np.float32)).mean() > 1.0


def test_pixelwise_adaptive_text_splits_by_backdrop():
    text_node = {
        "type": "Text", "text": "MMMMMMMM", "pos": [18, 40],
        "font": {"role": "bold", "size": 34}, "align": "left", "baseline": "cjk_top",
        "fill": [255, 0, 0, 255],
        "adaptive": {"light": [255, 255, 255, 255], "dark": [0, 0, 0, 255],
                     "threshold": 0.4, "pixelwise": True},
    }
    img = np.asarray(_render([
        {"type": "Rect", "pos": [0, 0], "size": [W / 2, H], "fill": [10, 10, 10, 255]},
        {"type": "Rect", "pos": [W / 2, 0], "size": [W / 2, H], "fill": [245, 245, 245, 255]},
        text_node,
    ]))
    left, right = img[:, : W // 2 - 12], img[:, W // 2 + 12 :]
    # Glyph ink on the dark half must be light pixels, and vice versa.
    assert (left[:, :, :3].max(axis=2) > 200).sum() > 50, "no light glyphs over the dark half"
    assert (right[:, :, :3].min(axis=2) < 60).sum() > 50, "no dark glyphs over the bright half"
    # Sanity: the non-pixelwise average mode picks ONE color for the whole run — the
    # mixed backdrop averages bright (> 0.4), so the dark color wins and the dark half
    # gets NO light glyphs (unlike pixelwise, which put light glyphs there above).
    text_avg = {**text_node, "adaptive": {**text_node["adaptive"], "pixelwise": False}}
    img_avg = np.asarray(_render([
        {"type": "Rect", "pos": [0, 0], "size": [W / 2, H], "fill": [10, 10, 10, 255]},
        {"type": "Rect", "pos": [W / 2, 0], "size": [W / 2, H], "fill": [245, 245, 245, 255]},
        text_avg,
    ]))
    left_avg = img_avg[:, : W // 2 - 12]
    assert (left_avg[:, :, :3].min(axis=2) > 200).sum() < 20, "avg mode should not split per pixel"


def test_mix_tint_preserves_alpha():
    # Left half opaque red, right half fully transparent.
    src = Image.new("RGBA", (40, 20), (0, 0, 0, 0))
    src.paste(Image.new("RGBA", (20, 20), (255, 0, 0, 255)), (0, 0))
    img = _render(
        [
            {"type": "Image", "path": "mem:m0", "pos": [0, 0], "size": [40, 20], "fit": "stretch",
             "tint": {"color": [0, 0, 255, 255], "mode": "mix", "strength": 0.5}},
        ],
        mem={"m0": (40, 20, src.tobytes())},
        size=(40, 20),
    )
    px = np.asarray(img)
    opaque = px[10, 5]
    transparent = px[10, 30]
    assert transparent[3] == 0, f"mix tint colored a fully transparent pixel: {transparent}"
    # RGB' = lerp(red, blue, 0.5) = (127.5, 0, 127.5) within rounding.
    assert abs(int(opaque[0]) - 128) <= 3
    assert abs(int(opaque[2]) - 128) <= 3
    assert opaque[3] == 255


def test_blurglass_corners_and_shadow_width():
    def glass(corners, shadow_width=6.0):
        return _render([
            {"type": "Rect", "pos": [0, 0], "size": [W, H], "fill": [40, 160, 40, 255]},
            {"type": "BlurGlass", "pos": [30, 20], "size": [140, 80], "radius": 18,
             "fill": [255, 255, 255, 255], "shadow_alpha": 0.5,
             "corners": corners, "shadow_width": shadow_width},
        ])

    # UL corner disabled -> square (panel white covers the corner); UR enabled -> rounded
    # (background green shows at the corner point).
    img = np.asarray(glass([False, True, True, True]))
    ul = img[22, 32][:3]
    ur = img[22, 167][:3]
    assert ul.min() > 200, f"disabled UL corner should be square/white, got {ul}"
    assert ur[1] > ur[0] + 30, f"enabled UR corner should show green backdrop, got {ur}"

    # A wider shadow_width must darken the backdrop farther from the panel edge.
    near_default = np.asarray(glass([True] * 4, shadow_width=6.0))[70, 22][:3]
    near_wide = np.asarray(glass([True] * 4, shadow_width=16.0))[70, 22][:3]
    assert int(near_wide.sum()) < int(near_default.sum()), (
        f"shadow_width=16 should reach x=22 darker than width=6: {near_wide} vs {near_default}"
    )
