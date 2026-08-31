from __future__ import annotations

import asyncio

from PIL import Image

import src.sekai.music.drawer as music_drawer
from src.sekai.music.model import MusicListRequest


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
