"""Trace filtering, reference precedence and hourly speed are renderer-independent."""

from datetime import UTC, datetime, timedelta
import subprocess
import sys

import pytest

from src.sekai.sk.model import PlayerTraceRequest, RankTraceRequest
from src.sekai.sk.trace_spec import SCORE_COLORS, build_player_trace_spec, build_rank_trace_spec, day_night_spans

START = datetime(2026, 7, 9, 14, tzinfo=UTC)


def _rank(minutes, score, *, rank=10, name="player"):
    return {"time": START + timedelta(minutes=minutes), "score": score, "rank": rank, "name": name}


def _player(**extra):
    return PlayerTraceRequest.model_validate(
        {
            "event_id": 210,
            "region": "jp",
            "ranks": [_rank(60, 200), _rank(0, 100)],
            **extra,
        }
    )


def _rank_request(rows, **extra):
    return RankTraceRequest.model_validate(
        {"event_id": 210, "region": "jp", "target_rank": 100, "ranks": rows, **extra}
    )


def test_trace_spec_import_does_not_load_pillow_or_matplotlib():
    script = """
import importlib.abc, sys
class Reject(importlib.abc.MetaPathFinder):
    def find_spec(self, name, path=None, target=None):
        if name.split('.')[0] in ('PIL', 'matplotlib'):
            raise AssertionError('unexpected renderer import: ' + name)
sys.meta_path.insert(0, Reject())
from src.sekai.sk.trace_spec import build_player_trace_spec
from src.sekai.sk.model import PlayerTraceRequest
request = PlayerTraceRequest.model_validate({'event_id': 1, 'region': 'jp', 'ranks': [
    {'time': 1783576800000, 'score': 123, 'rank': 1, 'name': 'fixture'}]})
assert build_player_trace_spec(request).series[0].values == (123,)
"""
    process = subprocess.run([sys.executable, "-X", "gil=0", "-c", script], text=True, capture_output=True, timeout=30)
    assert process.returncode == 0, process.stderr


def test_player_filter_and_sort_do_not_mutate_request():
    request = _player(
        ranks=[_rank(60, 200), _rank(120, 1000, rank=101), _rank(0, 100)], ranks2=[_rank(70, 500, rank=200)]
    )
    before = request.model_dump(mode="json")
    spec = build_player_trace_spec(request)
    assert spec.series[0].values == (100, 200)
    assert len(spec.series) == 2
    assert spec.score_limits == (95, 210)
    assert request.model_dump(mode="json") == before


def test_player_without_top100_points_remains_invalid():
    with pytest.raises(ValueError, match="top 100"):
        build_player_trace_spec(_player(ranks=[_rank(0, 100, rank=101)]))


@pytest.mark.parametrize(("explicit", "expected"), [(None, 400), (300, 300)])
def test_explicit_reference_overrides_latest_score_but_keeps_latest_timestamp(explicit, expected):
    request = _player(compare_rank=100, compare_rank_line_score=explicit, compare_rank_latest=_rank(90, 400, rank=100))
    spec = build_player_trace_spec(request)
    assert spec.horizontal_lines[0].value == expected
    assert spec.annotations[1].time == request.compare_rank_latest.time
    assert spec.score_limits == (95, expected * 1.05)
    assert spec.end == request.ranks[0].time  # latest-only reference does not extend the historical x axis


def test_reference_history_controls_bounds_and_suppresses_latest_line():
    request = _player(compare_rank_trace=[_rank(180, 500, rank=50)], compare_rank_line_score=9000)
    spec = build_player_trace_spec(request)
    assert spec.horizontal_lines == ()
    assert spec.series[1].label == "T50分数线"
    assert spec.series[1].dashed
    assert spec.score_limits == (95, 525)
    assert spec.end == request.compare_rank_trace[0].time


def test_rank_hourly_window_uses_oldest_point_within_60_minutes():
    request = _rank_request(
        [_rank(0, 0), _rank(49, 49), _rank(50, 100), _rank(60, 120), _rank(61, 150), _rank(110, 240)]
    )
    spec = build_rank_trace_spec(request)
    assert spec.series[1].values == (-1, -1, 120, 120, -1, 140)
    assert spec.secondary_limits == (0, 140 * 1.2)


def test_rank_zero_valid_speed_and_no_valid_speed_are_distinct():
    assert build_rank_trace_spec(_rank_request([_rank(0, 5), _rank(60, 5)])).secondary_limits == (0, 0)
    assert build_rank_trace_spec(_rank_request([_rank(0, 5)])).secondary_limits == (0, 1.2)


def test_rank_name_colors_follow_sorted_first_occurrence_and_keep_input_order():
    request = _rank_request([_rank(60, 3, name="B"), _rank(0, 1, name="A"), _rank(30, 2, name="B")])
    before = request.model_dump(mode="json")
    spec = build_rank_trace_spec(request)
    assert spec.series[0].scatter_colors == (SCORE_COLORS[0], SCORE_COLORS[1], SCORE_COLORS[1])
    assert request.model_dump(mode="json") == before


def test_many_names_share_first_color_and_prediction_has_no_legend_entry():
    request = _rank_request([_rank(i, i + 1, name=str(i)) for i in range(11)], predict_ranks=_rank(100, 999))
    spec = build_rank_trace_spec(request)
    assert set(spec.series[0].scatter_colors) == {SCORE_COLORS[0]}
    assert spec.horizontal_lines[0].label is None
    assert spec.annotations[-1].value == 999 * 1.02
    assert spec.annotations[-1].outline is False


def test_day_night_spans_preserve_partial_hours_and_midnight_colors():
    start = START.replace(hour=23, minute=30)
    end = start + timedelta(hours=2)
    spans = day_night_spans(start, end)
    assert spans[0][0] == start.replace(minute=0)
    assert spans[-1][1] == end
    assert spans[1][2] == "#c8c8e6"
