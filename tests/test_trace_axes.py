"""Freeze the legacy chart's scale/tick semantics before replacing its rasterizer."""

from datetime import UTC, datetime, timedelta
import random
import subprocess
import sys
from zoneinfo import ZoneInfo

import pytest

from src.sekai.sk.model import PlayerTraceRequest, RankTraceRequest
from src.sekai.sk.trace_axes import _scalar_labels, build_trace_axes, date_number, date_ticks, numeric_ticks
from src.sekai.sk.trace_spec import build_player_trace_spec, build_rank_trace_spec


def test_axes_import_and_real_layout_without_pillow_or_matplotlib():
    code = """
import sys, importlib.abc
class Guard(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in ('PIL', 'matplotlib'):
            raise AssertionError(name)
sys.meta_path.insert(0, Guard())
from src.sekai.sk.model import PlayerTraceRequest
from src.sekai.sk.trace_spec import build_player_trace_spec
from src.sekai.sk.trace_axes import build_trace_axes
request = PlayerTraceRequest.model_validate({'event_id': 1, 'region': 'jp', 'ranks': [
    {'time': 1783576800000, 'score': 123, 'rank': 1, 'name': 'fixture'}]})
layout = build_trace_axes(build_player_trace_spec(request))
assert layout.secondary.limits == (110, -10)
assert layout.time.labels
assert not any(name.split('.')[0] in ('PIL', 'matplotlib') for name in sys.modules)
"""
    result = subprocess.run([sys.executable, "-X", "gil=0", "-c", code], capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr


@pytest.mark.parametrize(
    "seconds", [0.001, 0.5, 3, 7, 60, 301, 3600, 25000, 86400, 252000, 604800, 2500000, 16000000, 90000000, 400000000]
)
@pytest.mark.parametrize("zone", ["UTC", "Asia/Shanghai", "America/New_York"])
def test_calendar_ticks_match_legacy_including_leap_year_and_dst(seconds, zone):
    dates = pytest.importorskip("matplotlib.dates")
    start = datetime(2024, 2, 28, 13, 14, 15, tzinfo=ZoneInfo(zone)).astimezone(UTC)
    end = start + timedelta(seconds=seconds)
    expected = dates.AutoDateLocator().tick_values(start, end)
    actual = date_ticks((date_number(start), date_number(end)))
    assert len(actual) == len(expected)
    assert actual == pytest.approx(expected, rel=0, abs=4e-12)


def test_numeric_ticks_and_scalar_labels_match_legacy_scales_and_offsets():
    ticker = pytest.importorskip("matplotlib.ticker")
    rng = random.Random(9173)
    limits = [(0, 1), (-0.05, 0.05), (110, -10), (-314999.95, 6614998.95), (1e7, 1e7 + 0.01)]
    for _ in range(200):
        low = rng.uniform(-10, 10) * 10 ** rng.randrange(-4, 12)
        limits.append((low, low + 10 ** rng.randrange(-4, 12)))
    for bounds in limits:
        expected = ticker.AutoLocator().tick_values(*bounds)
        actual = numeric_ticks(bounds)
        assert actual == pytest.approx(expected, rel=0, abs=max(abs(v) for v in expected) * 1e-15), bounds
        formatter = ticker.ScalarFormatter(useOffset=True, useMathText=False)
        formatter.create_dummy_axis()
        formatter.axis.set_view_interval(*bounds)
        labels = formatter.format_ticks(expected)
        # SK explicitly uses ASCII minus via rcParams['axes.unicode_minus']=False.
        labels = tuple(label.replace("−", "-") for label in labels)
        offset = formatter.get_offset().replace("−", "-")
        assert _scalar_labels(actual, bounds) == (labels, offset), bounds


def _request(kind, rows, **kwargs):
    data = {"event_id": 1, "region": "jp", "ranks": rows, **kwargs}
    if kind == "player":
        return build_player_trace_spec(PlayerTraceRequest.model_validate(data))
    return build_rank_trace_spec(RankTraceRequest.model_validate({"target_rank": 100, **data}))


def test_single_point_limits_preserve_legacy_expansion_and_inverted_rank_axis():
    row = {"time": 1783576800000, "score": 100, "rank": 10, "name": "player"}
    player = _request("player", [row])
    layout = build_trace_axes(player)
    day = date_number(player.start)
    assert layout.time.limits == (day - 730, day + 730)
    assert layout.score.limits == (95, 105)
    assert layout.secondary.limits == (110, -10)
    assert layout.point(player.start, 100) == (615, 368)
    assert layout.point(player.start, 110, "secondary") == (615, 640)
    rank = build_trace_axes(_request("rank", [row]))
    assert rank.time.limits == (day - 803, day + 803)
    assert rank.score.limits == (94.5, 105.5)
    assert rank.secondary.limits == (0, 1.2)


def test_day_night_background_extends_only_autoscaled_time_axis():
    rows = [
        {"time": datetime(2026, 7, 9, 14, 15, tzinfo=UTC) + timedelta(hours=i), "score": 100, "rank": 10, "name": "p"}
        for i in range(2)
    ]
    rank = _request("rank", rows)
    layout = build_trace_axes(rank)
    floor = date_number(rank.start.replace(minute=0))
    end = date_number(rank.end)
    margin = (end - floor) * 0.05
    assert layout.time.limits == (floor - margin, end + margin)
    assert layout.secondary.limits == (-0.05, 0.05)  # valid zero speed; not the no-speed sentinel
    player = _request("player", rows)
    assert build_trace_axes(player).time.limits == (date_number(player.start), date_number(player.end))
