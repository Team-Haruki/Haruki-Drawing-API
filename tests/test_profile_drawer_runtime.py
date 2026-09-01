from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

from PIL import Image
import pytest

from src.core.image_payload import EncodedImagePayload
from src.sekai.base.plot import Canvas, Frame, HSplit, ImageBg, ImageBox, Spacer, VSplit
from src.sekai.profile import drawer
from src.sekai.profile.model import (
    BasicProfile,
    CardFullThumbnailRequest,
    CharacterRank,
    MultiLiveTopScoreCount,
    MusicClearCount,
    ProfileBgSettings,
    ProfileCardRequest,
    ProfileDataSource,
    ProfileRequest,
    SoloLiveRank,
)


def _image(size: tuple[int, int] = (32, 32), color=(20, 40, 60, 255)) -> Image.Image:
    return Image.new("RGBA", size, color)


def _walk_widgets(widget):
    yield widget
    for child in getattr(widget, "items", []):
        yield from _walk_widgets(child)


def _request(*, vertical: bool = False, background: str | None = None) -> ProfileRequest:
    return ProfileRequest(
        profile=BasicProfile(
            id="1234567890123456",
            region="jp",
            nickname="Player",
            is_hide_uid=True,
            leader_image_path="avatar.png",
        ),
        rank=321,
        twitter_id="haruki",
        word="hello",
        pcards=[],
        bg_settings=ProfileBgSettings(img_path=background, vertical=vertical, alpha=120, blur=2),
        music_difficulty_count=[MusicClearCount(difficulty="expert", clear=3, fc=2, ap=1)],
        character_rank=[CharacterRank(character_id=1, rank=50)],
        lv_rank_bg_path="rank.png",
        x_icon_path="x.png",
        icon_clear_path="clear.png",
        icon_fc_path="fc.png",
        icon_ap_path="ap.png",
        chara_rank_icon_path_map={1: "chara.png"},
    )


class _RecordingPainter:
    def __init__(self) -> None:
        self.calls: list[tuple[str, tuple, dict]] = []

    def __getattr__(self, name: str):
        def record(*args, **kwargs):
            self.calls.append((name, args, kwargs))

        return record


@pytest.mark.anyio
async def test_profile_asset_layer_loaders_and_thumbnail_drawing_cover_optional_layers(monkeypatch) -> None:
    requested: list[str | None] = []

    async def fake_asset(_root, path, **_kwargs):
        requested.append(path)
        return _image((40, 50))

    monkeypatch.setattr(drawer, "get_asset_image_ref", fake_asset)
    monkeypatch.setattr(drawer, "ascender_top_to_painter_y", lambda *_args: 17)

    request = CardFullThumbnailRequest(
        card_id=1,
        card_thumbnail_path="base.png",
        rare="rarity_birthday",
        frame_img_path="frame.png",
        attr_img_path="attr.png",
        rare_img_path="star.png",
        birthday_icon_path="birthday.png",
        train_rank=3,
        train_rank_img_path="rank.png",
        level=60,
        custom_text="MAX",
        is_pcard=True,
    )
    layers = await drawer.get_card_full_thumbnail_layers(request)
    assert requested == ["base.png", "birthday.png", "frame.png", "rank.png", "attr.png"]

    painter = _RecordingPainter()
    drawer.CardFullThumbnailBox(layers, size=(80, 100), shadow=True)._draw_content(painter)
    names = [name for name, _args, _kwargs in painter.calls]
    assert names[0:3] == ["shadow_roundrect", "push_clip_roundrect", "paste"]
    assert "text" in names
    assert names[-1] == "pop_clip"
    assert names.count("paste_with_alpha_blend") >= 4

    plain = request.model_copy(
        update={
            "rare": "rarity_1",
            "birthday_icon_path": None,
            "frame_img_path": "",
            "attr_img_path": "",
            "train_rank": None,
            "train_rank_img_path": None,
            "custom_text": None,
            "is_pcard": False,
        }
    )
    plain_layers = await drawer.get_card_full_thumbnail_layers(plain)
    plain_painter = _RecordingPainter()
    drawer.CardFullThumbnailBox(plain_layers, size=(80, 100))._draw_content(plain_painter)
    assert "text" not in [name for name, _args, _kwargs in plain_painter.calls]


@pytest.mark.anyio
async def test_player_frame_loader_widget_and_nine_slice_drawing(monkeypatch) -> None:
    async def fake_asset(_root, path, **_kwargs):
        return _image((24 if path == "base" else 10, 24 if path == "base" else 12))

    monkeypatch.setattr(drawer, "get_asset_image_ref", fake_asset)
    paths = SimpleNamespace(
        base="base",
        centertop="ct",
        leftbottom="lb",
        lefttop="lt",
        rightbottom="rb",
        righttop="rt",
    )
    layers = await drawer.get_player_frame_layers(paths)
    box = drawer.PlayerFrameBox(layers, 100)
    assert box._get_content_size() == (140, 140)
    painter = _RecordingPainter()
    box._draw_content(painter)
    assert len([name for name, _args, _kwargs in painter.calls if name == "paste_with_alpha_blend"]) == 13

    avatar = await drawer.get_avatar_widget_with_frame(True, paths, _image(), 80, [])
    assert isinstance(avatar, Frame)
    assert len(avatar.items) == 2
    no_frame = await drawer.get_avatar_widget_with_frame(False, paths, _image(), 80, [])
    assert len(no_frame.items) == 1


@pytest.mark.anyio
async def test_cached_profile_module_uses_memory_disk_and_render_paths(monkeypatch) -> None:
    cached = _image((1, 1), (1, 2, 3, 255))
    disk = _image((1, 1), (4, 5, 6, 255))
    rendered = _image((2, 2), (7, 8, 9, 255))
    monkeypatch.setattr(drawer, "build_rendered_image_cache_key", lambda *_args, **_kwargs: "key")

    monkeypatch.setattr(drawer, "get_composed_image_cached", lambda _key: cached)
    assert await drawer._build_cached_profile_module_image("profile", {}, lambda: None) is cached

    writes: list[tuple[str, object]] = []
    monkeypatch.setattr(drawer, "get_composed_image_cached", lambda _key: None)
    monkeypatch.setattr(drawer, "get_composed_image_disk_cached", lambda *_args: disk)
    monkeypatch.setattr(drawer, "put_composed_image_cache", lambda key, value: writes.append((key, value)))
    assert await drawer._build_cached_profile_module_image("profile", {}, lambda: None) is disk
    assert writes == [("key", disk)]

    async def build_widget():
        return Spacer(2, 2)

    monkeypatch.setattr(drawer, "get_composed_image_disk_cached", lambda *_args: None)
    monkeypatch.setattr(drawer, "_render_profile_widget_image", lambda *_args, **_kwargs: _async_value(rendered))
    monkeypatch.setattr(
        drawer,
        "put_composed_image_disk_cache",
        lambda namespace, key, value: writes.append((f"{namespace}:{key}", value)),
    )
    assert await drawer._build_cached_profile_module_image("profile", {}, build_widget, scale=2) is rendered
    assert writes[-2:] == [("key", rendered), ("profile:key", rendered)]
    assert isinstance(drawer._build_cached_profile_module_widget(rendered), ImageBox)


async def _async_value(value):
    return value


@pytest.mark.anyio
async def test_profile_canvas_builds_horizontal_vertical_and_missing_backgrounds(monkeypatch) -> None:
    async def fake_asset(_root, path, **kwargs):
        if path == "missing-bg.png" and kwargs.get("on_missing") == "raise":
            raise FileNotFoundError(path)
        size = (180, 60) if path == "rank.png" else (32, 32)
        return _image(size)

    monkeypatch.setattr(drawer, "get_asset_image_ref", fake_asset)
    monkeypatch.setattr(drawer, "_profile_stats_badge_width", lambda text, font_size=18: len(text) * 5 + font_size)

    horizontal = await drawer._build_profile_canvas(_request())
    assert isinstance(horizontal, Canvas)
    assert any(isinstance(item, HSplit) for item in _walk_widgets(horizontal))

    vertical = await drawer._build_profile_canvas(_request(vertical=True, background="background.png"))
    assert isinstance(vertical.bg, ImageBg)
    vertical_root = next(item for item in _walk_widgets(vertical) if isinstance(item, VSplit) and len(item.items) == 3)
    assert all(item.bg is None for item in vertical_root.items)

    missing = await drawer._build_profile_canvas(_request(background="missing-bg.png"))
    assert missing.bg is drawer.SEKAI_BLUE_BG


@pytest.mark.anyio
async def test_profile_growth_modules_cover_solo_multi_icons_and_empty_paths(monkeypatch) -> None:
    async def fake_asset(_root, path, **_kwargs):
        return _image((96, 48), color=(len(path), 0, 0, 255))

    monkeypatch.setattr(drawer, "get_asset_image_ref", fake_asset)
    monkeypatch.setattr(drawer, "_profile_stats_badge_width", lambda text, font_size=18: len(text) * 5 + font_size)
    request = _request()
    base_ctx = drawer._ProfileLayoutContext(
        request=request,
        profile=request.profile,
        avatar_img=_image(),
        ui_bg=drawer.roundrect_bg(),
        pcards=[],
        honors=[],
        diff_count=drawer._build_profile_diff_count(request.music_difficulty_count),
        character_rank=drawer._build_profile_character_rank_lookup(request.character_rank),
        solo_live=None,
        multi_live=None,
    )

    empty_request = request.model_copy(update={"chara_rank_icon_path_map": {}})
    empty_ctx = replace(base_ctx, request=empty_request)
    assert await drawer._preload_profile_chara_icons(empty_ctx) == {}

    solo = SoloLiveRank(character_id=1, score=123456, rank=10)
    solo_ctx = replace(base_ctx, solo_live=solo)
    solo_panel = await drawer._build_profile_growth_content_module(solo_ctx)
    assert len(solo_panel.items) == 2

    multi = MultiLiveTopScoreCount(mvp=4, super_star=5)
    multi_ctx = replace(base_ctx, multi_live=multi)
    multi_panel = await drawer._build_profile_growth_content_module(multi_ctx)
    assert len(multi_panel.items) == 2

    icon_cache = {"chara.png": _image((96, 48))}
    assert len(drawer._build_profile_character_grid_module(base_ctx, icon_cache).items) == len(drawer.CHARA_LIST)
    assert len(drawer._build_profile_multi_live_module(None, multi, 120).items) == 3
    no_icon_ctx = replace(solo_ctx, request=empty_request)
    solo_module = drawer._build_profile_solo_live_module(no_icon_ctx, {}, None, None, 0)
    assert isinstance(solo_module.items[1].items[0], Spacer)


@pytest.mark.anyio
async def test_profile_payload_and_compose_routes_cover_enabled_and_disabled(monkeypatch) -> None:
    request = _request()
    canvas = Canvas().set_size((2, 2))
    monkeypatch.setattr(drawer, "_build_profile_canvas", lambda _request: _async_value(canvas))

    monkeypatch.setattr(drawer, "skia_plot_enabled", lambda: False)
    assert await drawer.try_render_profile_payload(request) is None

    payload = EncodedImagePayload(
        image_bytes=b"png",
        media_type="image/png",
        filename="profile.png",
        image_width=1,
        image_height=1,
        image_mode="RGBA",
        encode_elapsed=0.0,
    )
    monkeypatch.setattr(drawer, "skia_plot_enabled", lambda: True)
    monkeypatch.setattr(drawer, "render_canvas_payload", lambda *_args, **_kwargs: _async_value(payload))
    assert await drawer.try_render_profile_payload(request) is payload

    expected = _image((3, 3))

    class FakeCanvas:
        async def get_img(self, scale):
            assert scale == drawer._PROFILE_SCALE
            return expected

    monkeypatch.setattr(drawer, "_build_profile_canvas", lambda _request: _async_value(FakeCanvas()))
    assert await drawer.compose_profile_image(request) is expected


@pytest.mark.anyio
async def test_profile_card_modules_cover_absent_complete_and_error_cases(monkeypatch) -> None:
    async def fake_asset(_root, _path, **_kwargs):
        return _image()

    monkeypatch.setattr(drawer, "get_asset_image_ref", fake_asset)
    empty = ProfileCardRequest()
    assert await drawer._build_profile_card_modules(empty) == []
    assert drawer._build_profile_card_identity_module(empty, []) is None
    assert drawer._build_profile_card_error_module(empty) is None
    assert await drawer._build_profile_card_avatar_module(empty) is None

    complete = ProfileCardRequest(
        timezone="Asia/Shanghai",
        bg_alpha=0,
        profile=BasicProfile(
            id="1234567890123456",
            region="cn",
            nickname="A very long nickname",
            leader_image_path="avatar.png",
        ),
        data_sources=[
            ProfileDataSource(name="Suite数据", update_time=1_700_000_000_000),
            ProfileDataSource(name="Toolbox", update_time=1_700_000_100_000),
        ],
        mysekai_level=12,
        error_message="partial data",
    )
    modules = await drawer._build_profile_card_modules(complete)
    assert len(modules) == 3
    assert isinstance(await drawer.get_profile_card(complete), Frame)
    assert drawer._profile_card_data_source_label(None) == "数据"
    assert drawer._profile_card_data_source_label("Suite数据") == "Suite"
    assert drawer._profile_card_data_source_label("Toolbox") == "Toolbox"

    assert drawer.process_hide_uid(True, "1234") == "*" * 16
    assert drawer.process_hide_uid(False, "1234") == "1234"
