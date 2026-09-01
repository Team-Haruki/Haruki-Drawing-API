import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

from PIL import Image
import pytest

from src.sekai.base.draw import CHARACTER_COLOR_CODE
from src.sekai.base.painter import color_code_to_rgb
import src.sekai.event.drawer as event_drawer
from src.sekai.event.drawer import (
    _current_wl_chapter,
    _event_card_column_count,
    _event_status_text,
    _is_wl_chapter_current,
    _normalize_wl_chapters,
    _wl_chapter_progress_segments,
)
from src.sekai.event.model import EventAssets, EventDetailRequest, EventInfo


def _millis(dt) -> int:
    return int(dt.timestamp() * 1000)


def _event_request(
    *,
    event_type: str = "normal",
    start_at: datetime | None = None,
    end_at: datetime | None = None,
    wl_time_list: list[dict] | None = None,
) -> EventDetailRequest:
    start_at = start_at or datetime(2027, 1, 1, tzinfo=UTC)
    end_at = end_at or start_at + timedelta(days=1)
    return EventDetailRequest(
        region="jp",
        timezone="UTC",
        event_info=EventInfo(
            id=1001,
            event_type=event_type,
            event_type_name="World Bloom" if event_type == "world_bloom" else "Marathon",
            start_at=start_at,
            end_at=end_at,
            is_wl_event=event_type == "world_bloom",
            banner_cid=1,
            banner_index=2,
            bonus_attr="cool",
            bonus_chara_id=[1],
            wl_time_list=wl_time_list,
        ),
        event_assets=EventAssets(
            event_bg_path="event-bg.png",
            event_logo_path="logo.png",
            event_story_bg_path="story-bg.png",
            event_attr_image_path="attr.png",
            event_ban_chara_img="chara.png",
            ban_chara_icon_path="ban-icon.png",
            bonus_chara_path=["bonus.png"],
        ),
        event_cards=[],
    )


def test_normalize_wl_chapters_uses_payload_color_and_chapter_end_time():
    base = 1_700_000_000_000
    chapters = [
        {
            "chapter_no": 2,
            "game_character_id": 22,
            "chapter_start_at": base + 5_000,
            "chapter_aggregate_at": base + 7_000,
            "chapter_end_at": base + 8_000,
            "color_code": "#123456",
            "character_name": "镜音铃",
            "character_icon_path": "static_images/chara_icon/rin.png",
        },
        {
            "chapter_id": 1,
            "game_character_id": 21,
            "start_at": base + 1_000,
            "aggregate_at": base + 3_000,
            "character_color_code": "#abcdef",
            "character_name": "初音未来",
            "character_icon_path": "static_images/chara_icon/miku.png",
        },
    ]

    normalized = _normalize_wl_chapters(chapters, "Asia/Shanghai")

    assert [item["chapter_no"] for item in normalized] == [1, 2]
    assert normalized[0]["color"] == (171, 205, 239, 255)
    assert normalized[0]["chapter_label"] == "初音未来 章节"
    assert normalized[0]["character_icon_path"] == "static_images/chara_icon/miku.png"
    assert _millis(normalized[0]["end_time"]) == base + 4_000
    assert normalized[1]["color"] == (18, 52, 86, 255)
    assert _millis(normalized[1]["end_time"]) == base + 8_000


def test_normalize_wl_chapters_falls_back_to_character_color():
    base = 1_700_000_000_000
    normalized = _normalize_wl_chapters(
        [
            {
                "chapter_no": 1,
                "game_character_id": 5,
                "chapter_start_at": base + 1_000,
                "chapter_end_at": base + 2_000,
            }
        ],
        "Asia/Shanghai",
    )

    assert normalized[0]["color"] == color_code_to_rgb(CHARACTER_COLOR_CODE[5])


def test_wl_chapter_progress_segments_keep_chapter_gaps_and_hide_future_time():
    base = 1_700_000_000_000
    normalized = _normalize_wl_chapters(
        [
            {
                "chapter_no": 1,
                "chapter_start_at": base,
                "chapter_end_at": base + 20_000,
            },
            {
                "chapter_no": 2,
                "chapter_start_at": base + 50_000,
                "chapter_end_at": base + 80_000,
            },
        ],
        "Asia/Shanghai",
    )
    event_start = normalized[0]["start_time"]
    event_end = event_start + timedelta(seconds=100)
    now = event_start + timedelta(seconds=65)

    segments = _wl_chapter_progress_segments(
        normalized,
        event_start,
        event_end,
        now,
    )

    assert [(round(start, 2), round(end, 2)) for start, end, _ in segments] == [(0, 0.2), (0.5, 0.65)]
    assert segments[0][1] < segments[1][0]


def test_is_wl_chapter_current_uses_real_chapter_window():
    base = 1_700_000_000_000
    normalized = _normalize_wl_chapters(
        [
            {
                "chapter_no": 1,
                "chapter_start_at": base,
                "chapter_end_at": base + 20_000,
            }
        ],
        "Asia/Shanghai",
    )

    assert _is_wl_chapter_current(normalized[0], normalized[0]["start_time"] + timedelta(seconds=10))
    assert not _is_wl_chapter_current(normalized[0], normalized[0]["end_time"] + timedelta(seconds=1))


@pytest.mark.parametrize(
    ("now_offset", "expected_prefix"),
    [(-1, "距开始还有"), (1, "距结束还有"), (3, "活动已结束")],
    ids=["future", "active", "expired"],
)
def test_event_status_text_covers_lifecycle(now_offset: int, expected_prefix: str) -> None:
    start_time = datetime(2027, 1, 2, tzinfo=UTC)
    end_time = start_time + timedelta(days=2)

    assert _event_status_text(start_time, end_time, start_time + timedelta(days=now_offset)).startswith(expected_prefix)


def test_current_wl_chapter_returns_first_matching_window() -> None:
    start_time = datetime(2027, 1, 1, tzinfo=UTC)
    chapters = [
        {"start_time": start_time, "end_time": start_time + timedelta(hours=1)},
        {"start_time": start_time + timedelta(hours=2), "end_time": start_time + timedelta(hours=3)},
    ]

    assert _current_wl_chapter(chapters, start_time + timedelta(minutes=30)) is chapters[0]
    assert _current_wl_chapter(chapters, start_time + timedelta(hours=4)) is None


@pytest.mark.parametrize(("card_count", "columns"), [(1, 1), (4, 4), (5, 3), (6, 3), (7, 4), (8, 4)])
def test_event_card_column_count_balances_rows(card_count: int, columns: int) -> None:
    assert _event_card_column_count(card_count) == columns


@pytest.mark.parametrize(
    ("event_type", "expected_bg", "expects_chara"),
    [("normal", "story-bg.png", True), ("world_bloom", "event-bg.png", False)],
)
def test_load_event_detail_images_selects_background_and_optional_chara(
    monkeypatch,
    event_type: str,
    expected_bg: str,
    expects_chara: bool,
) -> None:
    base = 1_800_000_000_000
    request = _event_request(
        event_type=event_type,
        wl_time_list=[
            {
                "chapter_no": 1,
                "chapter_start_at": base,
                "chapter_end_at": base + 10_000,
                "character_icon_path": "chapter.png",
            }
        ],
    )
    chapters = _normalize_wl_chapters(request.event_info.wl_time_list, request.timezone)
    calls: list[str] = []

    async def fake_asset_ref(_base_dir, path):
        calls.append(path)
        return path

    monkeypatch.setattr(event_drawer, "get_asset_image_ref", fake_asset_ref)

    images = asyncio.run(event_drawer._load_event_detail_images(request, chapters, event_type != "world_bloom"))

    assert images["bg"] == expected_bg
    assert ("chara" in images) is expects_chara
    assert images["wl_chapter_icon_0"] == "chapter.png"
    assert calls.count("bonus.png") == 1


@pytest.mark.parametrize(
    ("event_type", "start_offset", "end_offset"),
    [
        ("normal", 1, 2),
        ("normal", -1, 1),
        ("normal", -2, -1),
        ("world_bloom", -1, 1),
    ],
    ids=["future", "active", "expired", "world-bloom"],
)
def test_build_event_detail_canvas_covers_lifecycle_and_world_bloom(
    monkeypatch,
    event_type: str,
    start_offset: int,
    end_offset: int,
) -> None:
    now = datetime(2027, 1, 2, tzinfo=UTC)
    chapter_start = int((now - timedelta(hours=1)).timestamp() * 1000)
    request = _event_request(
        event_type=event_type,
        start_at=now + timedelta(days=start_offset),
        end_at=now + timedelta(days=end_offset),
        wl_time_list=[
            {
                "chapter_no": 1,
                "chapter_start_at": chapter_start,
                "chapter_end_at": chapter_start + 2 * 60 * 60 * 1000,
                "character_name": "Miku",
                "character_icon_path": "chapter.png",
            }
        ]
        if event_type == "world_bloom"
        else None,
    )
    background = Image.new("RGB", (1600, 1024), (40, 80, 120))
    icon = Image.new("RGBA", (64, 64), (180, 80, 120, 255))

    async def fake_card_layers(_request):
        return []

    async def fake_images(_request, chapters, _use_story_bg):
        images = {
            "bg": background,
            "logo": icon,
            "ban_icon": icon,
            "attr": icon,
            "bonus_chara_0": icon,
            "chara": icon,
        }
        if chapters:
            chapters[0]["character_icon_key"] = "wl_chapter_icon_0"
            images["wl_chapter_icon_0"] = icon
        return images

    monkeypatch.setattr(event_drawer, "request_now", lambda _timezone: now)
    monkeypatch.setattr(event_drawer, "_load_event_detail_card_layers", fake_card_layers)
    monkeypatch.setattr(event_drawer, "_load_event_detail_images", fake_images)

    canvas = asyncio.run(event_drawer._build_event_detail_canvas(request))

    assert canvas is not None


def test_draw_event_cards_limits_visible_cards_and_uses_four_columns(monkeypatch) -> None:
    cards = [SimpleNamespace(card_id=index) for index in range(10)]
    layers = [object() for _ in cards]
    seen_layers: list[object] = []

    monkeypatch.setattr(
        event_drawer,
        "CardFullThumbnailBox",
        lambda layer, **_kwargs: seen_layers.append(layer),
    )
    style = event_drawer.TextStyle(font=event_drawer.DEFAULT_BOLD_FONT, size=24, color=(0, 0, 0))
    styles = event_drawer._EventDetailStyles(style, style, style, style, style)
    with event_drawer.Canvas(bg=event_drawer.SEKAI_BLUE_BG):
        event_drawer._draw_event_cards(cards, layers, styles)

    assert seen_layers == layers[:8]
