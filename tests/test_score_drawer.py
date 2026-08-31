from __future__ import annotations

import asyncio

from PIL import Image
import pytest

import src.sekai.score.drawer as score_drawer
from src.sekai.score.model import CustomRoomScoreRequest, MusicBoardItem, MusicBoardRequest


def test_custom_room_score_canvas_covers_music_and_empty_rows(tmp_path, monkeypatch) -> None:
    Image.new("RGBA", (32, 32), (20, 80, 160, 255)).save(tmp_path / "cover_one.png")
    Image.new("RGBA", (32, 32), (160, 80, 20, 255)).save(tmp_path / "cover_two.png")
    monkeypatch.setattr(score_drawer, "ASSETS_BASE_DIR", tmp_path)
    request = CustomRoomScoreRequest(
        target_point=12345,
        candidate_pairs=[(100, 25), (200, 50)],
        music_list_map={
            100: [
                {"music_title": "First", "music_cover": "cover_one.png"},
                {"music_title": "Second", "music_cover": "cover_two.png"},
            ]
        },
    )

    canvas = asyncio.run(score_drawer._build_custom_room_score_control_canvas(request))
    image = asyncio.run(canvas.get_img())

    assert image.width > 0
    assert image.height > 0


def test_load_custom_room_covers_accepts_an_empty_map() -> None:
    assert asyncio.run(score_drawer._load_custom_room_covers({})) == {}

    request = CustomRoomScoreRequest(target_point=0, candidate_pairs=[], music_list_map={})
    assert asyncio.run(score_drawer._build_custom_room_score_control_canvas(request)) is not None


@pytest.mark.parametrize("target", ["score", "pt", "pt/time", "time"])
def test_music_board_canvas_covers_dynamic_column_sets(target, monkeypatch) -> None:
    async def fake_image_loader(_base_dir, _path):
        return Image.new("RGBA", (40, 40), (20, 80, 160, 255))

    monkeypatch.setattr(score_drawer, "get_asset_image_ref", fake_image_loader)
    item = MusicBoardItem(
        rank=1,
        music_id=10,
        difficulty="master",
        level=30,
        music_title="Test Song",
        music_cover_path="cover.png",
        live_type_pt=123,
        live_type_real_score=456,
        live_type_score=0.75,
        live_type_skill_account=0.25,
        live_type_pt_per_hour=789,
        play_count_per_hour=12.5,
        event_rate=220,
        music_time=125.4,
        tps=8.2,
    )
    request = MusicBoardRequest(
        live_type="multi",
        target=target,
        ascend=False,
        page=1,
        total_page=1,
        title_text="Music Board",
        description="Description",
        items=[item],
        spec_mid_diffs=[(10, "master")],
    )

    canvas = asyncio.run(score_drawer._build_music_board_canvas(request))
    image = asyncio.run(canvas.get_img())

    assert image.width > 0
    assert image.height > 0
