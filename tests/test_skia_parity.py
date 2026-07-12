"""Per-node Pillow-vs-Skia parity harness (port step ⑥).

Renders a single Render IR v2 node two ways — through the Pillow ``Painter``
(reference) and through the Rust interpreter's ``render_scene`` — then checks:

* **size** — a hard assert (1px drift fails); catches layout/rounding regressions.
* **shape overlap** — alpha-channel IoU above a per-node threshold; robust to the
  anti-aliasing / rasterizer differences that are expected and allowed (constraint
  B: Skia native AA is the fidelity baseline, not pixel-identity).

On any failure the ``expected | actual | diff`` triptych is written to
``out/skia-parity/<case>/`` for inspection. Skips cleanly if the native
extension or fonts are unavailable.
"""

from __future__ import annotations

from io import BytesIO
import json
import os
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

try:
    import haruki_skia_renderer as _native
except ImportError:  # pragma: no cover - extension not built
    _native = None

from src.sekai.base.painter import LinearGradient, Painter, get_font
from src.settings import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT, DEFAULT_EMOJI_FONT, DEFAULT_FONT, FONT_DIR

pytestmark = pytest.mark.skipif(_native is None, reason="haruki_skia_renderer not built")

ARTIFACT_DIR = Path("out/skia-parity")

# Asset used by the Image cases (transparent-edged so alpha IoU is meaningful).
# Skipped if the asset isn't synced locally (e.g. CI without game assets).
_ATTR_REL = "static_images/card/attr_cool.png"
_ATTR_FULL = os.path.join(str(ASSETS_BASE_DIR), _ATTR_REL)
_ATTR_IMG = Image.open(_ATTR_FULL).convert("RGBA") if os.path.exists(_ATTR_FULL) else None


def _scene(nodes: list[dict], w: int, h: int) -> dict:
    return {
        "version": 2,
        "assets_base_dir": str(ASSETS_BASE_DIR),
        "export_format": "png",
        "fonts": {"dir": str(FONT_DIR), "default": DEFAULT_FONT, "bold": DEFAULT_BOLD_FONT},
        "canvas": {"width": w, "height": h},
        "root": {"type": "Group", "offset": [0, 0], "size": [w, h], "children": nodes},
    }


def _render_skia(nodes: list[dict], w: int, h: int) -> Image.Image:
    out = _native.render_scene(json.dumps(_scene(nodes, w, h)).encode("utf-8"))
    return Image.open(BytesIO(out["image_bytes"])).convert("RGBA")


def _render_pillow(build, w: int, h: int) -> Image.Image:
    painter = Painter(size=(w, h))
    build(painter)
    # Mirror Painter.get()'s image marshalling so paste ops carry their image.
    image_dict: dict = {}
    for op in painter.operations:
        op.image_to_id(image_dict)
    img = Painter._execute(painter.operations, None, painter.size, image_dict)
    return img.convert("RGBA")


def _alpha_iou(ref: Image.Image, act: Image.Image, thresh: int = 16) -> float:
    a = np.asarray(ref)[:, :, 3] > thresh
    b = np.asarray(act)[:, :, 3] > thresh
    union = np.logical_or(a, b).sum()
    if union == 0:
        return 1.0
    return float(np.logical_and(a, b).sum()) / float(union)


def _ink_bbox(img: Image.Image, thresh: int = 16) -> tuple[int, int, int, int] | None:
    mask = np.asarray(img)[:, :, 3] > thresh
    if not mask.any():
        return None
    ys, xs = np.where(mask)
    return int(xs.min()), int(ys.min()), int(xs.max()), int(ys.max())


def _mae(ref: Image.Image, act: Image.Image) -> float:
    a = np.asarray(ref).astype(int)
    b = np.asarray(act).astype(int)
    return float(np.abs(a - b).mean())


def _save_triptych(name: str, ref: Image.Image, act: Image.Image) -> Path:
    w, h = ref.size
    diff = Image.fromarray(
        np.clip(np.abs(np.asarray(ref).astype(int) - np.asarray(act).astype(int)) * 3, 0, 255).astype("uint8"),
        "RGBA",
    )
    strip = Image.new("RGBA", (w * 3 + 16, h), (40, 40, 40, 255))
    strip.paste(ref, (0, 0))
    strip.paste(act, (w + 8, 0))
    strip.paste(diff, (w * 2 + 16, 0))
    out_dir = ARTIFACT_DIR / name
    out_dir.mkdir(parents=True, exist_ok=True)
    ref.save(out_dir / "expected.png")
    act.save(out_dir / "actual.png")
    diff.save(out_dir / "diff.png")
    strip.save(out_dir / "side_by_side.png")
    return out_dir


# Each case renders a single node both ways. Fill shapes are checked by alpha IoU;
# text by ink-bounding-box tolerance (IoU is too sensitive to sub-pixel stroke shifts).
_CASES = [
    {
        "name": "rect_solid",
        "build": lambda p: p.rect((20, 20), (80, 60), (220, 40, 60, 255)),
        "node": {"type": "Rect", "pos": [20, 20], "size": [80, 60], "fill": [220, 40, 60, 255]},
        "size": (120, 100),
        "check": ("iou", 0.95),
    },
    {
        "name": "roundrect_solid",
        "build": lambda p: p.roundrect((10, 10), (100, 80), (40, 120, 220, 255), 16),
        "node": {"type": "RoundRect", "pos": [10, 10], "size": [100, 80], "radius": 16, "fill": [40, 120, 220, 255]},
        "size": (120, 100),
        "check": ("iou", 0.95),
    },
    {
        "name": "pieslice_270",
        "build": lambda p: p.pieslice((10, 10), (80, 80), 0, 270, (40, 200, 120, 255)),
        "node": {"type": "PieSlice", "pos": [10, 10], "size": [80, 80], "start_angle": 0, "end_angle": 270,
                 "fill": [40, 200, 120, 255]},
        "size": (100, 100),
        "check": ("iou", 0.95),
    },
    {
        "name": "gradient_rect",
        "build": lambda p: p.rect(
            (10, 10), (100, 80),
            LinearGradient((255, 80, 80, 255), (80, 80, 255, 255), (10, 10), (110, 90)),
        ),
        "node": {"type": "Rect", "pos": [10, 10], "size": [100, 80],
                 "fill": {"kind": "linear", "c1": [255, 80, 80, 255], "c2": [80, 80, 255, 255],
                          "p1": [10, 10], "p2": [110, 90]}},
        "size": (120, 100),
        "check": ("iou", 0.95),
    },
    {
        "name": "text_cjk_top",
        "build": lambda p: p.text("提示Aa", (10, 14), get_font(DEFAULT_FONT, 28), (20, 20, 40, 255)),
        "node": {"type": "Text", "text": "提示Aa", "pos": [10, 14], "font": {"role": "default", "size": 28},
                 "align": "left", "baseline": "cjk_top", "fill": [20, 20, 40, 255]},
        "size": (140, 56),
        "check": ("bbox", 6),
    },
    {
        # Frosted panel over an opaque backdrop; blur of a solid is a solid, so this
        # checks placement + tint + shadow (the blur kernel itself is the loosest part).
        "name": "blurglass_panel",
        "build": lambda p: (
            p.rect((0, 0), (160, 120), (120, 150, 210, 255)),
            p.blurglass_roundrect((24, 20), (112, 80), (255, 255, 255, 80), 16, shadow_alpha=0.26),
        ),
        "nodes": [
            {"type": "Rect", "pos": [0, 0], "size": [160, 120], "fill": [120, 150, 210, 255]},
            {"type": "BlurGlass", "pos": [24, 20], "size": [112, 80], "radius": 16,
             "fill": [255, 255, 255, 80], "shadow_alpha": 0.26},
        ],
        "size": (160, 120),
        "check": ("mae", 8.0),
    },
    {
        # Opaque gradient-tinted frosted panel: the tint covers the backdrop so this checks
        # that a BlurGlass fill accepts a gradient (regression for the music-rewards header
        # that used to TypeError and silently fall back to Pillow). LinearGradient p1/p2 are
        # panel-fractional; the IR node carries the equivalent absolute endpoints.
        "name": "blurglass_gradient",
        "build": lambda p: (
            p.rect((0, 0), (160, 120), (120, 150, 210, 255)),
            p.blurglass_roundrect(
                (24, 20), (112, 80),
                LinearGradient((182, 144, 247, 255), (243, 132, 220, 255), (0, 0), (1, 1)),
                16, shadow_alpha=0.26,
            ),
        ),
        "nodes": [
            {"type": "Rect", "pos": [0, 0], "size": [160, 120], "fill": [120, 150, 210, 255]},
            {"type": "BlurGlass", "pos": [24, 20], "size": [112, 80], "radius": 16,
             "fill": {"kind": "linear", "c1": [182, 144, 247, 255], "c2": [243, 132, 220, 255],
                      "p1": [24, 20], "p2": [136, 100]}, "shadow_alpha": 0.26},
        ],
        "size": (160, 120),
        "check": ("mae", 12.0),
    },
]

# Image cases need a synced game asset; skipped cleanly when it's absent.
if _ATTR_IMG is not None:
    _aw, _ah = _ATTR_IMG.size
    _wfit_h = round(60 * _ah / _aw)
    _CASES += [
        {
            "name": "image_stretch",
            "build": lambda p: p.paste(_ATTR_IMG, (10, 10), (60, 40)),
            "node": {"type": "Image", "pos": [10, 10], "size": [60, 40], "path": _ATTR_REL, "fit": "stretch"},
            "size": (80, 60),
            "check": ("iou", 0.97),
        },
        {
            "name": "image_width",
            "build": lambda p, h=_wfit_h: p.paste(_ATTR_IMG, (10, 10), (60, h)),
            "node": {"type": "Image", "pos": [10, 10], "size": [60, 0], "path": _ATTR_REL, "fit": "width"},
            "size": (80, _wfit_h + 20),
            "check": ("iou", 0.97),
        },
    ]


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_node_parity(case):
    name = case["name"]
    w, h = case["size"]
    nodes = case.get("nodes") or [case["node"]]
    ref = _render_pillow(case["build"], w, h)
    act = _render_skia(nodes, w, h)

    # Hard: exact size parity (Python integer layout is authoritative).
    assert ref.size == act.size == (w, h), f"{name}: size {ref.size} vs {act.size}"

    mode, bound = case["check"]
    if mode == "iou":
        iou = _alpha_iou(ref, act)
        if iou < bound:
            out_dir = _save_triptych(name, ref, act)
            pytest.fail(f"{name}: alpha IoU {iou:.3f} < {bound} (artifacts: {out_dir})")
    elif mode == "mae":  # mean abs error ceiling (rasterizer/blur differences allowed)
        mae = _mae(ref, act)
        if mae > bound:
            out_dir = _save_triptych(name, ref, act)
            pytest.fail(f"{name}: MAE {mae:.2f} > {bound} (artifacts: {out_dir})")
    else:  # bbox tolerance (px) on the ink bounding box
        rb, ab = _ink_bbox(ref), _ink_bbox(act)
        assert rb is not None, f"{name}: empty reference ink"
        assert ab is not None, f"{name}: empty actual ink"
        deltas = [abs(r - a) for r, a in zip(rb, ab, strict=True)]
        if max(deltas) > bound:
            out_dir = _save_triptych(name, ref, act)
            pytest.fail(f"{name}: ink bbox delta {deltas} > {bound}px (ref={rb} act={ab}, artifacts: {out_dir})")


_EMOJI_FONT = DEFAULT_EMOJI_FONT  # 平台各异:mac=SVGinOT(CoreText),linux=COLR TwemojiMozilla
_EMOJI_FONT_EXISTS = os.path.exists(os.path.join(str(FONT_DIR), _EMOJI_FONT + ".ttf"))


@pytest.mark.skipif(not _EMOJI_FONT_EXISTS, reason="color emoji font not present")
def test_emoji_routes_to_color_emoji_font():
    """An emoji codepoint renders in color only when an emoji font is configured."""

    def render(emoji_font: str | None) -> np.ndarray:
        fonts = {"dir": str(FONT_DIR), "default": DEFAULT_FONT, "bold": DEFAULT_BOLD_FONT}
        if emoji_font:
            fonts["emoji"] = emoji_font
        scene = {
            "version": 2,
            "assets_base_dir": str(ASSETS_BASE_DIR),
            "export_format": "png",
            "fonts": fonts,
            "canvas": {"width": 80, "height": 60},
            "root": {
                "type": "Group", "offset": [0, 0], "size": [80, 60],
                "children": [
                    {"type": "Rect", "pos": [0, 0], "size": [80, 60], "fill": [255, 255, 255, 255]},
                    {"type": "Text", "text": "\U0001F600", "pos": [20, 8],
                     "font": {"role": "default", "size": 40}, "fill": [0, 0, 0, 255]},
                ],
            },
        }
        res = _native.render_scene(json.dumps(scene).encode())
        return np.asarray(Image.open(BytesIO(res["image_bytes"])).convert("RGB"), dtype=np.int16)

    def colored_pixels(img: np.ndarray) -> int:
        gray = (np.abs(img[:, :, 0] - img[:, :, 1]) < 12) & (np.abs(img[:, :, 1] - img[:, :, 2]) < 12)
        return int((~gray).sum())

    without = colored_pixels(render(None))
    with_font = colored_pixels(render(_EMOJI_FONT))
    assert without == 0
    assert with_font > 100
