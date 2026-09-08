"""Immutable vector operations in top-left pixel coordinates.

Paths use nonzero winding, centered strokes and continuous dash phase across
segments. Text anchors its alphabetic baseline; positive angles turn counter-
clockwise around that anchor. Ordinary Painter.text keeps its CJK-top contract.
"""

from dataclasses import dataclass
from functools import cached_property
import math
from typing import Literal

from .paint_types import Color

PathCommand = tuple[str, tuple[float, ...]]


@dataclass(frozen=True, slots=True)
class VectorPath:
    commands: tuple[PathCommand, ...]
    fill: Color | None = None
    stroke: Color | None = None
    width: float = 1
    dashes: tuple[float, ...] = ()
    phase: float = 0
    cap: Literal["butt", "round", "square"] = "butt"
    join: Literal["miter", "round", "bevel"] = "round"

    def __post_init__(self):
        object.__setattr__(self, "commands", tuple((op, tuple(values)) for op, values in self.commands))
        object.__setattr__(self, "dashes", tuple(self.dashes))
        for name in ("fill", "stroke"):
            if (color := getattr(self, name)) is not None:
                object.__setattr__(self, name, tuple(color))
        if len(self.commands) > 16384:
            raise ValueError("vector path exceeds 16384 commands")
        if self.cap not in ("butt", "round", "square") or self.join not in ("miter", "round", "bevel"):
            raise ValueError("invalid vector stroke cap/join")
        if not math.isfinite(self.width) or not 0 <= self.width <= 1000 or not math.isfinite(self.phase):
            raise ValueError("invalid vector stroke width/phase")
        if len(self.dashes) > 32 or len(self.dashes) % 2 or any(not math.isfinite(v) or v < 0.01 for v in self.dashes):
            raise ValueError("vector dashes need an even count of finite lengths >= 0.01")
        point = start = None
        length = 0
        for op, values in self.commands:
            expected = {"move": 2, "line": 2, "quad": 4, "cubic": 6, "close": 0, "ellipse": 4}.get(op)
            if expected is None or len(values) != expected or any(not math.isfinite(v) or abs(v) > 1e7 for v in values):
                raise ValueError("invalid vector command")
            if op == "ellipse":
                x0, y0, x1, y1 = values
                if x1 <= x0 or y1 <= y0:
                    raise ValueError("vector ellipse must have positive dimensions")
                length += 2 * (x1 - x0 + y1 - y0)
                point = start = (x1, (y0 + y1) / 2)
            elif op == "move":
                point = start = values
            elif point is None:
                raise ValueError("vector subpath must start with move")
            elif op == "close":
                length += math.dist(point, start)
                point = start
            else:
                for i in range(0, len(values), 2):
                    target = values[i : i + 2]
                    length += math.dist(point, target)
                    point = target
        if self.dashes and length / sum(self.dashes) * len(self.dashes) > 100000:
            raise ValueError("vector dash subdivision exceeds 100000 segments")


@dataclass(frozen=True)
class VectorText:
    text: str
    font: str
    size: float
    fill: Color = (0, 0, 0, 255)
    align: Literal["left", "center", "right"] = "left"
    angle: float = 0
    stroke: Color | None = None
    stroke_width: float = 0

    @cached_property
    def geometry(self):
        from .vector_font import glyph_run

        return glyph_run(self.font, self.size, self.text)

    def path(self, pos):
        run = self.geometry
        shift = run.advance * {"left": 0, "center": 0.5, "right": 1}[self.align]
        c, s = math.cos(math.radians(self.angle)), math.sin(math.radians(self.angle))
        commands = []
        for op, points in run.commands:
            transformed = []
            for i in range(0, len(points), 2):
                x, y = points[i] - shift, points[i + 1]
                transformed.extend((pos[0] + x * c + y * s, pos[1] - x * s + y * c))
            commands.append((op, tuple(transformed)))
        return VectorPath(tuple(commands), fill=self.fill)

    def __post_init__(self):
        for name in ("fill", "stroke"):
            if (color := getattr(self, name)) is not None:
                object.__setattr__(self, name, tuple(color))
        if self.align not in ("left", "center", "right"):
            raise ValueError("invalid vector text alignment")
        if not math.isfinite(self.size) or not 0 < self.size <= 1000 or not math.isfinite(self.angle):
            raise ValueError("invalid vector text size/angle")
        if not math.isfinite(self.stroke_width) or not 0 <= self.stroke_width <= 1000:
            raise ValueError("invalid vector text outline width")


def polyline(points, **style) -> VectorPath:
    return VectorPath(tuple(("move" if i == 0 else "line", tuple(point)) for i, point in enumerate(points)), **style)


def circle_path(center, radius, **style) -> VectorPath:
    """A closed analytic ellipse; small markers need the renderer's oval coverage."""
    x, y = center
    return VectorPath((("ellipse", (x - radius, y - radius, x + radius, y + radius)),), **style)
