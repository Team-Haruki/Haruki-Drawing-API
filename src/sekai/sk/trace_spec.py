"""Immutable SK chart inputs, independent of Matplotlib and either raster renderer.

Legend order is score series, labelled reference lines, then secondary series.
Keeping score selection, hourly speed windows, reference precedence and labels
here lets both renderers consume the same chart.
"""

from dataclasses import dataclass
from datetime import datetime, timedelta
import math
from typing import Literal

from src.sekai.base.paint_types import lerp_color, rgb_to_color_code
from src.sekai.base.utils import truncate

from .model import PlayerTraceRequest, RankTraceRequest

Axis = Literal["score", "secondary"]
SCORE_COLORS = (
    "#1d4ed8",
    "#dc2626",
    "#7c3aed",
    "#d97706",
    "#0891b2",
    "#c026d3",
    "#15803d",
    "#be123c",
    "#4b5563",
    "#a16207",
)


def event_title(region: str, event_id: int, event_name: str = "") -> str:
    if event_id < 1000:
        return f"【{region.upper()}-{event_id}】{event_name}"
    chapter, event = divmod(event_id, 1000)
    return f"【{region.upper()}-{event}-第{chapter}章单榜】{event_name}"


def score_text(score: int | None, width: int | None = None) -> str:
    if score is None:
        result = "?"
    else:
        value = int(score)
        result = f"{value // 10000}.{value % 10000:04d}w"
    return result.rjust(width) if width else result


@dataclass(frozen=True, slots=True)
class TraceSeries:
    """Marker size is diameter in points, or area in points² for scatter colors."""

    axis: Axis
    times: tuple[datetime, ...]
    values: tuple[float, ...]
    label: str
    color: str
    marker_size: float
    scatter_colors: tuple[str, ...] = ()
    dashed: bool = False
    alpha: float = 1.0


@dataclass(frozen=True, slots=True)
class TraceAnnotation:
    axis: Axis
    time: datetime
    value: float
    text: str
    color: str
    outline: bool = True
    vertical_alignment: Literal["baseline", "bottom"] = "baseline"
    annotation: bool = True  # Matplotlib annotate clips an off-axis anchor; text does not.


@dataclass(frozen=True, slots=True)
class TraceHorizontalLine:
    value: float
    color: str
    style: Literal["--", ":"]
    width: float
    alpha: float = 1.0
    label: str | None = None


@dataclass(frozen=True, slots=True)
class TraceSpec:
    title: str
    start: datetime
    end: datetime
    series: tuple[TraceSeries, ...]
    annotations: tuple[TraceAnnotation, ...]
    horizontal_lines: tuple[TraceHorizontalLine, ...]
    score_limits: tuple[float, float] | None
    score_format: Literal["board", "scalar"]
    secondary_limits: tuple[float, float]
    secondary_kind: Literal["rank", "speed"]
    exact_time_limits: bool
    score_grid: bool


def day_night_spans(start: datetime, end: datetime) -> tuple[tuple[datetime, datetime, str], ...]:
    time = start.replace(minute=0, second=0, microsecond=0)
    spans = []
    while time < end:
        stop = min(time + timedelta(hours=1), end)
        ratio = math.sin(time.hour / 24 * math.pi * 2 - math.pi / 2)
        color = rgb_to_color_code(lerp_color((200, 200, 230), (245, 245, 250), (ratio + 1) / 2))
        spans.append((time, stop, color))
        time += timedelta(hours=1)
    return tuple(spans)


def build_player_trace_spec(request: PlayerTraceRequest) -> TraceSpec:
    primary = sorted((r for r in request.ranks if r.rank <= 100), key=lambda r: r.time)
    if not primary:
        raise ValueError("player trace requires at least one rank entry within top 100")
    secondary = sorted((r for r in request.ranks2 or () if r.rank <= 100), key=lambda r: r.time)
    reference = sorted((r for r in request.compare_rank_trace or () if r.score is not None), key=lambda r: r.time)
    compare_rank = request.compare_rank or (reference[-1].rank if reference else None)
    line_score = request.compare_rank_line_score
    line_time = None
    if request.compare_rank_latest is not None:
        if line_score is None:
            line_score = request.compare_rank_latest.score
        line_time = request.compare_rank_latest.time
    if reference:
        if line_score is None:
            line_score = reference[-1].score
        if line_time is None:
            line_time = reference[-1].time
    if line_time is None:
        line_time = primary[-1].time

    all_rows = primary + secondary + reference
    min_score = min(r.score for r in all_rows)
    max_score = max(r.score for r in all_rows)
    if line_score is not None and not reference:
        min_score, max_score = min(min_score, line_score), max(max_score, line_score)
    series = []
    annotations = []
    lines = []
    players = [(primary, "royalblue", "cornflowerblue")]
    if secondary:
        players.append((secondary, "orangered", "coral"))
    names = [truncate(rows[-1].name, 40) for rows, _, _ in players]
    for name, (rows, color, _) in zip(names, players):
        series.append(
            TraceSeries("score", tuple(r.time for r in rows), tuple(r.score for r in rows), name + "分数", color, 1)
        )
        last = rows[-1]
        annotations.append(TraceAnnotation("score", last.time, last.score, score_text(last.score), color))
    if reference:
        label = f"T{compare_rank}分数线" if compare_rank else "参考分数线"
        series.append(
            TraceSeries(
                "score",
                tuple(r.time for r in reference),
                tuple(r.score for r in reference),
                label,
                "dimgray",
                1,
                dashed=True,
                alpha=0.85,
            )
        )
        last = reference[-1]
        annotations.append(
            TraceAnnotation("score", last.time, last.score, f"{label} {score_text(last.score)}", "dimgray")
        )
    elif line_score is not None:
        label = f"T{compare_rank}当前" if compare_rank else "参考当前"
        lines.append(TraceHorizontalLine(line_score, "gray", ":", 0.8, 0.9, label))
        annotations.append(
            TraceAnnotation(
                "score",
                line_time,
                line_score,
                f"{label}: {score_text(line_score)}",
                "gray",
                vertical_alignment="bottom",
                annotation=False,
            )
        )
    for name, (rows, _, color) in zip(names, players):
        series.append(
            TraceSeries(
                "secondary", tuple(r.time for r in rows), tuple(r.rank for r in rows), name + "排名", color, 0.7
            )
        )
        last = rows[-1]
        annotations.append(TraceAnnotation("secondary", last.time, last.rank * 1.02, str(int(last.rank)), color))
    return TraceSpec(
        title=f"{event_title(request.region, request.event_id)} 玩家: {' vs '.join(names)}",
        start=min(r.time for r in all_rows),
        end=max(r.time for r in all_rows),
        series=tuple(series),
        annotations=tuple(annotations),
        horizontal_lines=tuple(lines),
        score_limits=(min_score * 0.95, max_score * 1.05),
        score_format="board",
        secondary_limits=(110, -10),
        secondary_kind="rank",
        exact_time_limits=True,
        score_grid=True,
    )


def build_rank_trace_spec(request: RankTraceRequest) -> TraceSpec:
    rows = sorted(request.ranks, key=lambda r: r.time)
    if not rows:
        raise ValueError("ranks must not be empty")
    times = tuple(r.time for r in rows)
    scores = tuple(r.score for r in rows)
    speeds = []
    left = 0
    for right, row in enumerate(rows):
        while row.time - rows[left].time > timedelta(minutes=60):
            left += 1
        period = (row.time - rows[left].time).total_seconds()
        speeds.append((row.score - rows[left].score) / period * 3600 if 3000 <= period <= 3600 else -1)
    unique_names = tuple(dict.fromkeys(r.name for r in rows))
    colors = dict(zip(unique_names, SCORE_COLORS)) if len(unique_names) <= len(SCORE_COLORS) else {}
    point_colors = tuple(colors.get(r.name, SCORE_COLORS[0]) for r in rows)
    series = (
        TraceSeries("score", times, scores, "分数线", SCORE_COLORS[0], 3, scatter_colors=point_colors),
        TraceSeries("secondary", times, tuple(speeds), "时速", "green", 0.5),
    )
    last = rows[-1]
    annotations = [TraceAnnotation("score", last.time, last.score, score_text(last.score), point_colors[-1])]
    lines = []
    if request.predict_ranks is not None and request.predict_ranks.score is not None:
        score = request.predict_ranks.score
        lines.append(TraceHorizontalLine(score, "red", "--", 0.5))
        annotations.append(
            TraceAnnotation(
                "score",
                last.time,
                score * 1.02,
                f"预测最终: {score_text(score)}",
                "red",
                outline=False,
                annotation=False,
            )
        )
    valid_speeds = [v for v in speeds if v >= 0]
    return TraceSpec(
        title=f"{event_title(request.region, request.event_id)} T{request.target_rank} 分数线",
        start=times[0],
        end=times[-1],
        series=series,
        annotations=tuple(annotations),
        horizontal_lines=tuple(lines),
        score_limits=None,
        score_format="scalar",
        secondary_limits=(0, (max(valid_speeds) if valid_speeds else 1) * 1.2),
        secondary_kind="speed",
        exact_time_limits=False,
        score_grid=False,
    )
