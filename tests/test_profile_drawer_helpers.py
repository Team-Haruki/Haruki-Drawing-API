from datetime import UTC, datetime

from src.sekai.base.painter import DEFAULT_FONT
from src.sekai.base.plot import TextBox, TextStyle
from src.sekai.profile.drawer import (
    _CARD_AGE_STALE,
    _CARD_AGE_WARN,
    _CARD_DIM,
    _profile_card_age_text,
    _profile_card_level_label,
    _profile_card_region_chip_fill,
    _profile_card_summary_line,
    _profile_card_uid_line,
    _profile_card_update_entries,
    _profile_card_update_lines,
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


def test_profile_card_level_label_uses_compact_form_for_long_names() -> None:
    short = [TextBox("Short", TextStyle(font=DEFAULT_FONT, size=12))]
    long = [TextBox("abcdefghijklmnop", TextStyle(font=DEFAULT_FONT, size=12))]

    assert _profile_card_level_label(short, None) is None
    assert _profile_card_level_label(short, 42) == "MySekai Lv.42"
    assert _profile_card_level_label(long, 42) == "MSLv.42"


def test_profile_card_summary_handles_hidden_uid_and_single_source() -> None:
    source = ProfileDataSource(name="Suite数据")

    assert _profile_card_summary_line(_profile(), []) == "JP: 1234567890123456"
    assert _profile_card_summary_line(_profile(hidden=True), [source]) == "JP: **********123456 Suite数据"


def test_profile_card_update_lines_cover_single_and_multiple_sources() -> None:
    suite = ProfileDataSource(name="Suite数据", update_time=1000)
    empty = ProfileDataSource(name="No timestamp")
    secondary = ProfileDataSource(name="Secondary数据", update_time=2000)

    assert _profile_card_update_lines([], "UTC") == []
    assert _profile_card_update_lines([empty], "UTC") == []
    assert _profile_card_update_lines([suite], "UTC") == ["更新时间: 01-01 00:16:40 (UTC)"]
    assert _profile_card_update_lines([suite, empty, secondary], "UTC") == [
        "Suite更新时间: 01-01 00:16:40 (UTC)",
    ]
    assert _profile_card_update_lines([suite, secondary], "UTC") == [
        "Suite更新时间: 01-01 00:16:40 (UTC)",
        "Secondary更新时间: 01-01 00:33:20 (UTC)",
    ]


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

    assert age(minutes=20) == ("1 小时内", _CARD_DIM)
    assert age(hours=5) == ("5 小时前", _CARD_DIM)
    assert age(hours=30) == ("昨天", _CARD_AGE_WARN)
    assert age(days=3) == ("3 天前", _CARD_AGE_WARN)
    assert age(days=40) == ("40 天前", _CARD_AGE_STALE)
    assert _profile_card_age_text(now, now - timedelta_zero())[0] == "1 小时内"  # future timestamps clamp to fresh


def timedelta_zero():
    from datetime import timedelta

    return timedelta(0)


def test_profile_card_update_entries_label_only_with_several_sources() -> None:
    now = datetime(2026, 1, 2, tzinfo=UTC)
    suite = ProfileDataSource(name="Suite数据", update_time=1_000)
    empty = ProfileDataSource(name="No timestamp")
    secondary = ProfileDataSource(name="Secondary数据", update_time=2_000)

    assert _profile_card_update_entries([], "UTC", now) == []
    assert _profile_card_update_entries([empty], "UTC", now) == []
    single = _profile_card_update_entries([suite], "UTC", now)
    assert [(label, absolute) for label, absolute, _age, _color in single] == [(None, "01-01 00:16:40 (UTC)")]
    assert single[0][2:] == ("20454 天前", _CARD_AGE_STALE)
    labels = [entry[0] for entry in _profile_card_update_entries([suite, empty, secondary], "UTC", now)]
    assert labels == ["Suite"]
    labels = [entry[0] for entry in _profile_card_update_entries([suite, secondary], "UTC", now)]
    assert labels == ["Suite", "Secondary"]
