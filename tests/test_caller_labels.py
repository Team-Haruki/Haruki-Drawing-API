"""Caller labels (``TimeZoneRequest.labels``), the spec time format and the profile data source label."""

from datetime import UTC, datetime
from types import SimpleNamespace
from zoneinfo import ZoneInfo

from src.sekai.base.timezone import (
    TimeZoneRequest,
    caller_label,
    format_user_time,
    format_user_time_range,
    utc_offset_label,
)
from src.sekai.event.drawer import _build_event_planner_deck_request
from src.sekai.event.model import EventPlannerRequest
from src.sekai.profile.model import DetailedProfileCardRequest

SHANGHAI = ZoneInfo("Asia/Shanghai")


def test_caller_label_prefers_the_callers_text_and_fills_slots() -> None:
    request = TimeZoneRequest(labels={"sk.time_to_end": "还有 {duration}", "blank": "  "})
    assert caller_label(request, "sk.time_to_end", "fallback {duration}", duration="3天") == "还有 3天"
    # Missing or blank keys, and requests without labels, fall back to Drawing's text.
    assert caller_label(request, "blank", "默认") == "默认"
    assert caller_label(request, "missing", "默认 {n}", n=5) == "默认 5"
    assert caller_label(None, "missing", "默认") == "默认"
    assert caller_label(SimpleNamespace(labels=None), "x", "默认") == "默认"


def test_labels_are_optional_on_every_request() -> None:
    assert TimeZoneRequest().labels is None
    assert TimeZoneRequest.model_validate({"labels": {"a": "b"}}).labels == {"a": "b"}


def test_format_user_time_matches_cloud_format_user_time() -> None:
    assert format_user_time(datetime(2026, 10, 9, 6, 5, tzinfo=UTC), "Asia/Shanghai") == "2026-10-09 14:05 (UTC+8)"
    assert format_user_time(datetime(2026, 10, 9, 14, 5, 59, tzinfo=SHANGHAI)) == "2026-10-09 14:05 (UTC+8)"
    assert format_user_time(datetime(2026, 1, 9, 12, 0, tzinfo=UTC), "Asia/Kolkata") == "2026-01-09 17:30 (UTC+5:30)"
    assert format_user_time(datetime(2026, 1, 9, 12, 0, tzinfo=UTC), "America/Sao_Paulo") == "2026-01-09 09:00 (UTC-3)"
    assert format_user_time(1_791_547_200_000, "UTC") == "2026-10-09 12:00 (UTC+0)"
    assert format_user_time(None) == "未知时间"


def test_utc_offset_label() -> None:
    assert utc_offset_label(datetime(2026, 1, 1, tzinfo=SHANGHAI)) == "UTC+8"
    assert utc_offset_label(datetime(2026, 1, 1)) == "UTC+0"


def test_format_user_time_range_shows_one_offset() -> None:
    start = datetime(2026, 10, 9, 14, 5, tzinfo=SHANGHAI)
    end = datetime(2026, 10, 12, 20, 59, tzinfo=SHANGHAI)
    assert format_user_time_range(start, end) == "2026-10-09 14:05 ~ 2026-10-12 20:59 (UTC+8)"
    # Different offsets (a DST change inside the range) keep one offset per end.
    berlin = ZoneInfo("Europe/Berlin")
    assert format_user_time_range(
        datetime(2026, 3, 28, 12, tzinfo=berlin), datetime(2026, 3, 30, 12, tzinfo=berlin)
    ) == ("2026-03-28 12:00 (UTC+1) ~ 2026-03-30 12:00 (UTC+2)")
    assert format_user_time_range(None, end) == "未知时间 ~ 2026-10-12 20:59 (UTC+8)"


def _detailed_profile(**extra) -> DetailedProfileCardRequest:
    return DetailedProfileCardRequest(
        id="123456789",
        region="jp",
        nickname="name",
        source="snapshot",
        update_time=1_791_547_200_000,
        leader_image_path="leader.png",
        **extra,
    )


def test_detailed_profile_draws_the_data_source_label_from_the_kind() -> None:
    # Without the new fields: Drawing's own name for Suite data (it used to hard-code "Suite数据").
    (source,) = _detailed_profile().to_profile_card_request().data_sources
    assert (source.name, source.kind) == ("抓包数据", "suite")
    (source,) = _detailed_profile(data_source_kind="public").to_profile_card_request().data_sources
    assert (source.name, source.kind) == ("公开信息", "public")
    profile = _detailed_profile(data_source_kind="mysekai", data_source_label="MySekai data", labels={"k": "v"})
    card = profile.to_profile_card_request()
    assert (card.data_sources[0].name, card.data_sources[0].kind) == ("MySekai data", "mysekai")
    assert card.labels == {"k": "v"}


def test_event_planner_labels_reach_the_deck_request() -> None:
    request = EventPlannerRequest.model_validate(
        {
            "region": "jp",
            "event_id": 1,
            "target_point": 1000,
            "current_point": 10,
            "remaining_point": 990,
            "songs": [],
            "labels": {"deck.planner.target": "Target {point} pt"},
            "deck_request": {
                "region": "jp",
                "deck_data": [],
                "profile": _detailed_profile().model_dump(),
                "labels": {"deck.noun.planner": "Plan"},
            },
        }
    )
    deck = _build_event_planner_deck_request(request)
    assert deck.labels == {"deck.planner.target": "Target {point} pt", "deck.noun.planner": "Plan"}
