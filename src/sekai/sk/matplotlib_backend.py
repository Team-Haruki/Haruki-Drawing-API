"""Legacy plot backend, imported only by player/rank trace pixel composers."""

from datetime import datetime, timedelta
import math

import matplotlib
from matplotlib import dates as mdates, font_manager
from matplotlib.figure import Figure
import matplotlib.patheffects as patheffects
from matplotlib.ticker import FixedFormatter, FixedLocator, FuncFormatter

from src.sekai.base.paint_types import lerp_color, rgb_to_color_code
from src.settings import ASSETS_BASE_DIR, DEFAULT_FONT

matplotlib.use("Agg")

# matplotlib字体
font_paths = []
font_paths.append(ASSETS_BASE_DIR / (DEFAULT_FONT + ".otf"))
font_paths.append(ASSETS_BASE_DIR / (DEFAULT_FONT + ".ttf"))
for path in font_paths:
    try:
        font_manager.fontManager.addfont(path)
        prop = font_manager.FontProperties(fname=path)
        font_name = prop.get_name()
        matplotlib.rcParams["font.family"] = [font_name]
        matplotlib.rcParams["axes.unicode_minus"] = False
    except Exception:
        continue

PLOT_LABEL_PATH_EFFECTS = [patheffects.withStroke(linewidth=2.5, foreground="white", alpha=0.9)]


__all__ = ["PLOT_LABEL_PATH_EFFECTS", "Figure", "FuncFormatter", "mdates"]


def render_trace_figure(spec):
    """Legacy adapter for the renderer-neutral trace specification."""
    from src.sekai.base.utils import plt_fig_to_image

    from .trace_axes import build_trace_axes
    from .trace_spec import day_night_spans

    layout = build_trace_axes(spec)
    fig = Figure(figsize=(12, 8))
    ax = fig.add_subplot(111)
    try:
        fig.subplots_adjust(wspace=0, hspace=0)
        for start, end, color in day_night_spans(spec.start, spec.end):
            ax.axvspan(start, end, facecolor=color, edgecolor=None, zorder=0)
        axes = {"score": ax}
        lines = []

        def draw_series(series):
            axis = axes[series.axis]
            if series.scatter_colors:
                return axis.scatter(
                    series.times,
                    series.values,
                    c=series.scatter_colors,
                    s=series.marker_size,
                    label=series.label,
                    zorder=3,
                )
            extra = {"linestyle": "--", "alpha": series.alpha} if series.dashed else {}
            return axis.plot(
                series.times,
                series.values,
                "o",
                label=series.label,
                color=series.color,
                markersize=series.marker_size,
                linewidth=0.5,
                **extra,
            )[0]

        def draw_annotations(axis_name):
            axis = axes[axis_name]
            for annotation in spec.annotations:
                if annotation.axis != axis_name:
                    continue
                extra = {"path_effects": PLOT_LABEL_PATH_EFFECTS} if annotation.outline else {}
                if annotation.annotation:
                    pos = (annotation.time, annotation.value)
                    axis.annotate(
                        annotation.text, xy=pos, xytext=pos, color=annotation.color, fontsize=12, ha="right", **extra
                    )
                else:
                    axis.text(
                        annotation.time,
                        annotation.value,
                        annotation.text,
                        color=annotation.color,
                        fontsize=12,
                        ha="right",
                        va=annotation.vertical_alignment,
                        **extra,
                    )

        for series in spec.series:
            if series.axis == "score":
                lines.append(draw_series(series))
        for reference in spec.horizontal_lines:
            extra = {"label": reference.label} if reference.label else {}
            line = ax.axhline(
                reference.value,
                color=reference.color,
                linestyle=reference.style,
                linewidth=reference.width,
                alpha=reference.alpha,
                **extra,
            )
            if reference.label:
                lines.append(line)
        draw_annotations("score")
        if spec.score_grid:
            ax.grid(True, linestyle="-", alpha=0.3, color="gray")
        ax2 = axes["secondary"] = ax.twinx()
        for series in spec.series:
            if series.axis == "secondary":
                lines.append(draw_series(series))
        draw_annotations("secondary")
        ax.set_xlim(*layout.time.limits)
        ax.set_ylim(*layout.score.limits)
        ax2.set_ylim(*layout.secondary.limits)
        for axis, spec_axis in ((ax.xaxis, layout.time), (ax.yaxis, layout.score), (ax2.yaxis, layout.secondary)):
            formatter = FixedFormatter(spec_axis.labels)
            formatter.set_offset_string(spec_axis.offset)
            axis.set_major_locator(FixedLocator(spec_axis.ticks))
            axis.set_major_formatter(formatter)
        fig.autofmt_xdate()
        ax.set_title(spec.title)
        legend = ax2.legend(lines, [line.get_label() for line in lines], loc="upper left")
        legend.set_zorder(1000)
        return plt_fig_to_image(fig)
    finally:
        fig.clear()


def draw_day_night_bg(ax, start_time: datetime, end_time: datetime):
    """
    在 Matplotlib 图表中绘制昼夜交替背景

    白天 (12:00) 偏亮，夜晚 (0:00) 偏暗
    """

    def get_time_bg_color(time: datetime) -> str:
        night_color = (200, 200, 230)  # 0:00
        day_color = (245, 245, 250)  # 12:00
        ratio = math.sin(time.hour / 24 * math.pi * 2 - math.pi / 2)
        color = lerp_color(night_color, day_color, (ratio + 1) / 2)
        return rgb_to_color_code(color)

    interval = timedelta(hours=1)
    start_time = start_time.replace(minute=0, second=0, microsecond=0)
    bg_times = [start_time]
    while bg_times[-1] < end_time:
        bg_times.append(bg_times[-1] + interval)
    bg_colors = [get_time_bg_color(t) for t in bg_times]
    for i in range(len(bg_times)):
        start = bg_times[i]
        end = min(bg_times[i] + interval, end_time)
        if end <= start:
            continue
        ax.axvspan(start, end, facecolor=bg_colors[i], edgecolor=None, zorder=0)
