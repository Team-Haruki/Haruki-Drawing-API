"""Pages draw the caller's localized region label; colours and assets stay keyed on the raw region code."""

from __future__ import annotations

from src.sekai.base.timezone import TimeZoneRequest, id_with_region, region_display, region_tag
from src.sekai.profile import drawer as profile_drawer
from src.sekai.profile.model import BasicProfile, DetailedProfileCardRequest
from src.sekai.sk import trace_spec
from src.sekai.sk.drawer import get_event_id_and_name_text


def test_helpers_prefer_the_label_and_fall_back_to_the_code() -> None:
    assert region_display("jp", "日服(JP)") == "日服(JP)"
    assert region_display("jp", "  ") == "JP"
    assert region_display("jp") == "JP"
    assert id_with_region(123, "jp", "日服(JP)") == "123 · 日服(JP)"
    assert id_with_region(123, "jp") == "123 (JP)"
    assert region_tag("cn", "国服(CN)", 42) == "国服(CN) 42"
    assert region_tag("cn", None, "CUSTOM") == "CN-CUSTOM"


def test_base_request_accepts_the_label() -> None:
    assert TimeZoneRequest().region_label is None
    assert TimeZoneRequest(region_label="台服(TW)").region_label == "台服(TW)"


def test_sk_event_titles() -> None:
    assert get_event_id_and_name_text("jp", 42, "Event", "日服(JP)") == "【日服(JP) 42】Event"
    assert get_event_id_and_name_text("en", 3007, "WL", "国际服(EN)") == "【国际服(EN) 7-第3章单榜】WL"
    assert trace_spec.event_title("jp", 42) == "【JP-42】"


def test_profile_card_chip_colour_stays_on_the_raw_code() -> None:
    assert profile_drawer._profile_card_region_chip_fill("jp") == profile_drawer._profile_card_region_chip_fill("JP")


def test_detailed_profile_card_carries_the_labels_to_the_card() -> None:
    detailed = DetailedProfileCardRequest(
        id="1234567890",
        region="jp",
        nickname="n",
        source="s",
        update_time=1,
        leader_image_path="a.png",
        region_label="日服(JP)",
        account_label="[日服(JP)] 123****890",
    )
    card = detailed.to_profile_card_request()
    assert card.region_label == "日服(JP)"
    assert card.profile.region_label == "日服(JP)"
    assert card.profile.account_label == "[日服(JP)] 123****890"


def test_profile_account_line_uses_account_label() -> None:
    profile = BasicProfile(
        id="1234567890", region="tw", nickname="n", leader_image_path="a.png", account_label="[台服(TW)] 1234567890"
    )
    assert profile_drawer._profile_account_line(profile) == "[台服(TW)] 1234567890"
