from __future__ import annotations

import asyncio

from PIL import Image

import src.sekai.music.drawer as music_drawer
from src.sekai.music.model import DifficultyInfo, MusicBriefList, MusicBriefListRequest, MusicListRequest, MusicMD


def _music_info(music_id: int, *, release_at: int = 1_710_000_000_000) -> MusicMD:
    return MusicMD(
        id=music_id,
        title=f"Music {music_id}",
        composer="Composer",
        lyricist="Lyricist",
        arranger="Arranger",
        categories=[],
        release_at=release_at,
        is_full_length=False,
    )


def test_music_brief_helpers_normalize_dates_and_difficulty_levels() -> None:
    fallback = MusicBriefList(id=1, level=30, music_jacket_path="one.png")
    difficulty = MusicBriefList(
        id=2,
        level=0,
        music_jacket_path="two.png",
        music_info=_music_info(2),
        difficulty=DifficultyInfo(level=[5, 0, 7], note_count=[], has_append=True, order=["easy", "hard"]),
    )

    assert music_drawer._music_brief_release_date(fallback, "Asia/Shanghai") == ""
    assert music_drawer._music_brief_release_date(difficulty, "Asia/Shanghai") == "2024-03-10"
    assert music_drawer._music_brief_difficulty_levels(fallback, "master") == [("master", 30)]
    assert music_drawer._music_brief_difficulty_levels(fallback.model_copy(update={"level": 0}), "") == []
    assert music_drawer._music_brief_difficulty_levels(difficulty, "master") == [("easy", 5), ("append", 7)]


def test_music_brief_canvas_renders_profile_results_and_all_difficulty_shapes(monkeypatch) -> None:
    loaded_paths: list[str] = []
    profile = object()
    received_profiles: list[object] = []

    async def fake_image_loader(_base_dir, path: str):
        loaded_paths.append(path)
        if path.endswith("icon_missing.png"):
            return None
        return Image.new("RGBA", (96, 96), (20, 80, 160, 255))

    async def fake_profile_card(received):
        received_profiles.append(received)

    monkeypatch.setattr(music_drawer, "get_asset_image_ref", fake_image_loader)
    monkeypatch.setattr(music_drawer, "get_profile_card", fake_profile_card)
    request = MusicBriefListRequest(
        region="JP",
        required_difficulty="master",
        music_list=[
            MusicBriefList(
                id=1,
                level=0,
                music_jacket_path="one.png",
                music_info=_music_info(1),
                difficulty=DifficultyInfo(
                    level=[5, 0, 7],
                    note_count=[],
                    has_append=True,
                    order=["easy", "hard"],
                ),
                play_result="ap",
            ),
            MusicBriefList(id=2, level=30, music_jacket_path="two.png", play_result="missing"),
            MusicBriefList(
                id=3,
                level=0,
                music_jacket_path="three.png",
                difficulty=DifficultyInfo(level=[10], note_count=[], has_append=False),
            ),
        ],
    )
    object.__setattr__(request, "profile", profile)

    image = asyncio.run(music_drawer.compose_music_brief_list_image(request))

    assert image.width > 0
    assert image.height > 0
    assert received_profiles == [profile]
    assert loaded_paths[:3] == ["one.png", "two.png", "three.png"]
    assert any(path.endswith("/icon_ap.png") for path in loaded_paths)
    assert any(path.endswith("/icon_missing.png") for path in loaded_paths)


def test_music_brief_native_payload_honors_the_skia_gate(monkeypatch) -> None:
    request = MusicBriefListRequest(region="JP", music_list=[])
    monkeypatch.setattr(music_drawer, "skia_plot_enabled", lambda: False)

    assert asyncio.run(music_drawer.try_render_music_brief_list_payload(request)) is None

    payload = object()

    async def fake_render(_canvas, endpoint: str):
        assert endpoint == "music_brief_list"
        return payload

    monkeypatch.setattr(music_drawer, "skia_plot_enabled", lambda: True)
    monkeypatch.setattr(music_drawer, "render_canvas_payload", fake_render)

    assert asyncio.run(music_drawer.try_render_music_brief_list_payload(request)) is payload


def test_music_list_canvas_groups_entries_and_resolves_result_icons(monkeypatch) -> None:
    loaded_paths: list[str] = []

    async def fake_image_loader(_base_dir, path: str):
        loaded_paths.append(path)
        return Image.new("RGBA", (64, 64), (20, 80, 160, 255))

    monkeypatch.setattr(music_drawer, "get_asset_image_ref", fake_image_loader)
    request = MusicListRequest(
        user_results={1: "fc", 2: "ap"},
        music_list=[
            {"id": 2, "difficulty": 30, "difficulty_type": "master", "release_at": 2000},
            {"id": 1, "difficulty": 30, "difficulty_type": "master", "release_at": 1000},
            {"id": 3, "difficulty": 29, "difficulty_type": "", "release_at": 3000},
        ],
        jackets_path_list={1: "jacket-one.png", 2: "jacket-two.png", 3: "jacket-three.png"},
        required_difficulties="expert",
        play_result_icon_path_map={"fc": "custom-fc.png"},
    )

    canvas = asyncio.run(music_drawer._build_music_list_canvas(request))
    image = asyncio.run(canvas.get_img())

    assert image.width > 0
    assert image.height > 0
    assert loaded_paths[:3] == ["jacket-one.png", "jacket-two.png", "jacket-three.png"]
    assert "custom-fc.png" in loaded_paths
    assert any(path.endswith("/icon_ap.png") for path in loaded_paths)
    assert request.music_list[0]["play_result"] == "ap"
    assert request.music_list[2]["play_result"] is None


def test_music_list_helpers_accept_an_empty_request(monkeypatch) -> None:
    async def unused_loader(_base_dir, _path):
        raise AssertionError("empty requests should not load images")

    request = MusicListRequest(
        user_results={},
        music_list=[],
        jackets_path_list={},
        required_difficulties="master",
    )

    assert music_drawer._group_music_list(request) == []
    assert asyncio.run(music_drawer._load_music_list_jackets(request, unused_loader)) == {}


def test_music_list_canvas_renders_an_optional_profile(monkeypatch) -> None:
    profile_request = object()
    received_profiles: list[object] = []

    class _Profile:
        def to_profile_card_request(self):
            return profile_request

    async def fake_profile_card(request):
        received_profiles.append(request)

    monkeypatch.setattr(music_drawer, "get_profile_card", fake_profile_card)
    request = MusicListRequest(
        user_results={},
        music_list=[],
        jackets_path_list={},
        required_difficulties="master",
    )
    object.__setattr__(request, "profile", _Profile())

    image = asyncio.run(music_drawer.compose_music_list_image(request))

    assert image.width > 0
    assert received_profiles == [profile_request]


def test_music_list_native_payload_honors_the_skia_gate(monkeypatch) -> None:
    request = MusicListRequest(
        user_results={},
        music_list=[],
        jackets_path_list={},
        required_difficulties="master",
    )
    monkeypatch.setattr(music_drawer, "skia_plot_enabled", lambda: False)

    assert asyncio.run(music_drawer.try_render_music_list_payload(request)) is None

    payload = object()

    async def fake_render(_canvas, endpoint: str):
        assert endpoint == "music_list"
        return payload

    monkeypatch.setattr(music_drawer, "skia_plot_enabled", lambda: True)
    monkeypatch.setattr(music_drawer, "render_canvas_payload", fake_render)

    assert asyncio.run(music_drawer.try_render_music_list_payload(request)) is payload
