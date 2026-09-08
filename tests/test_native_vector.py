"""Shared vector geometry, clipping, alpha, import isolation and native limits."""

from io import BytesIO
import json
from pathlib import Path
import subprocess
import sys

import numpy as np
from PIL import Image
import pytest

from src.sekai.base.painter import Painter
from src.sekai.base.vector import VectorPath, VectorText, circle_path, polyline
from src.sekai.skia_renderer.canvas import REQUIRED_NATIVE_IR_CAPABILITY
from src.sekai.skia_renderer.ir_painter import IRPainter
from src.settings import DEFAULT_FONT, FONT_DIR

try:
    import haruki_skia_renderer as native
except ImportError:
    native = None

NATIVE = native is not None and native.IR_CAPABILITY >= REQUIRED_NATIVE_IR_CAPABILITY
requires_native = pytest.mark.skipif(not NATIVE, reason="current native vector extension required")
HAS_FONT = any((Path(FONT_DIR) / (DEFAULT_FONT + ext)).is_file() for ext in (".otf", ".ttf", ""))


def _native(size=(200, 100)):
    return IRPainter(
        size, assets_base_dir="data", font_dir=str(FONT_DIR), default_font=DEFAULT_FONT, bold_font=DEFAULT_FONT
    )


def _render(painter):
    if isinstance(painter, Painter):
        return Painter._execute(painter.operations, None, painter.size)
    scene, mem = painter.build_scene()
    return Image.open(BytesIO(native.render_scene(json.dumps(scene).encode(), mem)["image_bytes"])).convert("RGBA")


@requires_native
@pytest.mark.parametrize("cap", ["butt", "round", "square"])
@pytest.mark.parametrize("dashes", [(), (5, 3)])
def test_fractional_curves_and_strokes_match_agg_geometry(cap, dashes):
    path = VectorPath(
        (("move", (20.25, 80.25)), ("cubic", (35, 5, 100, 5, 140, 80)), ("quad", (150, 30, 180, 50))),
        stroke=(40, 100, 200, 255),
        width=1.5,
        dashes=dashes,
        phase=2.5,
        cap=cap,
    )
    p, s = Painter(size=(200, 100)), _native()
    for painter in (p, s):
        painter.rect((0, 0), (200, 100), (210, 210, 230, 255))
        painter.vector_path(path)
    diff = np.abs(np.asarray(_render(p)).astype(float) - np.asarray(_render(s)).astype(float))
    assert diff.mean() < 0.2
    # Skia and Agg use different curve flattening/coverage; retain the subpixel
    # geometry while bounding the observed edge-only AA difference.
    assert np.percentile(diff, 99) <= 4


@requires_native
def test_closed_subpaths_preserve_winding_holes_and_alpha():
    commands = (
        ("move", (20, 20)),
        ("line", (100, 20)),
        ("line", (100, 80)),
        ("line", (20, 80)),
        ("close", ()),
        ("move", (40, 40)),
        ("line", (40, 60)),
        ("line", (80, 60)),
        ("line", (80, 40)),
        ("close", ()),
    )
    for painter in (Painter(size=(200, 100)), _native()):
        painter.vector_path(VectorPath(commands, fill=(200, 40, 100, 128)))
        painter.vector_path(circle_path((100, 50), 15, fill=(40, 100, 200, 128)))
        image = _render(painter)
        assert image.getpixel((60, 50))[3] == 0
        assert image.getpixel((30, 50))[3] == 128
        assert image.getpixel((95, 50))[3] in (191, 192)


@requires_native
def test_region_and_clip_offsets_apply_once():
    images = []
    for painter in (Painter(size=(200, 100)), _native()):
        painter.move_region((30, 20), (120, 60))
        painter.push_clip_roundrect((10, 10), (80, 30), 0)
        painter.vector_path(polyline(((-20, 15), (150, 15)), stroke=(255, 0, 0, 255), width=4), pos=(0, 2))
        painter.pop_clip()
        painter.restore_region()
        images.append(_render(painter))
    for im in images:
        assert im.getbbox() == (40, 35, 120, 39)


@requires_native
@pytest.mark.parametrize(
    ("limits", "error"),
    [
        ({"max_node_pixels": 16}, "vector coverage exceeds node pixel"),
        ({"max_scene_bytes": 30000}, "vector coverage exceeds remaining scene byte"),
    ],
)
def test_vector_coverage_scratch_obeys_scene_limits(limits, error):
    p = _native((80, 60))
    p.vector_path(circle_path((40, 30), 12, fill=(40, 100, 200, 128)))
    scene, mem = p.build_scene()
    scene["limits"] = limits
    with pytest.raises(RuntimeError, match=error):
        native.render_scene(json.dumps(scene).encode(), mem)


@requires_native
def test_vector_coverage_uses_device_transform_and_restores_it():
    p = _native((80, 60))
    p.vector_path(
        VectorPath(
            (("move", (5, 5)), ("line", (25, 5)), ("line", (25, 15)), ("line", (5, 15)), ("close", ())),
            fill=(100, 50, 20, 128),
        )
    )
    scene, mem = p.build_scene()
    node = scene["root"]["children"][0]
    scene["root"]["children"] = [
        {"type": "Transform", "matrix": [0, -1, 60, 1, 0, 10], "children": [node]},
        {"type": "Rect", "pos": [0, 0], "size": [3, 3], "fill": [0, 255, 0, 255]},
    ]
    im = Image.open(BytesIO(native.render_scene(json.dumps(scene).encode(), mem)["image_bytes"]))
    assert im.getpixel((0, 0)) == (0, 255, 0, 255)
    assert im.crop((4, 4, 80, 60)).getbbox() == (41, 11, 51, 31)
    assert im.getpixel((50, 20))[3] == 128


@requires_native
@pytest.mark.parametrize(
    ("change", "error"),
    [
        ({"width": -1}, "stroke width"),
        ({"dashes": [1]}, "dash pattern"),
        ({"dashes": [0, 2]}, "dash pattern"),
        ({"commands": [{"op": "line", "points": [2, 3]}]}, "start with move"),
        ({"commands": [{"op": "move", "points": [1e8, 3]}]}, "coordinate"),
        (
            {
                "commands": [{"op": "move", "points": [0, 0]}, {"op": "line", "points": [1e7, 0]}],
                "dashes": [0.01, 0.01],
            },
            "subdivision",
        ),
        ({"commands": [{"op": "move", "points": [0, 0]}] * 16385}, "command limit"),
    ],
)
def test_raw_native_paths_reject_bad_geometry_before_asset_access(change, error):
    p = _native()
    p.vector_path(polyline(((0, 0), (10, 10)), stroke=(0, 0, 0, 255)))
    scene, _ = p.build_scene()
    path = scene["root"]["children"][0]
    path.update(change)
    scene["root"]["children"].insert(0, {"type": "Image", "path": "missing.png", "pos": [0, 0], "size": [10, 10]})
    with pytest.raises(RuntimeError, match=error):
        native.render_scene(json.dumps(scene).encode(), {})


@requires_native
def test_total_path_commands_are_bounded_across_groups():
    p = _native()
    p.vector_path(VectorPath((("move", (0, 0)),) * 16384))
    scene, _ = p.build_scene()
    path = scene["root"]["children"][0]
    scene["root"]["children"] = [{"type": "Group", "children": [path]} for _ in range(9)]
    with pytest.raises(RuntimeError, match="command limit"):
        native.render_scene(json.dumps(scene).encode(), {})


@requires_native
@pytest.mark.skipif(not HAS_FONT, reason="Source Han fixture font required")
def test_vector_text_rotation_and_outline_preserve_anchor():
    bounds = []
    for angle in (0, 30):
        p = _native()
        p.vector_text(
            VectorText(
                "07-09 20:00", DEFAULT_FONT, 16, align="right", angle=angle, stroke=(255, 255, 255, 255), stroke_width=3
            ),
            (160, 35),
        )
        im = _render(p)
        bounds.append(im.getbbox())
        assert im.getbbox()[2] <= 162
        assert im.getbbox()[0] >= 50
    assert bounds[1][3] > bounds[0][3] + 20  # CCW rotation of a right-anchored run extends down-left.


@requires_native
@pytest.mark.skipif(not HAS_FONT, reason="Source Han fixture font required")
def test_native_vector_render_never_imports_pillow_or_matplotlib():
    code = """
import sys, importlib.abc, json
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in ('PIL', 'matplotlib'):
            raise AssertionError(name)
sys.meta_path.insert(0, Guard())
from src.sekai.base.vector import VectorText, circle_path
from src.sekai.skia_renderer.ir_painter import IRPainter
from src.settings import FONT_DIR, DEFAULT_FONT
import haruki_skia_renderer as native
p = IRPainter((200,100), assets_base_dir='data', font_dir=str(FONT_DIR),
              default_font=DEFAULT_FONT, bold_font=DEFAULT_FONT)
p.vector_path(circle_path((30,30), 10, fill=(10,20,30,255)))
p.vector_text(VectorText('时速 123', DEFAULT_FONT, 16, angle=30, stroke=(255,255,255,255), stroke_width=2), (60,60))
scene, mem = p.build_scene()
result = native.render_scene(json.dumps(scene).encode(), mem)
assert result['image_bytes'].startswith(bytes.fromhex('89504e47'))
assert not any(n.split('.')[0] in ('PIL', 'matplotlib') for n in sys.modules)
"""
    result = subprocess.run([sys.executable, "-X", "gil=0", "-c", code], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


def test_vector_operation_freezes_callers_mutable_data():
    points = [["move", [0, 0]], ["line", [20, 30]]]
    color = [1, 2, 3, 255]
    path = VectorPath(points, stroke=color)
    points[1][1][0] = 999
    color[0] = 255
    assert path.commands[1][1] == (20, 30)
    assert path.stroke == (1, 2, 3, 255)


@requires_native
def test_zero_width_stroke_is_not_a_hairline():
    for painter in (Painter(size=(200, 100)), _native()):
        painter.vector_path(polyline(((20, 30), (180, 30)), stroke=(0, 0, 0, 255), width=0))
        assert _render(painter).getbbox() is None


@pytest.mark.parametrize("kwargs", [{"dashes": (1,)}, {"width": -1}, {"cap": "bad"}])
def test_shared_path_validation_rejects_invalid_styles(kwargs):
    with pytest.raises(ValueError, match="vector"):
        polyline(((0, 0), (10, 10)), **kwargs)
