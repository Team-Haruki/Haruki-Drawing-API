from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from PIL import Image
import pytest

import src.sekai.misc.drawer as misc_drawer
from src.sekai.misc.model import (
    BirthdayEventTime,
    CharaBirthdayCard,
    CharaBirthdayData,
    CharaBirthdayRequest,
)


def _birthday_request(is_fifth_anniv: bool) -> CharaBirthdayRequest:
    base_time = 1_767_225_600_000
    required_time = BirthdayEventTime(start_at=base_time, end_at=base_time + 3_600_000)
    optional_time = required_time if is_fifth_anniv else None
    return CharaBirthdayRequest(
        cid=1,
        month=8,
        day=31,
        region_name="JP",
        days_until_birthday=30,
        color_code="#33aaff",
        sd_image_path="sd.png",
        title_image_path="title.png",
        card_image_path="card.png",
        cards=[CharaBirthdayCard(id=100, thumbnail_path="thumb.png")],
        is_fifth_anniv=is_fifth_anniv,
        gacha_time=required_time,
        live_time=required_time,
        drop_time=optional_time,
        flower_time=optional_time,
        party_time=optional_time,
        all_characters=[
            CharaBirthdayData(cid=1, month=8, day=31, icon_path="one.png"),
            CharaBirthdayData(cid=6, month=5, day=17, icon_path="six.png"),
        ],
        timezone="UTC",
    )


@pytest.mark.parametrize("is_fifth_anniv", [False, True])
def test_chara_birthday_canvas_covers_standard_and_anniversary_sections(is_fifth_anniv, monkeypatch) -> None:
    async def fake_assets(_request):
        card_image = Image.new("RGBA", (640, 480), (20, 80, 160, 255))
        sd_image = Image.new("RGBA", (80, 80), (160, 80, 20, 255))
        title_image = Image.new("RGBA", (160, 60), (80, 160, 20, 255))
        thumbnail = Image.new("RGBA", (80, 80), (160, 20, 80, 255))
        icons = {
            1: Image.new("RGBA", (40, 40), (20, 160, 80, 255)),
            6: Image.new("RGBA", (40, 40), (80, 20, 160, 255)),
        }
        return card_image, sd_image, title_image, [thumbnail], icons, {}

    monkeypatch.setattr(misc_drawer, "_load_chara_birthday_assets", fake_assets)

    canvas = asyncio.run(misc_drawer._build_chara_birthday_canvas(_birthday_request(is_fifth_anniv)))
    image = asyncio.run(canvas.get_img())

    assert image.width > 0
    assert image.height > 0


def test_birthday_timezone_label_prefers_explicit_then_datetime_zone() -> None:
    aware = datetime(2026, 8, 31, tzinfo=UTC)

    assert misc_drawer._birthday_timezone_label(aware, aware, "Asia/Shanghai") == " (Asia/Shanghai)"
    assert misc_drawer._birthday_timezone_label(aware, aware, None) == " (UTC)"
    assert misc_drawer._birthday_timezone_label(None, None, None) == ""
    assert misc_drawer._birthday_calendar_start_index([CharaBirthdayData(cid=1, month=8, day=31, icon_path="")]) == 0
