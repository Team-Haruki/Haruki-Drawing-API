"""Python-side Render IR v2 builder (port step ⑤).

A thin builder whose method names mirror ``Painter`` primitives but which emit
declarative IR v2 nodes instead of drawing. Endpoint drawers build a node tree
here; the Rust ``render_scene`` interpreter renders it. This is the contract that
lets the layout live in Python while Rust stays a pure interpreter (constraint A
in ``docs/rust-skia-renderer-migration.md``).

Coordinates are relative to the nearest enclosing ``group``; the interpreter
resolves them to absolute canvas space. Colors are ``(r, g, b, a)`` 0-255.
"""

from __future__ import annotations

from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from typing import Any

Color = Sequence[int]
Vec2 = Sequence[float]
Node = dict[str, Any]


def _color(c: Color) -> list[int]:
    return [int(c[0]), int(c[1]), int(c[2]), int(c[3])]


def _vec(v: Vec2) -> list[float]:
    return [v[0], v[1]]


def _fill_value(value: Color | Node) -> Node | list[int]:
    """A fill/stroke is either a gradient dict (passed through) or an (r,g,b,a) color."""
    return value if isinstance(value, dict) else _color(value)


def _stops(stops: Sequence[tuple[Color, float]]) -> list[Node]:
    return [{"color": _color(c), "pos": float(p)} for c, p in stops]


def image_tint(color: Color, mode: str = "multiply", strength: float = 1.0) -> Node:
    """A color tint for an Image node. ``multiply`` = component-wise multiply;
    ``mix`` = alpha-weighted lerp toward ``color`` by ``strength`` (0..1)."""
    return {"color": _color(color), "mode": mode, "strength": float(strength)}


def image_shadow(alpha: float = 0.6, offset: Vec2 = (6, 6), sigma: float = 3.0,
                 color: Color = (0, 0, 0, 255)) -> Node:
    """A drop shadow derived from an Image node's alpha silhouette."""
    return {"alpha": alpha, "offset": _vec(offset), "sigma": sigma, "color": _color(color)}


def linear_gradient(
    c1: Color | None = None,
    c2: Color | None = None,
    p1: Vec2 = (0, 0),
    p2: Vec2 = (1, 1),
    method: str = "combine",
    stops: Sequence[tuple[Color, float]] | None = None,
) -> Node:
    """A linear-gradient fill usable as the ``fill``/``stroke`` of a shape node.

    Either pass ``c1``/``c2`` for a 2-stop gradient, or ``stops`` for N stops
    (list of ``(color, pos)`` with ``pos`` in 0..1).
    """
    node: Node = {"kind": "linear", "p1": _vec(p1), "p2": _vec(p2), "method": method}
    if stops is not None:
        node["stops"] = _stops(stops)
    else:
        node["c1"] = _color(c1 if c1 is not None else (0, 0, 0, 255))
        node["c2"] = _color(c2 if c2 is not None else (0, 0, 0, 255))
    return node


def radial_gradient(
    c1: Color | None = None,
    c2: Color | None = None,
    center: Vec2 = (0.5, 0.5),
    radius_px: float = 1.0,
    stops: Sequence[tuple[Color, float]] | None = None,
) -> Node:
    """A radial-gradient fill. ``c2`` is the center color, ``c1`` the edge (Painter's
    convention). With ``stops``, stop 0 is the center and stop 1 the edge."""
    node: Node = {"kind": "radial", "center": _vec(center), "radius_px": float(radius_px)}
    if stops is not None:
        node["stops"] = _stops(stops)
    else:
        node["c1"] = _color(c1 if c1 is not None else (0, 0, 0, 255))
        node["c2"] = _color(c2 if c2 is not None else (0, 0, 0, 255))
    return node


class IRBuilder:
    """Accumulates a Render IR v2 scene. See module docstring."""

    def __init__(
        self,
        width: int,
        height: int,
        *,
        assets_base_dir: str,
        font_dir: str,
        default_font: str,
        bold_font: str,
        heavy_font: str | None = None,
        emoji_font: str | None = None,
        export_format: str = "png",
        jpg_quality: int = 90,
    ) -> None:
        self.width = int(width)
        self.height = int(height)
        self._assets_base_dir = str(assets_base_dir)
        self._export_format = export_format
        self._jpg_quality = int(jpg_quality)
        self._fonts: Node = {"dir": str(font_dir), "default": default_font, "bold": bold_font}
        if heavy_font:
            self._fonts["heavy"] = heavy_font
        if emoji_font:
            self._fonts["emoji"] = emoji_font
        self._root_children: list[Node] = []
        self._stack: list[list[Node]] = [self._root_children]
        self._background: Node | None = None

    def _add(self, node: Node) -> Node:
        self._stack[-1].append(node)
        return node

    @contextmanager
    def group(self, offset: Vec2 = (0, 0), size: Vec2 = (0, 0), clip: Node | None = None) -> Iterator[IRBuilder]:
        node: Node = {"type": "Group", "offset": _vec(offset), "size": _vec(size), "children": []}
        if clip is not None:
            node["clip"] = clip
        self._add(node)
        self._stack.append(node["children"])
        try:
            yield self
        finally:
            self._stack.pop()

    def rect(self, pos: Vec2, size: Vec2, fill: Color | Node | None = None, stroke: Color | Node | None = None,
             stroke_width: float = 1) -> Node:
        node: Node = {"type": "Rect", "pos": _vec(pos), "size": _vec(size)}
        if fill is not None:
            node["fill"] = _fill_value(fill)
        if stroke is not None:
            node["stroke"] = _fill_value(stroke)
            node["stroke_width"] = stroke_width
        return self._add(node)

    def roundrect(self, pos: Vec2, size: Vec2, radius: float, fill: Color | Node | None = None,
                  corners: Sequence[bool] = (True, True, True, True), stroke: Color | Node | None = None,
                  stroke_width: float = 1, corner_radii: Sequence[float] | None = None) -> Node:
        node: Node = {"type": "RoundRect", "pos": _vec(pos), "size": _vec(size), "radius": radius,
                      "corners": [bool(c) for c in corners]}
        if corner_radii is not None:
            node["corner_radii"] = [float(r) for r in corner_radii]
        if fill is not None:
            node["fill"] = _fill_value(fill)
        if stroke is not None:
            node["stroke"] = _fill_value(stroke)
            node["stroke_width"] = stroke_width
        return self._add(node)

    def pieslice(self, pos: Vec2, size: Vec2, start_angle: float, end_angle: float,
                 fill: Color | Node | None = None, stroke: Color | Node | None = None,
                 stroke_width: float = 1) -> Node:
        node: Node = {"type": "PieSlice", "pos": _vec(pos), "size": _vec(size),
                      "start_angle": start_angle, "end_angle": end_angle}
        if fill is not None:
            node["fill"] = _fill_value(fill)
        if stroke is not None:
            node["stroke"] = _fill_value(stroke)
            node["stroke_width"] = stroke_width
        return self._add(node)

    def image(self, path: str, pos: Vec2, size: Vec2 = (0, 0), fit: str = "stretch", alpha: float = 1.0,
              anchor: Vec2 = (0, 0), tint: Node | None = None, shadow: Node | None = None) -> Node:
        node: Node = {"type": "Image", "pos": _vec(pos), "size": _vec(size), "path": path,
                      "fit": fit, "alpha": alpha}
        if anchor[0] or anchor[1]:
            node["anchor"] = _vec(anchor)
        if tint is not None:
            node["tint"] = tint
        if shadow is not None:
            node["shadow"] = shadow
        return self._add(node)

    def text(self, text: str, pos: Vec2, role: str, size: float, align: str = "left",
             baseline: str = "cjk_top", fill: Color = (0, 0, 0, 255)) -> Node:
        return self._add({"type": "Text", "text": text, "pos": _vec(pos), "font": {"role": role, "size": size},
                          "align": align, "baseline": baseline, "fill": _color(fill)})

    def shadow(self, pos: Vec2, size: Vec2, radius: float, alpha: float = 0.35, offset: Vec2 = (2, 4),
               sigma: float = 2.5, color: Color = (0, 0, 0, 255)) -> Node:
        return self._add({"type": "Shadow", "pos": _vec(pos), "size": _vec(size), "radius": radius,
                          "alpha": alpha, "offset": _vec(offset), "sigma": sigma, "color": _color(color)})

    def blurglass(self, pos: Vec2, size: Vec2, radius: float, fill: Color, shadow_alpha: float = 0.26) -> Node:
        return self._add({"type": "BlurGlass", "pos": _vec(pos), "size": _vec(size), "radius": radius,
                          "fill": _color(fill), "shadow_alpha": shadow_alpha})

    def triangle_bg(self, hour: float) -> None:
        self._background = {"type": "TriangleBg", "hour": hour}

    def image_bg(self, path: str) -> None:
        self._background = {"type": "ImageBg", "path": path}

    def build(self) -> Node:
        scene: Node = {
            "version": 2,
            "assets_base_dir": self._assets_base_dir,
            "export_format": self._export_format,
            "jpg_quality": self._jpg_quality,
            "fonts": self._fonts,
            "canvas": {"width": self.width, "height": self.height},
            "root": {"type": "Group", "offset": [0, 0], "size": [self.width, self.height],
                     "children": self._root_children},
        }
        if self._background is not None:
            scene["background"] = self._background
        return scene
