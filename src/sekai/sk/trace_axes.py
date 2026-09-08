"""Shared SK trace axis layout, independent of image and font renderers.

The numeric tick/format rules adapt Matplotlib's MaxNLocator/ScalarFormatter,
and the calendar intervals preserve AutoDateLocator's defaults. This freezes
the existing 1200×800 chart contract while allowing a native widget to consume
the same limits and labels as the legacy adapter. Matplotlib's license and
copyright notices are retained in docs/licenses/matplotlib.txt.
"""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import math
import sys

from dateutil.relativedelta import relativedelta
from dateutil.rrule import DAILY, HOURLY, MINUTELY, MONTHLY, SECONDLY, rrule

from .trace_spec import TraceSpec, score_text

SECONDS_PER_DAY = 86400
EPOCH = datetime(1970, 1, 1, tzinfo=UTC)


def date_number(value: datetime) -> float:
    """UTC days from the Unix epoch, matching the existing chart coordinates."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=UTC)
    return (value - EPOCH).total_seconds() / SECONDS_PER_DAY


def nonsingular(limits: tuple[float, float], expansion: float = 0.05, tiny: float = 1e-15) -> tuple[float, float]:
    left, right = sorted(map(float, limits))
    largest = max(abs(left), abs(right))
    if not all(map(math.isfinite, (left, right))) or largest < (1e6 / tiny) * sys.float_info.min:
        return -expansion, expansion
    if right - left <= largest * tiny:
        return left - expansion * abs(left), right + expansion * abs(right)
    return left, right


def numeric_ticks(limits: tuple[float, float]) -> tuple[float, ...]:
    """AutoLocator at the chart's fixed height (nine maximum intervals)."""
    low, high = nonsingular(limits, 1e-13, 1e-14)
    span, midpoint = high - low, (high + low) / 2
    offset = math.copysign(10 ** math.floor(math.log10(abs(midpoint))), midpoint) if abs(midpoint) / span >= 100 else 0
    scale = 10 ** math.floor(math.log10(span / 9))
    lower, upper = low - offset, high - offset
    steps = [factor * scale for factor in (0.1, 0.2, 0.25, 0.5, 1, 2, 2.5, 5, 10, 20)]
    index = next((i for i, step in enumerate(steps) if step >= span / 9), len(steps) - 1)
    for step in reversed(steps[: index + 1]):
        origin = (lower // step) * step
        tolerance = min(0.4999, max(1e-10, abs(offset) / step * 1e-12))
        lo, remainder = divmod(lower - origin, step)
        if abs(remainder / step - 1) < tolerance:
            lo += 1
        hi, remainder = divmod(upper - origin, step)
        if abs(remainder / step) >= tolerance:
            hi += 1
        values = tuple(i * step + origin for i in range(int(lo), int(hi) + 1))
        if sum(lower <= value <= upper for value in values) >= 2:
            return tuple(value + offset for value in values)
    raise ValueError("could not locate trace ticks")


def _scalar_labels(ticks: tuple[float, ...], limits: tuple[float, float]) -> tuple[tuple[str, ...], str]:
    low, high = sorted(limits)
    visible = [v for v in ticks if low <= v <= high]
    offset = 0
    if len(visible) >= 2 and min(visible) != max(visible) and not min(visible) <= 0 <= max(visible):
        small, large = sorted((abs(min(visible)), abs(max(visible))))
        power = math.ceil(math.log10(large))
        while small // 10**power == large // 10**power:
            power -= 1
        power += 1
        if (large - small) / 10**power <= 1e-2:
            power = math.ceil(math.log10(large))
            while large // 10**power - small // 10**power <= 1:
                power -= 1
            power += 1
        if large // 10**power >= 1000:
            offset = math.copysign((large // 10**power) * 10**power, visible[0])
    maximum = max(map(abs, visible), default=0)
    exponent = math.floor(math.log10(high - low if offset else maximum)) if maximum else 0
    if -5 < exponent < 6:
        exponent = 0
    normalized = [(value - offset) / 10.0**exponent for value in ticks]
    span = max(normalized) - min(normalized) if len(normalized) >= 2 else (high - low) / 10.0**exponent
    span = span or max(map(abs, normalized), default=0) or 1
    power = math.floor(math.log10(span))
    precision = max(0, 3 - power)
    while (
        precision >= 0 and max(abs(v - round(v * 10**precision) / 10**precision) for v in normalized) < 1e-3 * 10**power
    ):
        precision -= 1
    labels = tuple(f"{0 if abs(v) < 1e-8 else v:.{precision + 1}f}" for v in normalized)
    suffix = f"1e{exponent}" if exponent else ""
    if offset:
        power = math.floor(math.log10(abs(offset)))
        coefficient = round(offset / 10**power, 10)
        value = f"{coefficient:.10g}" if power == 0 else f"{coefficient:.10g}e{power}"
        suffix += ("+" if offset > 0 else "") + value
    return labels, suffix


def date_ticks(limits: tuple[float, float]) -> tuple[float, ...]:
    """Calendar-aligned UTC ticks; labels alone are converted to request time."""
    low, high = sorted(limits)
    start, end = (EPOCH + timedelta(days=v) for v in (low, high))
    delta = relativedelta(end, start)
    elapsed = end - start
    hours = elapsed.days * 24 + delta.hours
    counts = (
        delta.years,
        delta.years * 12 + delta.months,
        elapsed.days,
        hours,
        hours * 60 + delta.minutes,
        int(elapsed.total_seconds()),
    )
    intervals = (
        (1, 2, 4, 5, 10, 20, 40, 50, 100, 200, 400, 500, 1000, 2000, 4000, 5000, 10000),
        (1, 2, 3, 4, 6),
        (1, 2, 4, 7, 14),
        (1, 2, 3, 4, 6, 12),
        (1, 5, 10, 15, 30),
        (1, 5, 10, 15, 30),
    )
    maxima = (11, 12, 11, 12, 11, 11)
    frequency = next((i for i, count in enumerate(counts) if count >= 5), 6)
    if frequency == 6:
        # MicrosecondLocator uses an epoch-day origin and MultipleLocator,
        # including the tick immediately outside each end of the view.
        micros = math.floor(elapsed.total_seconds() * 1e6)
        step = next((v for power in range(7) for m in (1, 2, 5) if (v := m * 10**power) >= micros / 7), 1000000)
        origin = math.floor(low)
        first = math.ceil((low - origin) * SECONDS_PER_DAY * 1e6 / step) * step - step
        count = math.floor(((high - origin) * SECONDS_PER_DAY * 1e6 - first - step + 0.001 * step) / step) + 3
        return tuple(origin + (first + i * step) / 1e6 / SECONDS_PER_DAY for i in range(count))
    choices = intervals[frequency]
    step = next((v for v in choices if counts[frequency] <= v * (maxima[frequency] - 1)), choices[-1])
    if frequency == 0:
        first = max(1, start.year // step * step)
        last = min(9999, math.ceil(end.year / step) * step)
        return tuple(date_number(datetime(year, 1, 1, tzinfo=UTC)) for year in range(first, last + 1, step))
    fields = ("bymonth", "bymonthday", "byhour", "byminute", "bysecond")
    ranges = (range(1, 13), range(1, 32), range(24), range(60), range(60))
    rules = dict(zip(fields, (1, 1, 0, 0, 0)))
    for field in fields[: frequency - 1]:
        rules[field] = None
    selected = ranges[frequency - 1][::step]
    if frequency == 2 and step in (7, 14):
        selected = (1, 8, 15, 22) if step == 7 else (1, 15)
    rules[fields[frequency - 1]] = selected
    try:
        rule_start = start - delta
    except (ValueError, OverflowError):
        rule_start = datetime(1, 1, 1, tzinfo=UTC)
    rule = rrule((MONTHLY, DAILY, HOURLY, MINUTELY, SECONDLY)[frequency - 1], dtstart=rule_start, until=end, **rules)
    values = tuple(date_number(value) for value in rule.between(start, end, inc=True))
    return values or (low, high)


@dataclass(frozen=True, slots=True)
class TraceAxis:
    limits: tuple[float, float]
    ticks: tuple[float, ...]
    labels: tuple[str, ...]
    offset: str = ""

    def fraction(self, value: float) -> float:
        start, end = self.limits
        return (value - start) / (end - start)


@dataclass(frozen=True, slots=True)
class TraceAxes:
    time: TraceAxis
    score: TraceAxis
    secondary: TraceAxis
    # Figure 12×8 inches at 100 dpi, with the legacy autofmt_xdate margins.
    size: tuple[int, int] = (1200, 800)
    bounds: tuple[float, float, float, float] = (150, 96, 1080, 640)

    def point(self, time: datetime, value: float, axis: str = "score") -> tuple[float, float]:
        left, top, right, bottom = self.bounds
        yaxis = self.score if axis == "score" else self.secondary
        return left + self.time.fraction(date_number(time)) * (right - left), bottom - yaxis.fraction(value) * (
            bottom - top
        )


def build_trace_axes(spec: TraceSpec) -> TraceAxes:
    start, end = date_number(spec.start), date_number(spec.end)
    if not spec.exact_time_limits and spec.start < spec.end:
        start = min(start, date_number(spec.start.replace(minute=0, second=0, microsecond=0)))
    if start == end:
        start, end = start - 730, end + 730
    if not spec.exact_time_limits:
        margin = (end - start) * 0.05
        start, end = start - margin, end + margin
    times = date_ticks((start, end))
    time_axis = TraceAxis(
        (start, end),
        times,
        tuple((EPOCH + timedelta(days=v)).astimezone(spec.start.tzinfo).strftime("%m-%d %H:%M") for v in times),
    )
    if spec.score_limits is None:
        scores = [v for series in spec.series if series.axis == "score" for v in series.values]
        scores.extend(line.value for line in spec.horizontal_lines)
        lo, hi = nonsingular((min(scores), max(scores)))
        margin = (hi - lo) * 0.05
        score_limits = lo - margin, hi + margin
    else:
        score_limits = nonsingular(spec.score_limits)
        if spec.score_limits[0] > spec.score_limits[1]:
            score_limits = score_limits[::-1]
    scores = numeric_ticks(score_limits)
    labels, offset = (
        _scalar_labels(scores, score_limits)
        if spec.score_format == "scalar"
        else (tuple(score_text(v) for v in scores), "")
    )
    secondary_limits = nonsingular(spec.secondary_limits)
    if spec.secondary_limits[0] > spec.secondary_limits[1]:
        secondary_limits = secondary_limits[::-1]
    secondary = numeric_ticks(secondary_limits)
    secondary_labels = tuple(
        (str(int(v)) if 1 <= int(v) <= 100 else "") if spec.secondary_kind == "rank" else score_text(int(v)) + "/h"
        for v in secondary
    )
    return TraceAxes(
        time_axis,
        TraceAxis(score_limits, scores, labels, offset),
        TraceAxis(secondary_limits, secondary, secondary_labels),
    )
