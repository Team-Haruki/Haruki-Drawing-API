from __future__ import annotations

import asyncio

from PIL import Image
import pytest

from src.sekai.base.plot import Canvas, TextStyle, VSplit
from src.sekai.deck.drawer import _draw_planner_number_cell, _planner_rows, draw_event_planner_block
from src.sekai.deck.model import DeckPlannerBoostRow, DeckPlannerInfo, DeckPlannerSong


def _planner(*, songs: list[DeckPlannerSong], target_source: str | None = "直接输入") -> DeckPlannerInfo:
    return DeckPlannerInfo(
        target_point=12_000_000,
        current_point=None,
        remaining_point=8_000_000,
        daily_point=None,
        target_source=target_source,
        songs=songs,
        warnings=["测试提示"],
    )


def test_planner_rows_keeps_empty_songs_as_placeholder_rows() -> None:
    populated = DeckPlannerSong(
        music_id=1,
        title="第一首",
        difficulty="expert",
        rows=[DeckPlannerBoostRow(boost=5, point_per_play=59_800, plays=148, energy=740)],
    )
    empty = DeckPlannerSong(music_id=2, title="第二首", difficulty=None, rows=[])

    assert _planner_rows(_planner(songs=[populated, empty])) == [
        (populated, populated.rows[0]),
        (empty, None),
    ]


def test_draw_event_planner_block_renders_rows_placeholders_and_empty_state() -> None:
    covered = DeckPlannerSong(
        music_id=1,
        title="有封面的歌曲",
        music_cover_path="cover.png",
        difficulty="expert",
        rows=[DeckPlannerBoostRow(boost=10, point_per_play=91_000, plays=97, energy=970)],
    )
    placeholder = DeckPlannerSong(music_id=2, title="无封面的歌曲", difficulty=None, rows=[])

    with Canvas() as populated_canvas:
        draw_event_planner_block(
            _planner(songs=[covered, placeholder]),
            {"cover.png": Image.new("RGBA", (52, 52), (20, 80, 160, 255))},
        )
    populated_image = asyncio.run(populated_canvas.get_img())

    with Canvas() as empty_canvas:
        draw_event_planner_block(_planner(songs=[], target_source=None), {})
    empty_image = asyncio.run(empty_canvas.get_img())

    assert populated_image.width > 0
    assert populated_image.height > empty_image.height
    assert empty_image.width > 0


@pytest.mark.parametrize("text", ["12,345,678", "1,234,567,890", "9,223,372,036,854,775,807", "-"])
def test_planner_daily_point_preserves_all_digits(text: str) -> None:
    with VSplit() as container:
        _draw_planner_number_cell(text, "pt/日", 140, TextStyle(size=22))
    number = container.items[0].items[0]
    assert number._get_lines() == [text]
    assert number._get_clip_text_to_width_idx(text, 136) is None
