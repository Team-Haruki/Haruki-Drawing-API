from datetime import UTC, datetime
from types import SimpleNamespace

from src.sekai.sk.drawer import _collect_skl_display_ranks, _collect_speed_display_rows
from src.sekai.sk.model import PlayerTraceRequest


def test_collect_skl_display_ranks_uses_payload_ranks_without_default_filter():
    current_ranks = [
        SimpleNamespace(rank=1500),
        SimpleNamespace(rank=10),
    ]
    forecast_columns = [
        SimpleNamespace(
            ranks=[
                SimpleNamespace(rank=2500),
                SimpleNamespace(rank=1500),
            ]
        )
    ]

    assert _collect_skl_display_ranks(current_ranks, forecast_columns) == [10, 1500, 2500]


def test_collect_speed_display_rows_uses_payload_ranks_without_default_filter():
    record_time = datetime(2026, 6, 5, tzinfo=UTC)
    rows = _collect_speed_display_rows(
        [
            SimpleNamespace(rank=1500, score=20, speed=2, record_time=record_time),
            SimpleNamespace(rank=42, score=10, speed=1, record_time=record_time),
        ]
    )

    assert [row[0] for row in rows] == [42, 1500]


def test_player_trace_request_accepts_compare_rank_trace_payload():
    payload = PlayerTraceRequest.model_validate(
        {
            "event_id": 101,
            "region": "jp",
            "ranks": [
                {"rank": 20, "name": "Self", "score": 1_000_000, "time": 1_704_067_200_000},
            ],
            "compare_rank": 100,
            "compare_rank_trace": [
                {"rank": 100, "name": "Rank 100", "score": 900_000, "time": 1_704_067_200_000},
                {"rank": 100, "name": "Rank 100", "score": 950_000, "time": 1_704_070_800_000},
            ],
            "compare_rank_latest": {
                "rank": 100,
                "name": "Rank 100",
                "score": 950_000,
                "time": 1_704_070_800_000,
            },
            "compare_rank_line_score": 950_000,
        }
    )

    assert payload.compare_rank == 100
    assert payload.ranks2 is None
    assert payload.compare_rank_trace is not None
    assert len(payload.compare_rank_trace) == 2
    assert payload.compare_rank_latest is not None
    assert payload.compare_rank_latest.score == 950_000
    assert payload.compare_rank_line_score == 950_000
