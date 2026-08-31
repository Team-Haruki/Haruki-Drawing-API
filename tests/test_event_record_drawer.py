from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path

from src.sekai.base.utils import AssetImageRef
from src.sekai.event import drawer
from src.sekai.event.model import EventHistoryInfo, EventRecordRequest
from src.sekai.profile.model import DetailedProfileCardRequest


def _event(
    event_id: int,
    *,
    rank: int | None = None,
    rank_display: str | None = None,
    rank_tier: int | None = None,
    point: int | None = None,
) -> EventHistoryInfo:
    start = datetime(2026, 1, event_id, tzinfo=UTC)
    return EventHistoryInfo(
        id=event_id,
        event_name=f"event-{event_id}",
        start_at=start,
        end_at=start + timedelta(days=1),
        rank=rank,
        rank_display=rank_display,
        rank_tier=rank_tier,
        event_point=point,
        banner_path=f"banner-{event_id}.png",
    )


def _profile() -> DetailedProfileCardRequest:
    return DetailedProfileCardRequest(
        id="1",
        region="jp",
        nickname="tester",
        source="test",
        update_time=0,
        leader_image_path="leader.png",
    )


def test_event_record_sort_rank_supports_all_rank_forms() -> None:
    assert drawer._event_record_sort_rank(_event(1, rank=12)) == 12
    assert drawer._event_record_sort_rank(_event(1, rank_tier=34)) == 34
    assert drawer._event_record_sort_rank(_event(1, rank_display=" t56 ")) == 56
    assert drawer._event_record_sort_rank(_event(1, rank_display="TOP")) == float("inf")


def test_event_record_rows_sort_ranked_and_point_only_records() -> None:
    ranked = [_event(1, rank_display="T20", point=100), _event(2, rank=10, point=50)]
    title, has_rank, rows = drawer._event_record_rows("活动", ranked)
    assert title == "排名前30的活动记录"
    assert has_rank is True
    assert [item.id for item in rows] == [2, 1]

    point_only = [_event(3, point=None), _event(4, point=200)]
    title, has_rank, rows = drawer._event_record_rows("WL单榜", point_only)
    assert title == "活动点数前30的WL单榜记录"
    assert has_rank is False
    assert [item.id for item in rows] == [4, 3]
    assert drawer._event_record_point(rows[-1]) == 0


def test_build_event_record_canvas_dispatches_both_groups(monkeypatch) -> None:
    calls: list[tuple[str, list[int]]] = []

    async def fake_profile(_request) -> None:
        return None

    async def fake_group(name, events, *_styles) -> None:
        calls.append((name, [item.id for item in events]))

    monkeypatch.setattr(drawer, "get_profile_card", fake_profile)
    monkeypatch.setattr(drawer, "_draw_event_record_group", fake_group)
    monkeypatch.setattr(drawer, "add_request_watermark", lambda *_args: None)
    request = EventRecordRequest(
        event_info=[_event(1, rank=1, point=100)],
        wl_event_info=[_event(2, rank_display="T10", point=200)],
        user_info=_profile(),
        rank_note="档线仅供参考",
    )

    canvas = asyncio.run(drawer._build_event_record_canvas(request))

    assert canvas is not None
    assert calls == [("活动", [1]), ("WL单榜", [2])]


def test_build_event_record_canvas_accepts_empty_history_without_note(monkeypatch) -> None:
    async def fake_profile(_request) -> None:
        return None

    monkeypatch.setattr(drawer, "get_profile_card", fake_profile)
    monkeypatch.setattr(drawer, "add_request_watermark", lambda *_args: None)
    request = EventRecordRequest(event_info=[], wl_event_info=[], user_info=_profile())

    assert asyncio.run(drawer._build_event_record_canvas(request)) is not None


def test_draw_event_record_group_builds_rank_point_and_asset_columns(monkeypatch) -> None:
    async def fake_asset(_base, path) -> AssetImageRef:
        return AssetImageRef(Path(path), (120, 60), "RGBA")

    monkeypatch.setattr(drawer, "get_asset_image_ref", fake_asset)
    events = [
        _event(1, rank=2, point=123),
        _event(2, rank_display="T10", point=None),
    ]
    events[0].wl_chara_icon_path = "icon.png"
    header_style = drawer.TextStyle(font=drawer.DEFAULT_BOLD_FONT, size=24, color=(50, 50, 50))
    detail_style = drawer.TextStyle(font=drawer.DEFAULT_FONT, size=16, color=(70, 70, 70))
    value_style = drawer.TextStyle(font=drawer.DEFAULT_BOLD_FONT, size=24, color=(70, 70, 70))

    async def build() -> drawer.Canvas:
        with drawer.Canvas() as canvas:
            await drawer._draw_event_record_group("活动", events, header_style, detail_style, value_style)
        return canvas

    assert asyncio.run(build()) is not None
