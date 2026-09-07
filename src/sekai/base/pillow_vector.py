"""Legacy vector adapter. Imported only while replaying a Pillow operation.

Agg retains the old SK chart's raster conventions. No matplotlib objects cross
the shared widget/IR boundary; native rendering never imports this adapter.
"""

import math
from pathlib import Path
import threading

from matplotlib.backends.backend_agg import RendererAgg, get_hinting_flag
from matplotlib.font_manager import FontProperties
from matplotlib.path import Path as MplPath
from matplotlib.transforms import Affine2D
from PIL import Image

from src.settings import FONT_DIR

_lock = threading.RLock()  # Agg's cached font faces are mutable, including on 3.14t.


def _color(value):
    return tuple(v / 255 for v in value) if len(value) == 4 else (*[v / 255 for v in value], 1)


def _compose(image, renderer, origin):
    size = (int(renderer.width), int(renderer.height))
    layer = Image.frombytes("RGBA", size, bytes(renderer.buffer_rgba()))
    image.alpha_composite(layer, origin)


def _bounds(image, points, padding):
    left = max(0, math.floor(min(p[0] for p in points) - padding))
    top = max(0, math.floor(min(p[1] for p in points) - padding))
    right = min(image.width, math.ceil(max(p[0] for p in points) + padding))
    bottom = min(image.height, math.ceil(max(p[1] for p in points) + padding))
    return (left, top, right, bottom) if left < right and top < bottom else None


def draw_path(image, path, pos):
    # Restrict the temporary raster to the control-point hull (plus centered
    # miter/AA reach). Thousands of tiny trace markers must not allocate a full
    # page each. This is a temporary draw surface, not a per-request image cache.
    points = [
        (values[i] + pos[0], values[i + 1] + pos[1]) for _, values in path.commands for i in range(0, len(values), 2)
    ]
    bounds = _bounds(image, points, path.width * 5 + 2) if points else None
    if bounds is None:
        return
    left, top, right, bottom = bounds
    with _lock:
        renderer = RendererAgg(right - left, bottom - top, 72)  # shared widths and sizes are pixels
        gc = renderer.new_gc()
        gc.set_snap(False)
        gc.set_linewidth(path.width)
        gc.set_capstyle("projecting" if path.cap == "square" else path.cap)
        gc.set_joinstyle(path.join)
        gc.set_foreground(_color(path.stroke) if path.stroke is not None else (0, 0, 0, 0))
        if path.dashes:
            gc.set_dashes(path.phase, path.dashes)
        vertices, codes = [], []
        for op, points in path.commands:
            if op == "ellipse":
                x0, y0, x1, y1 = points
                ellipse = MplPath.unit_circle().transformed(
                    Affine2D().scale((x1 - x0) / 2, (y1 - y0) / 2).translate((x0 + x1) / 2, (y0 + y1) / 2)
                )
                vertices.extend(ellipse.vertices)
                codes.extend(ellipse.codes)
                continue
            code = {
                "move": MplPath.MOVETO,
                "line": MplPath.LINETO,
                "quad": MplPath.CURVE3,
                "cubic": MplPath.CURVE4,
                "close": MplPath.CLOSEPOLY,
            }[op]
            if op == "close":
                vertices.append((0, 0))
                codes.append(code)
            else:
                for i in range(0, len(points), 2):
                    vertices.append(points[i : i + 2])
                    codes.append(code)
        if vertices:
            transform = Affine2D().translate(pos[0] - left, pos[1] - top).scale(1, -1).translate(0, bottom - top)
            renderer.draw_path(
                gc, MplPath(vertices, codes), transform, _color(path.fill) if path.fill is not None else None
            )
            _compose(image, renderer, (left, top))


def text_geometry(name, size, text):
    """Fallback hinted outlines when the native metrics extension is unavailable."""
    with _lock:
        renderer = RendererAgg(1, 1, 100)
        path = Path(name)
        if not path.is_absolute():
            path = Path(FONT_DIR) / path
        if not path.suffix:
            path = next((p for ext in (".otf", ".ttf") if (p := path.with_suffix(ext)).is_file()), path)
        prop = FontProperties(fname=str(path), size=size * 0.72)
        width, height, descent = renderer.get_text_width_height_descent(text, prop, False)
        face = renderer._prepare_font(prop)
        units = face.get_sfnt_table("head")["unitsPerEm"]
        table = face.get_sfnt_table("OS/2")
        if table is not None:
            ascent = table["sTypoAscender"] * size / units
            font_descent = -table["sTypoDescender"] * size / units
        else:
            table = face.get_sfnt_table("hhea")
            ascent = table["ascent"] * size / units
            font_descent = -table["descent"] * size / units
        commands = []
        for item in face._layout(text, flags=get_hinting_flag()):
            item.ft_object.load_glyph(item.glyph_index, flags=get_hinting_flag())
            vertices, codes = item.ft_object.get_path()
            i = 0
            while i < len(codes):
                op, count = {1: ("move", 1), 2: ("line", 1), 3: ("quad", 2), 4: ("cubic", 3), 79: ("close", 1)}[
                    codes[i]
                ]
                points = (
                    tuple(value for x, y in vertices[i : i + count] for value in (x + item.x, -y - item.y))
                    if op != "close"
                    else ()
                )
                commands.append((op, points))
                i += count
        return {
            "commands": commands,
            "advance": width,
            "ascent": max(ascent, height - descent),
            "descent": max(font_descent, descent),
        }
