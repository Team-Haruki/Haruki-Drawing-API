from datetime import UTC, datetime

from src.sekai.card.timeline import acquired_datetime, timeline_columns, timeline_width


def record(card_id, timestamp, owned=True):
    return {"card": {"card_id": card_id, "character_id": 1}, "has": owned, "acquired_at": timestamp}


def test_timeline_uses_acquisition_time_timezone_and_owned_only():
    boundary = int(datetime(2026, 1, 31, 16, 30, tzinfo=UTC).timestamp() * 1000)
    records = [record(3, None), record(2, boundary), record(1, boundary - 3600000), record(4, boundary, False)]
    columns = timeline_columns(records, "Asia/Shanghai")
    assert [name for name, _ in columns] == ["2026.01", "2026.02", "获取时间未知"]
    assert [items[0]["card"]["card_id"] for _, items in columns] == [1, 2, 3]
    assert acquired_datetime(record(1, -1), "UTC") is None
    assert acquired_datetime(record(1, 10**30), "UTC") is None


def test_dense_month_splits_without_losing_order_or_growing_unbounded_width():
    timestamp = 1780000000000
    columns = timeline_columns([record(i, timestamp) for i in reversed(range(1000))], "UTC")
    assert len(columns) == 21
    assert max(len(items) for _, items in columns) == 48
    assert [r["card"]["card_id"] for _, items in columns for r in items] == list(range(1000))
    assert timeline_width(columns) == timeline_width(columns[:9])
    assert timeline_width([]) >= 480


def test_timeline_draws_month_rows_dates_and_character_tints():
    from types import SimpleNamespace

    from src.sekai.base.plot import Canvas, Spacer
    from src.sekai.card.timeline import draw_timeline

    drawn = []

    def draw_card(item):
        drawn.append(item["card"]["card_id"])
        Spacer(w=72, h=72)

    renderer = SimpleNamespace(
        panel_width=timeline_width([("2026.01", [])] * 10),
        rqd=SimpleNamespace(timezone="UTC"),
        _draw_card=draw_card,
        _character_color=lambda _: (51, 170, 238, 255),
    )
    columns = [(f"2026.{month:02}", [record(month, 1780000000000)]) for month in range(1, 11)]
    columns.append(("获取时间未知", [record(11, None)]))
    with Canvas() as canvas:
        draw_timeline(renderer, columns)
    width, height = canvas._get_self_size()
    assert width == renderer.panel_width
    assert height > 250
    assert drawn == list(range(1, 12))
    with Canvas() as empty:
        draw_timeline(renderer, [])
    assert empty._get_self_size()[0] == renderer.panel_width
