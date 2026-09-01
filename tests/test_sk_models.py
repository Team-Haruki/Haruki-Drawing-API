from datetime import timedelta

from src.sekai.sk.model import (
    CFRequest,
    CSBRequest,
    PlayerTraceRequest,
    RankInfo,
    RankTraceRequest,
    SklForecastColumn,
    SklRequest,
    SKRequest,
    SpeedInfo,
    SpeedRequest,
    TeamInfo,
    WinRateRequest,
)

RANK = {
    "rank": 1,
    "name": "player",
    "score": 100,
    "time": "2026-01-01T00:00:00Z",
    "record_start_at": "2025-12-31T23:00:00Z",
}


def _assert_tokyo(value) -> None:
    assert value is not None
    assert value.utcoffset() == timedelta(hours=9)


def test_rank_speed_and_forecast_validators_parse_utc_inputs() -> None:
    rank = RankInfo(**RANK)
    speed = SpeedInfo(rank=1, score=100, record_time="1767225600000")
    forecast = SklForecastColumn(
        key="source",
        name="Source",
        ranks=[RANK],
        forecast_time="2026-01-01T01:00:00+01:00",
        update_time=1767225600,
    )
    assert rank.time.utcoffset() == timedelta(0)
    assert rank.record_start_at is not None
    assert speed.record_time.utcoffset() == timedelta(0)
    assert forecast.forecast_time == forecast.update_time


def test_skl_request_localizes_current_and_forecast_rank_collections() -> None:
    request = SklRequest(
        id=1,
        region="jp",
        start_at=1,
        aggregate_at=2,
        name="event",
        banner_img_path="banner.png",
        ranks=[RANK],
        current_ranks=[RANK],
        forecast_columns=[
            {
                "key": "source",
                "name": "Source",
                "ranks": [RANK],
                "forecast_time": "2026-01-01T00:00:00Z",
                "update_time": "2026-01-01T00:00:00Z",
            }
        ],
        timezone="Asia/Tokyo",
    )
    _assert_tokyo(request.ranks[0].time)
    _assert_tokyo(request.current_ranks[0].record_start_at)
    _assert_tokyo(request.forecast_columns[0].forecast_time)
    _assert_tokyo(request.forecast_columns[0].ranks[0].time)


def test_sk_and_cf_requests_localize_primary_neighbor_and_update_fields() -> None:
    sk = SKRequest(
        id=1,
        region="jp",
        name="event",
        aggregate_at=2,
        ranks=[RANK],
        prev_ranks=RANK,
        next_ranks=RANK,
        timezone="Asia/Tokyo",
    )
    _assert_tokyo(sk.ranks[0].time)
    _assert_tokyo(sk.prev_ranks.record_start_at)
    _assert_tokyo(sk.next_ranks.time)

    cf = CFRequest(
        eid=1,
        event_name="event",
        name="player",
        region="jp",
        ranks=[RANK],
        prev_rank=RANK,
        next_rank=RANK,
        aggregate_at=2,
        update_at="2026-01-01T00:00:00Z",
        timezone="Asia/Tokyo",
    )
    _assert_tokyo(cf.ranks[0].record_start_at)
    _assert_tokyo(cf.prev_rank.time)
    _assert_tokyo(cf.next_rank.record_start_at)
    _assert_tokyo(cf.update_at)


def test_csb_speed_and_trace_requests_localize_all_optional_series() -> None:
    csb = CSBRequest(
        eid=1,
        event_name="event",
        region="jp",
        ranks=[RANK],
        aggregate_at=2,
        update_at="2026-01-01T00:00:00Z",
        timezone="Asia/Tokyo",
    )
    _assert_tokyo(csb.ranks[0].time)
    _assert_tokyo(csb.update_at)

    speed = SpeedRequest(
        event_id=1,
        region="jp",
        event_name="event",
        event_start_at=1,
        event_aggregate_at=2,
        ranks=[{"rank": 1, "score": 100, "record_time": "2026-01-01T00:00:00Z"}],
        is_wl_event=False,
        request_type="hour",
        period=timedelta(hours=1),
        timezone="Asia/Tokyo",
    )
    _assert_tokyo(speed.ranks[0].record_time)

    trace = PlayerTraceRequest(
        event_id=1,
        region="jp",
        ranks=[RANK],
        ranks2=[RANK],
        compare_rank=100,
        compare_rank_trace=[RANK],
        compare_rank_latest=RANK,
        timezone="Asia/Tokyo",
    )
    _assert_tokyo(trace.ranks[0].time)
    _assert_tokyo(trace.ranks2[0].record_start_at)
    _assert_tokyo(trace.compare_rank_trace[0].time)
    _assert_tokyo(trace.compare_rank_latest.record_start_at)


def test_rank_trace_and_win_rate_requests_localize_predictions() -> None:
    trace = RankTraceRequest(
        event_id=1,
        region="jp",
        target_rank=100,
        ranks=[RANK],
        predict_ranks=RANK,
        timezone="Asia/Tokyo",
    )
    _assert_tokyo(trace.ranks[0].record_start_at)
    _assert_tokyo(trace.predict_ranks.time)

    team = TeamInfo(team_id=1, team_name="team", win_rate=0.5, is_recruiting=True)
    request = WinRateRequest(
        event_id=1,
        event_name="event",
        region="jp",
        updated_at="2026-01-01T00:00:00Z",
        event_start_at=1,
        event_aggregate_at=2,
        team_info=[team],
        timezone="Asia/Tokyo",
    )
    _assert_tokyo(request.updated_at)
    assert request.team_info == [team]
