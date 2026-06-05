from datetime import UTC, datetime
from types import SimpleNamespace

from src.sekai.sk.drawer import _collect_skl_display_ranks, _collect_speed_display_rows


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
