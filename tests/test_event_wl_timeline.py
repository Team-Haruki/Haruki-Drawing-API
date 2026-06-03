from datetime import timedelta

from src.sekai.base.draw import CHARACTER_COLOR_CODE
from src.sekai.base.painter import color_code_to_rgb
from src.sekai.event.drawer import _is_wl_chapter_current, _normalize_wl_chapters, _wl_chapter_progress_segments


def _millis(dt) -> int:
    return int(dt.timestamp() * 1000)


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
