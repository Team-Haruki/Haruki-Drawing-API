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
from pathlib import Path

import numpy as np
from PIL import Image
import pytest

try:
    import haruki_skia_renderer as _native
except ImportError:  # pragma: no cover - extension not built
    _native = None

from src.sekai.base.painter import LinearGradient, Painter, get_font
from src.settings import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT, DEFAULT_FONT, FONT_DIR

pytestmark = pytest.mark.skipif(_native is None, reason="haruki_skia_renderer not built")

ARTIFACT_DIR = Path("out/skia-parity")


def _scene(node: dict, w: int, h: int) -> dict:
    return {
        "version": 2,
        "assets_base_dir": str(ASSETS_BASE_DIR),
        "export_format": "png",
        "fonts": {"dir": str(FONT_DIR), "default": DEFAULT_FONT, "bold": DEFAULT_BOLD_FONT},
        "canvas": {"width": w, "height": h},
        "root": {"type": "Group", "offset": [0, 0], "size": [w, h], "children": [node]},
    }


def _render_skia(node: dict, w: int, h: int) -> Image.Image:
    out = _native.render_scene(json.dumps(_scene(node, w, h)).encode("utf-8"))
    return Image.open(BytesIO(out["image_bytes"])).convert("RGBA")


def _render_pillow(build, w: int, h: int) -> Image.Image:
    painter = Painter(size=(w, h))
    build(painter)
    img = Painter._execute(painter.operations, None, painter.size, {})
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
        "check": ("iou", 0.97),
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
        "check": ("iou", 0.97),
    },
    {
        "name": "text_cjk_top",
        "build": lambda p: p.text("提示Aa", (10, 14), get_font(DEFAULT_FONT, 28), (20, 20, 40, 255)),
        "node": {"type": "Text", "text": "提示Aa", "pos": [10, 14], "font": {"role": "default", "size": 28},
                 "align": "left", "baseline": "cjk_top", "fill": [20, 20, 40, 255]},
        "size": (140, 56),
        "check": ("bbox", 6),
    },
]


@pytest.mark.parametrize("case", _CASES, ids=[c["name"] for c in _CASES])
def test_node_parity(case):
    name = case["name"]
    w, h = case["size"]
    ref = _render_pillow(case["build"], w, h)
    act = _render_skia(case["node"], w, h)

    # Hard: exact size parity (Python integer layout is authoritative).
    assert ref.size == act.size == (w, h), f"{name}: size {ref.size} vs {act.size}"

    mode, bound = case["check"]
    if mode == "iou":
        iou = _alpha_iou(ref, act)
        if iou < bound:
            out_dir = _save_triptych(name, ref, act)
            pytest.fail(f"{name}: alpha IoU {iou:.3f} < {bound} (artifacts: {out_dir})")
    else:  # bbox tolerance (px) on the ink bounding box
        rb, ab = _ink_bbox(ref), _ink_bbox(act)
        assert rb is not None, f"{name}: empty reference ink"
        assert ab is not None, f"{name}: empty actual ink"
        deltas = [abs(r - a) for r, a in zip(rb, ab, strict=True)]
        if max(deltas) > bound:
            out_dir = _save_triptych(name, ref, act)
            pytest.fail(f"{name}: ink bbox delta {deltas} > {bound}px (ref={rb} act={ab}, artifacts: {out_dir})")
