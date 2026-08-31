from src.sekai.base.painter import DEFAULT_FONT
from src.sekai.base.plot import TextBox, TextStyle
from src.sekai.profile.drawer import (
    _profile_card_level_label,
    _profile_card_summary_line,
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
