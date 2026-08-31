from __future__ import annotations

import asyncio

from PIL import Image

import src.sekai.score.drawer as score_drawer
from src.sekai.score.model import CustomRoomScoreRequest


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
