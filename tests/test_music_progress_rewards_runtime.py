from __future__ import annotations

from PIL import Image
import pytest

from src.core.image_payload import EncodedImagePayload
from src.sekai.base.plot import Canvas, HSplit
from src.sekai.music import drawer
from src.sekai.music.model import (
    BasicMusicRewardsRequest,
    DetailMusicRewardsRequest,
    MusicComboReward,
    PlayProgressCount,
    PlayProgressRequest,
)
from src.sekai.profile.model import ProfileCardRequest


async def _async_value(value):
    return value


def _profile() -> ProfileCardRequest:
    return ProfileCardRequest()


def _image() -> Image.Image:
    return Image.new("RGBA", (16, 16), (10, 20, 30, 255))


@pytest.mark.anyio
async def test_play_progress_builder_draws_profile_icons_and_all_result_segments(monkeypatch) -> None:
    profiles: list[ProfileCardRequest] = []
    paths: list[str] = []

    async def fake_profile(profile):
        profiles.append(profile)

    async def fake_asset(_root, path):
        paths.append(path)
        return _image()

    monkeypatch.setattr(drawer, "get_profile_card", fake_profile)
    monkeypatch.setattr(drawer, "get_asset_image_ref", fake_asset)
    request = PlayProgressRequest(
        profile=_profile(),
        difficulty="append",
        counts=[
            PlayProgressCount(level=31, total=10, not_clear=1, clear=9, fc=7, ap=4),
            PlayProgressCount(level=32, total=5, not_clear=5, clear=0, fc=0, ap=0),
        ],
    )

    canvas = await drawer._build_play_progress_canvas(request)

    assert isinstance(canvas, Canvas)
    assert profiles == [request.profile]
    assert [path.rsplit("/", 1)[-1] for path in paths] == [
        "icon_not_clear.png",
        "icon_clear.png",
        "icon_fc.png",
        "icon_ap.png",
    ]


@pytest.mark.anyio
async def test_detail_rewards_builder_draws_default_and_custom_icons_with_running_totals(monkeypatch) -> None:
    profiles: list[ProfileCardRequest] = []
    paths: list[str] = []

    async def fake_profile(profile):
        profiles.append(profile)

    async def fake_asset(_root, path):
        paths.append(path)
        return _image()

    monkeypatch.setattr(drawer, "get_profile_card", fake_profile)
    monkeypatch.setattr(drawer, "get_asset_image_ref", fake_asset)
    rewards = {
        difficulty: [MusicComboReward(level=level, reward=amount), MusicComboReward(level=level + 1, reward=amount + 1)]
        for difficulty, level, amount in (
            ("hard", 20, 10),
            ("expert", 25, 20),
            ("master", 30, 30),
            ("append", 34, 40),
        )
    }
    request = DetailMusicRewardsRequest(profile=_profile(), rank_rewards=100, combo_rewards=rewards)

    canvas = await drawer._build_detail_music_rewards_canvas(request)

    assert isinstance(canvas, Canvas)
    assert profiles == [request.profile]
    assert paths == [f"{drawer.RESULT_ASSET_PATH}/jewel.png", f"{drawer.RESULT_ASSET_PATH}/shard.png"]

    paths.clear()
    custom = request.model_copy(update={"jewel_icon_path": "custom/jewel.png", "shard_icon_path": "custom/shard.png"})
    assert isinstance(await drawer._build_detail_music_rewards_canvas(custom), Canvas)
    assert paths == ["custom/jewel.png", "custom/shard.png"]


@pytest.mark.anyio
async def test_basic_rewards_builder_and_text_icon_cover_optional_text(monkeypatch) -> None:
    async def fake_asset(_root, _path):
        return _image()

    monkeypatch.setattr(drawer, "get_profile_card", lambda _profile: _async_value(None))
    monkeypatch.setattr(drawer, "get_asset_image_ref", fake_asset)
    request = BasicMusicRewardsRequest(
        profile=_profile(),
        rank_rewards="400 (4×100)",
        combo_rewards={
            "hard": "10",
            "expert": "20",
            "master": "30",
            "append": "40",
        },
    )

    assert isinstance(await drawer._build_basic_music_rewards_canvas(request), Canvas)
    style = drawer.TextStyle(drawer.DEFAULT_FONT, 12, (1, 2, 3))
    assert isinstance(drawer.draw_text_icon("reward", _image(), style), HSplit)
    assert isinstance(drawer.draw_text_icon(None, _image(), style), HSplit)


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("compose_name", "native_name", "builder_name", "rqd"),
    [
        (
            "compose_play_progress_image",
            "try_render_play_progress_payload",
            "_build_play_progress_canvas",
            PlayProgressRequest(profile=_profile(), counts=[]),
        ),
        (
            "compose_detail_music_rewards_image",
            "try_render_detail_music_rewards_payload",
            "_build_detail_music_rewards_canvas",
            DetailMusicRewardsRequest(profile=_profile()),
        ),
        (
            "compose_basic_music_rewards_image",
            "try_render_basic_music_rewards_payload",
            "_build_basic_music_rewards_canvas",
            BasicMusicRewardsRequest(profile=_profile()),
        ),
    ],
)
async def test_music_progress_and_reward_wrappers_cover_pillow_disabled_and_native_routes(
    monkeypatch,
    compose_name,
    native_name,
    builder_name,
    rqd,
) -> None:
    expected = _image()

    class FakeCanvas:
        async def get_img(self):
            return expected

    fake_canvas = FakeCanvas()
    monkeypatch.setattr(drawer, builder_name, lambda _request: _async_value(fake_canvas))
    assert await getattr(drawer, compose_name)(rqd) is expected

    monkeypatch.setattr(drawer, "skia_plot_enabled", lambda: False)
    assert await getattr(drawer, native_name)(rqd) is None

    payload = EncodedImagePayload(b"x", "image/png", "x.png", 1, 1, "RGBA", 0)
    observed: list[tuple[object, str]] = []

    async def fake_render(canvas, *, endpoint):
        observed.append((canvas, endpoint))
        return payload

    monkeypatch.setattr(drawer, "skia_plot_enabled", lambda: True)
    monkeypatch.setattr(drawer, "render_canvas_payload", fake_render)
    assert await getattr(drawer, native_name)(rqd) is payload
    assert observed[-1][0] is fake_canvas
