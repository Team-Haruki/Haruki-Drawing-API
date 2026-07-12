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
import os
import re
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
    ``mix`` = alpha-weighted lerp toward ``color`` by ``strength`` (0..1);
    ``recolor`` = keep the source alpha as a stencil and replace RGB with ``color``
    (``color``'s own alpha scales the result alpha; 255 keeps the source mask)."""
    return {"color": _color(color), "mode": mode, "strength": float(strength)}


def image_shadow(alpha: float = 0.6, offset: Vec2 = (6, 6), sigma: float = 3.0,
                 color: Color = (0, 0, 0, 255)) -> Node:
    """A drop shadow derived from an Image node's alpha silhouette."""
    return {"alpha": alpha, "offset": _vec(offset), "sigma": sigma, "color": _color(color)}


_COLOR_TAG = re.compile(r"<#([0-9a-fA-F]{6})>|</?>")


def parse_colored_segments(markup: str, default: Color | None = None) -> list[tuple[str, Color | None]]:
    """Parse ``<#rrggbb>...`` inline-color markup into ``(text, color)`` segments. A ``<#hex>``
    tag sets the color for following text; ``<>``/``</>`` resets to ``default``. Feed the result
    to :meth:`IRBuilder.colored_text`."""
    segments: list[tuple[str, Color | None]] = []
    pos = 0
    color: Color | None = default
    for m in _COLOR_TAG.finditer(markup):
        if m.start() > pos:
            segments.append((markup[pos:m.start()], color))
        hexv = m.group(1)
        if hexv:
            color = (int(hexv[0:2], 16), int(hexv[2:4], 16), int(hexv[4:6], 16), 255)
        else:
            color = default
        pos = m.end()
    if pos < len(markup):
        segments.append((markup[pos:], color))
    return [(t, c) for t, c in segments if t]


def text_stroke(color: Color, width: float = 1.0) -> Node:
    """An outline for a Text node, drawn under the fill."""
    return {"color": _color(color), "width": float(width)}


def adaptive_color(light: Color = (255, 255, 255, 255), dark: Color = (0, 0, 0, 255),
                   threshold: float = 0.4) -> Node:
    """Background-adaptive text color: ``light`` over dark backdrops, ``dark`` over bright
    ones (chosen by average luminance vs ``threshold``)."""
    return {"light": _color(light), "dark": _color(dark), "threshold": float(threshold)}


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
        extra_fonts: dict[str, str] | None = None,
        export_format: str = "png",
        jpg_quality: int = 90,
    ) -> None:
        self.width = int(width)
        self.height = int(height)
        self._assets_base_dir = str(assets_base_dir)
        self._export_format = export_format
        self._jpg_quality = int(jpg_quality)
        self._font_dir = str(font_dir)
        self._fonts: Node = {"dir": str(font_dir), "default": default_font, "bold": bold_font}
        if heavy_font:
            self._fonts["heavy"] = heavy_font
        if emoji_font:
            self._fonts["emoji"] = emoji_font
        if extra_fonts:
            self._fonts["extra"] = dict(extra_fonts)
        self._root_children: list[Node] = []
        self._stack: list[list[Node]] = [self._root_children]
        self._background: Node | None = None
        self._pil_font_cache: dict[tuple, Any] = {}

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
              anchor: Vec2 = (0, 0), tint: Node | None = None, shadow: Node | None = None,
              source_rect: tuple[float, float, float, float] | None = None) -> Node:
        node: Node = {"type": "Image", "pos": _vec(pos), "size": _vec(size), "path": path,
                      "fit": fit, "alpha": alpha}
        if anchor[0] or anchor[1]:
            node["anchor"] = _vec(anchor)
        if tint is not None:
            node["tint"] = tint
        if shadow is not None:
            node["shadow"] = shadow
        if source_rect is not None:
            # Source-pixel crop window [x0, y0, x1, y1] applied before the fit (Pillow img.crop).
            node["source_rect"] = [float(v) for v in source_rect]
        return self._add(node)

    # ---- rich-text layout helpers (Python owns wrapping/measuring; emit Text nodes) ----

    def _pil_font(self, role: str, size: float, font_name: str | None = None) -> Any:
        """Load a PIL font for measurement, matching the role/name the renderer will use."""
        from PIL import ImageFont

        key = (role, font_name, round(float(size), 2))
        cached = self._pil_font_cache.get(key)
        if cached is not None:
            return cached
        extra = self._fonts.get("extra", {})
        if font_name and font_name in extra:
            name = extra[font_name]
        elif role == "bold":
            name = self._fonts["bold"]
        elif role == "heavy":
            name = self._fonts.get("heavy") or self._fonts["bold"]
        else:
            name = self._fonts["default"]
        font = None
        for ext in (".otf", ".ttf", ".ttc", ""):
            path = os.path.join(self._font_dir, name + ext)
            if os.path.exists(path):
                try:
                    font = ImageFont.truetype(path, max(1, round(size)))
                except OSError:
                    font = None
                if font is not None:
                    break
        if font is None:
            font = ImageFont.load_default()
        self._pil_font_cache[key] = font
        return font

    def measure_text(self, text: str, role: str, size: float, font_name: str | None = None) -> float:
        """Approximate rendered width (px) of ``text`` (PIL metrics; near-Skia, used for layout)."""
        return float(self._pil_font(role, size, font_name).getlength(text))

    def wrap_text(self, text: str, role: str, size: float, max_width: float,
                  font_name: str | None = None) -> list[str]:
        """Greedy wrap to ``max_width`` (word-aware for Latin, char-wrap for CJK); honors ``\\n``."""
        font = self._pil_font(role, size, font_name)
        lines: list[str] = []
        for para in str(text).split("\n"):
            cur = ""
            last_space = -1
            for ch in para:
                if not cur or font.getlength(cur + ch) <= max_width:
                    if ch == " ":
                        last_space = len(cur)
                    cur += ch
                elif ch == " ":
                    lines.append(cur)
                    cur, last_space = "", -1
                elif 0 <= last_space < len(cur):
                    lines.append(cur[:last_space])
                    cur, last_space = cur[last_space + 1:] + ch, -1
                else:
                    lines.append(cur)
                    cur, last_space = ch, -1
            lines.append(cur)
        return lines

    def multiline_text(self, text: str, pos: Vec2, role: str, size: float, *, max_width: float,
                       line_height: float | None = None, align: str = "left", baseline: str = "cjk_top",
                       fill: Color | Node = (0, 0, 0, 255), font_name: str | None = None,
                       max_lines: int | None = None, ellipsis: str = "…", stroke: Node | None = None,
                       letter_spacing: float = 0.0) -> list[Node]:
        """Wrap ``text`` to ``max_width`` and emit one Text node per line. Truncates with an
        ellipsis past ``max_lines``. Returns the emitted nodes."""
        lines = self.wrap_text(text, role, size, max_width, font_name)
        if max_lines is not None and len(lines) > max_lines:
            lines = lines[:max_lines]
            font = self._pil_font(role, size, font_name)
            last = lines[-1]
            while last and font.getlength(last + ellipsis) > max_width:
                last = last[:-1]
            lines[-1] = last + ellipsis
        lh = line_height if line_height is not None else size * 1.3
        nodes: list[Node] = []
        for i, line in enumerate(lines):
            nodes.append(self.text(line, (pos[0], pos[1] + i * lh), role, size, align=align,
                                   baseline=baseline, fill=fill, stroke=stroke,
                                   letter_spacing=letter_spacing, font_name=font_name))
        return nodes

    def colored_text(self, segments: Sequence[tuple[str, Color | None]], pos: Vec2, role: str, size: float, *,
                     align: str = "left", baseline: str = "cjk_top", default_fill: Color = (0, 0, 0, 255),
                     font_name: str | None = None, stroke: Node | None = None) -> list[Node]:
        """Emit inline multi-color text: ``segments`` are ``(text, color|None)`` drawn left to
        right. ``None`` color uses ``default_fill``. Use :func:`parse_colored_segments` for markup."""
        font = self._pil_font(role, size, font_name)
        widths = [font.getlength(seg[0]) for seg in segments]
        total = sum(widths)
        cx = pos[0]
        if align == "center":
            cx = pos[0] - total / 2
        elif align == "right":
            cx = pos[0] - total
        nodes: list[Node] = []
        for (txt, col), w in zip(segments, widths):
            nodes.append(self.text(txt, (cx, pos[1]), role, size, align="left", baseline=baseline,
                                   fill=col if col is not None else default_fill, stroke=stroke,
                                   font_name=font_name))
            cx += w
        return nodes

    def shadowed_text(self, text: str, pos: Vec2, role: str, size: float, *, shadow_offset: Vec2 = (2, 2),
                      shadow_color: Color = (0, 0, 0, 160), align: str = "left", baseline: str = "cjk_top",
                      fill: Color | Node = (255, 255, 255, 255), font_name: str | None = None) -> list[Node]:
        """Emit a drop-shadowed text as two Text nodes (shadow then fill)."""
        shadow = self.text(text, (pos[0] + shadow_offset[0], pos[1] + shadow_offset[1]), role, size,
                           align=align, baseline=baseline, fill=shadow_color, font_name=font_name)
        top = self.text(text, pos, role, size, align=align, baseline=baseline, fill=fill, font_name=font_name)
        return [shadow, top]

    def text(self, text: str, pos: Vec2, role: str, size: float, align: str = "left",
             baseline: str = "cjk_top", fill: Color | Node = (0, 0, 0, 255), stroke: Node | None = None,
             letter_spacing: float = 0.0, adaptive: Node | None = None, font_name: str | None = None) -> Node:
        font: Node = {"role": role, "size": size}
        if font_name:
            font["name"] = font_name
        node: Node = {"type": "Text", "text": text, "pos": _vec(pos), "font": font,
                      "align": align, "baseline": baseline, "fill": _fill_value(fill)}
        if stroke is not None:
            node["stroke"] = stroke
        if letter_spacing:
            node["letter_spacing"] = float(letter_spacing)
        if adaptive is not None:
            node["adaptive"] = adaptive
        return self._add(node)

    def watermark(self, lines: Sequence[tuple[str, Vec2, str]], role: str, size: float,
                  fill: Color = (255, 255, 255, 255), font_name: str | None = None) -> Node:
        """A multi-line watermark. ``lines`` are ``(text, pos, align)`` (Python owns wrapping
        and auto-sizing; see :meth:`wrap_text`/:meth:`watermark_lines`)."""
        font: Node = {"role": role, "size": size}
        if font_name:
            font["name"] = font_name
        node: Node = {"type": "Watermark", "font": font, "fill": _color(fill),
                      "lines": [{"text": t, "pos": _vec(p), "align": a} for t, p, a in lines]}
        return self._add(node)

    def shadow(self, pos: Vec2, size: Vec2, radius: float, alpha: float = 0.35, offset: Vec2 = (2, 4),
               sigma: float = 2.5, color: Color = (0, 0, 0, 255)) -> Node:
        return self._add({"type": "Shadow", "pos": _vec(pos), "size": _vec(size), "radius": radius,
                          "alpha": alpha, "offset": _vec(offset), "sigma": sigma, "color": _color(color)})

    def blurglass(self, pos: Vec2, size: Vec2, radius: float, fill: Color | Node,
                  shadow_alpha: float = 0.26, blur: float = 4.0) -> Node:
        node: Node = {"type": "BlurGlass", "pos": _vec(pos), "size": _vec(size), "radius": radius,
                      "fill": _fill_value(fill), "shadow_alpha": shadow_alpha}
        if blur != 4.0:
            node["blur"] = float(blur)
        return self._add(node)

    def triangle_bg(self, hour: float = 15.0, time_color: bool = True, main_hue: float = 0.0,
                    size_fixed_rate: float = 0.0) -> None:
        node: Node = {"type": "TriangleBg", "hour": hour}
        if not time_color:
            node["time_color"] = False
            node["main_hue"] = float(main_hue)
        if size_fixed_rate:
            node["size_fixed_rate"] = float(size_fixed_rate)
        self._background = node

    def image_bg(self, path: str, mode: str = "fit", align: str = "c", blur: bool = False,
                 fade: float = 0.0) -> None:
        node: Node = {"type": "ImageBg", "path": path}
        if mode != "fit":
            node["mode"] = mode
        if align != "c":
            node["align"] = align
        if blur:
            node["blur"] = True
        if fade:
            node["fade"] = float(fade)
        self._background = node

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
