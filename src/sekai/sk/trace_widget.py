"""SK curves as a shared vector widget tree, with no pre-rendered plot bitmap."""

from itertools import pairwise
import math

from src.sekai.base.plot import Canvas, Widget
from src.sekai.base.vector import VectorPath, VectorText, circle_path, polyline
from src.settings import DEFAULT_FONT

from .trace_axes import build_trace_axes, date_number
from .trace_spec import TraceSpec, day_night_spans

PT = 100 / 72
BLACK = (0, 0, 0, 255)
COLORS = {
    "royalblue": "4169e1",
    "cornflowerblue": "6495ed",
    "orangered": "ff4500",
    "coral": "ff7f50",
    "dimgray": "696969",
    "gray": "808080",
    "green": "008000",
    "red": "ff0000",
}


def _color(value, alpha=1):
    value = COLORS.get(value, value.removeprefix("#"))
    return (*[int(value[i : i + 2], 16) for i in (0, 2, 4)], round(alpha * 255))


def _rectangle(x, y, width, height, **style):
    return VectorPath(
        (
            ("move", (x, y)),
            ("line", (x + width, y)),
            ("line", (x + width, y + height)),
            ("line", (x, y + height)),
            ("close", ()),
        ),
        **style,
    )


def _rounded_rectangle(x, y, w, h, r, **style):
    k = r * 0.5522847498307936
    return VectorPath(
        (
            ("move", (x + r, y)),
            ("line", (x + w - r, y)),
            ("cubic", (x + w - r + k, y, x + w, y + r - k, x + w, y + r)),
            ("line", (x + w, y + h - r)),
            ("cubic", (x + w, y + h - r + k, x + w - r + k, y + h, x + w - r, y + h)),
            ("line", (x + r, y + h)),
            ("cubic", (x + r - k, y + h, x, y + h - r + k, x, y + h - r)),
            ("line", (x, y + r)),
            ("cubic", (x, y + r - k, x + r - k, y, x + r, y)),
            ("close", ()),
        ),
        **style,
    )


class TracePlotBox(Widget):
    def __init__(self, spec: TraceSpec):
        super().__init__()
        self.spec = spec
        self.axes = build_trace_axes(spec)

    def _get_content_size(self):
        return self.axes.size

    def _text(self, p, text, pos, size=10, *, color=BLACK, align="left", vertical="baseline", angle=0, outline=False):
        run = VectorText(
            text,
            DEFAULT_FONT,
            size * PT,
            color,
            align=align,
            angle=angle,
            stroke=(255, 255, 255, 230) if outline else None,
            stroke_width=2.5 * PT if outline else 0,
        )
        x, y = pos
        if angle:
            metrics = run.geometry
            c, s = math.cos(math.radians(angle)), math.sin(math.radians(angle))
            corners = [
                (dx * c + dy * s, -dx * s + dy * c)
                for dx in (0, metrics.advance)
                for dy in (-metrics.ascent, metrics.descent)
            ]
            # Default rotated bounding-box alignment used by the old x ticks.
            x -= max(v[0] for v in corners)
            y -= min(v[1] for v in corners)
            run = VectorText(text, DEFAULT_FONT, size * PT, color, angle=angle)
        elif vertical == "center_baseline":
            y += run.geometry.ascent / 2
        elif vertical == "bottom":
            y -= run.geometry.descent
        p.vector_text(run, (x, y))

    @staticmethod
    def _line(p, points, color=BLACK, width=0.8 * PT, style=None):
        if len(points) >= 2 and all(a[0] == b[0] or a[1] == b[1] for a, b in pairwise(points)):
            # Agg snaps axis-aligned strokes to device pixel centers. Preserve
            # this in the shared geometry instead of relying on backend defaults.
            points = [(math.floor(x + 0.5) + 0.5, math.floor(y + 0.5) + 0.5) for x, y in points]
        dashes = (3.7 * width, 1.6 * width) if style == "--" else (width, 1.65 * width) if style == ":" else ()
        p.vector_path(polyline(points, stroke=color, width=width, dashes=dashes))

    def _x(self, value):
        left, _, right, _ = self.axes.bounds
        return left + self.axes.time.fraction(value) * (right - left)

    def _y(self, value, axis="score"):
        _, top, _, bottom = self.axes.bounds
        scale = self.axes.score if axis == "score" else self.axes.secondary
        return bottom - scale.fraction(value) * (bottom - top)

    def _clip(self, p):
        left, top, right, bottom = self.axes.bounds
        p.push_clip_roundrect((left, top), (right - left, bottom - top), 0)

    def _series(self, p, series):
        points = [self.axes.point(t, v, series.axis) for t, v in zip(series.times, series.values)]
        color = _color(series.color, series.alpha)
        self._clip(p)
        if series.dashed:
            self._line(p, points, color, 0.5 * PT, "--")
        radius = math.sqrt(series.marker_size) * PT / 2 if series.scatter_colors else series.marker_size * PT / 2
        for i, point in enumerate(points):
            marker_color = _color(series.scatter_colors[i], series.alpha) if series.scatter_colors else color
            if not series.scatter_colors:
                point = tuple(math.floor(v + 0.5) + 0.5 for v in point)
            p.vector_path(circle_path(point, radius, fill=marker_color, stroke=marker_color, width=PT))
        p.pop_clip()

    def _annotations(self, p, axis):
        left, top, right, bottom = self.axes.bounds
        for annotation in self.spec.annotations:
            if annotation.axis != axis:
                continue
            pos = self.axes.point(annotation.time, annotation.value, axis)
            if annotation.annotation and not (left <= pos[0] <= right and top <= pos[1] <= bottom):
                continue
            self._text(
                p,
                annotation.text,
                pos,
                12,
                color=_color(annotation.color),
                align="right",
                vertical=annotation.vertical_alignment,
                outline=annotation.outline,
            )

    def _ticks(self, p, axis):
        left, top, right, bottom = self.axes.bounds
        size, pad = 3.5 * PT, 7 * PT
        if axis == "score":
            for value, label in zip(self.axes.time.ticks, self.axes.time.labels):
                x = self._x(value)
                if not left <= x <= right:
                    continue
                self._line(p, ((x, bottom), (x, bottom + size)))
                self._text(p, label, (x, bottom + pad), align="right", angle=30)
        scale = self.axes.score if axis == "score" else self.axes.secondary
        for value, label in zip(scale.ticks, scale.labels):
            y = self._y(value, axis)
            if not top <= y <= bottom:
                continue
            x = left if axis == "score" else right
            direction = -1 if axis == "score" else 1
            self._line(p, ((x, y), (x + direction * size, y)))
            if label:
                self._text(
                    p,
                    label,
                    (x + direction * pad, y),
                    align="right" if axis == "score" else "left",
                    vertical="center_baseline",
                )
        if scale.offset:
            self._text(p, scale.offset, (left, top - 3 * PT), vertical="bottom")

    def _spines(self, p):
        left, top, right, bottom = self.axes.bounds
        for points in (
            ((left, top), (left, bottom)),
            ((right, top), (right, bottom)),
            ((left, top), (right, top)),
            ((left, bottom), (right, bottom)),
        ):
            self._line(p, points)

    def _legend(self, p):
        entries = [(s.label, s) for s in self.spec.series if s.axis == "score"]
        entries += [(line.label, line) for line in self.spec.horizontal_lines if line.label]
        entries += [(s.label, s) for s in self.spec.series if s.axis == "secondary"]
        texts = [VectorText(label, DEFAULT_FONT, 10 * PT) for label, _ in entries]
        metrics = [t.geometry for t in texts]
        pad, sep, handle, gap = 4 * PT, 5 * PT, 20 * PT, 8 * PT
        left, top = self.axes.bounds[:2]
        x, y = left + 5 * PT, top + 5 * PT
        width = max(m.advance for m in metrics) + 2 * pad + handle + gap
        height = sum(m.ascent + m.descent for m in metrics) + (len(metrics) - 1) * sep + 2 * pad
        p.vector_path(
            _rounded_rectangle(
                x, y, width, height, 2 * PT, fill=(255, 255, 255, 204), stroke=(204, 204, 204, 204), width=PT
            )
        )
        y += pad
        for text, metric, (_, entry) in zip(texts, metrics, entries):
            baseline = y + metric.ascent
            p.vector_text(text, (x + pad + handle + gap, baseline))
            color = _color(entry.color, entry.alpha)
            hy = baseline - 3.5 * PT
            if hasattr(entry, "marker_size"):
                if entry.dashed:
                    self._line(p, ((x + pad, hy), (x + pad + handle, hy)), color, 0.5 * PT, "--")
                radius = math.sqrt(entry.marker_size) * PT / 2 if entry.scatter_colors else entry.marker_size * PT / 2
                if entry.scatter_colors:
                    hy = baseline - 7 * PT * 0.375
                    color = _color(entry.scatter_colors[0], entry.alpha)
                point = (x + pad + handle / 2, hy)
                if not entry.scatter_colors:
                    point = tuple(math.floor(v + 0.5) + 0.5 for v in point)
                p.vector_path(circle_path(point, radius, fill=color, stroke=color, width=PT))
            else:
                self._line(p, ((x + pad, hy), (x + pad + handle, hy)), color, entry.width * PT, entry.style)
            y += metric.ascent + metric.descent + sep

    def _draw_content(self, p):
        left, top, right, bottom = self.axes.bounds
        self._clip(p)
        for start, end, color in day_night_spans(self.spec.start, self.spec.end):
            x, stop = self._x(date_number(start)), self._x(date_number(end))
            x, stop = math.floor(x + 0.5), math.floor(stop + 0.5)
            p.vector_path(_rectangle(x, top, stop - x, bottom - top, fill=_color(color)))
        if self.spec.score_grid:
            color = _color("gray", 0.3)
            for value in self.axes.time.ticks:
                self._line(p, ((self._x(value), top), (self._x(value), bottom)), color)
            for value in self.axes.score.ticks:
                self._line(p, ((left, self._y(value)), (right, self._y(value))), color)
        p.pop_clip()
        self._ticks(p, "score")
        for series in self.spec.series:
            if series.axis == "score" and not series.scatter_colors:
                self._series(p, series)
        self._clip(p)
        for line in self.spec.horizontal_lines:
            y = self._y(line.value)
            self._line(p, ((left, y), (right, y)), _color(line.color, line.alpha), line.width * PT, line.style)
        p.pop_clip()
        self._spines(p)
        for series in self.spec.series:
            if series.axis == "score" and series.scatter_colors:
                self._series(p, series)
        self._annotations(p, "score")
        self._text(p, self.spec.title, ((left + right) / 2, top - 6 * PT), 12, align="center")
        self._ticks(p, "secondary")
        for series in self.spec.series:
            if series.axis == "secondary":
                self._series(p, series)
        self._spines(p)
        self._annotations(p, "secondary")
        self._legend(p)


def build_trace_plot_canvas(spec: TraceSpec) -> Canvas:
    with Canvas() as canvas:
        TracePlotBox(spec)
    return canvas
