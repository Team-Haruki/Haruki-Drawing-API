from datetime import UTC, datetime

from src.sekai.profile.drawer import (
    _CARD_AGE_FRESH,
    _CARD_AGE_STALE,
    _CARD_AGE_WARN,
    _profile_account_line,
    _profile_card_age_text,
    _profile_card_level_label,
    _profile_card_rank_label,
    _profile_card_region_chip_fill,
    _profile_card_source_rows,
    _profile_card_uid_line,
)
from src.sekai.profile.model import BasicProfile, ProfileDataSource


def _profile(*, hidden: bool = False) -> BasicProfile:
    return BasicProfile(
        id="1234567890123456",
        region="jp",
        nickname="Test",
        is_hide_uid=hidden,
        leader_image_path="leader.png",
    )


def test_profile_card_level_label_draws_the_mysekai_level() -> None:
    assert _profile_card_level_label(None) is None
    assert _profile_card_level_label(42) == "烤森 Lv.42"


def test_profile_account_line_prefers_caller_labels() -> None:
    # No labels: the legacy line, UID hidden by the renderer.
    assert _profile_account_line(_profile()) == "JP: 1234567890123456"
    assert _profile_account_line(_profile(hidden=True)) == "JP: **********123456"
    # A region label (on the profile or the request) keeps the renderer's UID hiding.
    labelled = _profile(hidden=True).model_copy(update={"region_label": "日服(JP)"})
    assert _profile_account_line(labelled) == "[日服(JP)] **********123456"
    assert _profile_account_line(_profile(), "日服(JP)") == "[日服(JP)] 1234567890123456"
    # The caller's account line is drawn verbatim: it already hid the UID.
    account = _profile(hidden=True).model_copy(update={"account_label": "[日服(JP)] 123***456"})
    assert _profile_account_line(account, "ignored") == "[日服(JP)] 123***456"


def test_profile_card_uid_line_and_region_chip() -> None:
    assert _profile_card_uid_line(_profile()) == "ID 1234567890123456"
    assert _profile_card_uid_line(_profile(hidden=True)) == "ID **********123456"
    assert _profile_card_region_chip_fill("jp") == _profile_card_region_chip_fill("JP")
    assert _profile_card_region_chip_fill("xx") != _profile_card_region_chip_fill("jp")


def test_profile_card_age_text_granularity_and_stale_colours() -> None:
    now = datetime(2026, 9, 30, 12, 0, tzinfo=UTC)

    def age(**delta):
        from datetime import timedelta

        return _profile_card_age_text(now - timedelta(**delta), now)

    assert age(minutes=20) == ("1 小时内", _CARD_AGE_FRESH)
    assert age(hours=5) == ("5 小时前", _CARD_AGE_FRESH)
    assert age(hours=30) == ("昨天", _CARD_AGE_WARN)
    assert age(days=3) == ("3 天前", _CARD_AGE_WARN)
    assert age(days=40) == ("40 天前", _CARD_AGE_STALE)
    assert _profile_card_age_text(now, now - timedelta_zero())[0] == "1 小时内"  # future timestamps clamp to fresh


def timedelta_zero():
    from datetime import timedelta

    return timedelta(0)


def test_profile_card_source_rows_keep_the_first_two_timestamped_sources() -> None:
    now = datetime(2026, 1, 2, tzinfo=UTC)
    suite = ProfileDataSource(name="Suite数据", update_time=1_000)
    empty = ProfileDataSource(name="No timestamp")
    secondary = ProfileDataSource(name="Secondary数据", update_time=2_000)

    assert _profile_card_source_rows([], "UTC", now) == []
    assert _profile_card_source_rows([empty], "UTC", now) == []
    assert _profile_card_source_rows([suite], "UTC", now) == [
        ("Suite数据", "01-01 00:16:40", "20454 天前", _CARD_AGE_STALE)
    ]
    assert [row[0] for row in _profile_card_source_rows([suite, empty, secondary], "UTC", now)] == ["Suite数据"]
    assert [row[0] for row in _profile_card_source_rows([suite, secondary], "UTC", now)] == [
        "Suite数据",
        "Secondary数据",
    ]


def test_profile_card_rank_label() -> None:
    assert _profile_card_rank_label(None) is None
    assert _profile_card_rank_label(0) is None
    assert _profile_card_rank_label(380) == "Lv.380"
