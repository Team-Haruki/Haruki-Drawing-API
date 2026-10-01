from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

from PIL import Image
import pytest

from src.sekai.base.image_source import missing_image_ref
import src.sekai.misc.drawer as misc_drawer
from src.sekai.misc.model import (
    BirthdayEventTime,
    CharaBirthdayCard,
    CharaBirthdayData,
    CharaBirthdayRequest,
)


def _birthday_request(is_fifth_anniv: bool, **updates) -> CharaBirthdayRequest:
    base_time = 1_767_225_600_000
    required_time = BirthdayEventTime(start_at=base_time, end_at=base_time + 3_600_000)
    optional_time = required_time if is_fifth_anniv else None
    data = {
        "cid": 1,
        "month": 8,
        "day": 31,
        "region_name": "JP",
        "days_until_birthday": 30,
        "color_code": "#33aaff",
        "sd_image_path": "sd.png",
        "title_image_path": "title.png",
        "card_image_path": "card.png",
        "cards": [CharaBirthdayCard(id=100, thumbnail_path="thumb.png")],
        "is_fifth_anniv": is_fifth_anniv,
        "gacha_time": required_time,
        "live_time": required_time,
        "drop_time": optional_time,
        "flower_time": optional_time,
        "party_time": optional_time,
        "all_characters": [
            CharaBirthdayData(cid=1, month=8, day=31, icon_path="one.png"),
            CharaBirthdayData(cid=6, month=5, day=17, icon_path="six.png"),
        ],
        "timezone": "UTC",
    }
    data.update(updates)
    return CharaBirthdayRequest(**data)


def _fake_assets(card_image=None):
    async def fake_assets(_request):
        sd_image = Image.new("RGBA", (250, 200), (160, 80, 20, 255))
        title_image = Image.new("RGBA", (208, 72), (80, 160, 20, 255))
        thumbnail = Image.new("RGBA", (128, 128), (160, 20, 80, 255))
        icons = {
            1: Image.new("RGBA", (128, 128), (20, 160, 80, 255)),
            6: Image.new("RGBA", (128, 128), (80, 20, 160, 255)),
        }
        art = card_image if card_image is not None else Image.new("RGBA", (2520, 1440), (20, 80, 160, 255))
        return art, sd_image, title_image, [thumbnail], icons, {}

    return fake_assets


@pytest.mark.parametrize("is_fifth_anniv", [False, True])
def test_chara_birthday_canvas_covers_standard_and_anniversary_sections(is_fifth_anniv, monkeypatch) -> None:
    monkeypatch.setattr(misc_drawer, "_load_chara_birthday_assets", _fake_assets())

    canvas = asyncio.run(misc_drawer._build_chara_birthday_canvas(_birthday_request(is_fifth_anniv)))
    image = asyncio.run(canvas.get_img())

    assert image.width > 0
    assert image.height > 0


def test_chara_birthday_canvas_survives_missing_art_and_empty_lists(monkeypatch) -> None:
    monkeypatch.setattr(misc_drawer, "_load_chara_birthday_assets", _fake_assets(missing_image_ref("card")))
    # The watermark re-roots the canvas; skip it so the page root stays at canvas.items[0].
    monkeypatch.setattr(misc_drawer, "add_request_watermark", lambda *_args, **_kwargs: None)

    request = _birthday_request(True, cards=[], all_characters=[], days_until_birthday=0, color_code="nope!")
    canvas = asyncio.run(misc_drawer._build_chara_birthday_canvas(request))
    image = asyncio.run(canvas.get_img())

    assert image.width > 0
    # Without cards and a calendar only the header, the art band and the event panel remain.
    assert len(canvas.items[0].items) == 3


def test_birthday_event_rows_follow_the_anniversary_flag() -> None:
    standard = misc_drawer._birthday_event_rows(_birthday_request(False))
    assert [row.key for row in standard] == ["gacha", "live"]

    anniversary = misc_drawer._birthday_event_rows(_birthday_request(True))
    assert [row.key for row in anniversary] == ["gacha", "live", "drop", "flower", "party"]

    partial = misc_drawer._birthday_event_rows(_birthday_request(True, party_time=None))
    assert [row.key for row in partial] == ["gacha", "live", "drop", "flower"]


def test_birthday_countdown_accent_and_calendar_cells() -> None:
    assert misc_drawer._birthday_countdown(0) == ("今天生日", misc_drawer.RED)
    assert misc_drawer._birthday_countdown(1) == ("明天生日", misc_drawer.RED)
    assert misc_drawer._birthday_countdown(7) == ("还有 7 天", misc_drawer.AMBER)
    assert misc_drawer._birthday_countdown(200) == ("还有 200 天", misc_drawer.SLATE)

    assert misc_drawer._birthday_accent("#33dd99") == (51, 221, 153, 255)
    assert misc_drawer._birthday_accent("nope!") == misc_drawer.SLATE

    rin = CharaBirthdayData(cid=22, month=12, day=27, icon_path="rin.png")
    len_ = CharaBirthdayData(cid=23, month=12, day=27, icon_path="len.png")
    miku = CharaBirthdayData(cid=21, month=8, day=31, icon_path="miku.png")
    cells = misc_drawer._birthday_calendar_cells([rin, miku, len_])
    assert cells == [[rin, len_], [miku]]


def test_birthday_span_text_and_length() -> None:
    start = datetime(2026, 7, 16, tzinfo=UTC)
    end = start + timedelta(days=7) - timedelta(minutes=1)
    assert misc_drawer._birthday_span_text(start, end) == "07-16 00:00 ~ 07-22 23:59"
    assert misc_drawer._birthday_span_length(start, end) == "7 天"
    assert misc_drawer._birthday_span_length(start, start + timedelta(days=1) - timedelta(minutes=1)) == "1 天"
    assert misc_drawer._birthday_span_length(start, start + timedelta(hours=6)) == "6 小时"
    assert misc_drawer._birthday_span_length(start, start) == "1 小时"


def test_cover_rect_covers_the_target_with_the_focus_clamped() -> None:
    rect = misc_drawer._cover_rect((2520, 1440), (900, 300), 0.42)
    crop_h = 300 / (900 / 2520)
    assert rect[0] == 0
    assert rect[2] == 2520
    assert rect[3] - rect[1] == pytest.approx(crop_h)
    assert rect[1] == pytest.approx(1440 * 0.42 - crop_h / 2)

    # A focus near the bottom keeps the crop inside the image.
    rect = misc_drawer._cover_rect((100, 100), (100, 50), 0.95)
    assert rect[1] == 50
    assert rect[3] == 100
    # A portrait source is cropped horizontally, centred.
    rect = misc_drawer._cover_rect((100, 400), (100, 50), 0.5)
    assert (rect[0], rect[2]) == (0, 100)
    assert rect[1] == 175


def test_birthday_timezone_label_prefers_explicit_then_datetime_zone() -> None:
    aware = datetime(2026, 8, 31, tzinfo=UTC)

    assert misc_drawer._birthday_timezone_label(aware, aware, "Asia/Shanghai") == " (Asia/Shanghai)"
    assert misc_drawer._birthday_timezone_label(aware, aware, None) == " (UTC)"
    assert misc_drawer._birthday_timezone_label(None, None, None) == ""
