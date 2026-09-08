from __future__ import annotations

import asyncio

from PIL import Image
import pytest

from src.sekai.music import drawer
from src.sekai.music.model import (
    CustomChartInfo,
    DifficultyInfo,
    LeaderboardInfo,
    MusicDetailRequest,
    MusicMD,
    MusicVocalInfo,
)


def _request(**overrides) -> MusicDetailRequest:
    values = {
        "region": "jp",
        "music_info": MusicMD(
            id=123,
            title="Test Song",
            composer="Composer",
            lyricist="Lyricist",
            arranger="Arranger",
            mv_info=["original", "mv", "mv_2d", "unknown"],
            categories=[],
            release_at=1_710_000_000_000,
            is_full_length=True,
        ),
        "vocal": MusicVocalInfo(
            vocal_info={
                "sekai": {
                    "caption": "SEKAI ver.",
                    "characters": [
                        {"characterName": "Miku"},
                        {"characterName": "Singer"},
                    ],
                }
            },
            vocal_assets={"Miku": "logo.png", "Missing": "missing.png"},
        ),
        "alias": ["测试别名", "Another Alias"],
        "length": "123.4秒",
        "bpm": 180,
        "difficulty": DifficultyInfo(
            level=[5, 10, 15, 25, 31, 34],
            note_count=[100, 200, 300, 400, 500, 600],
            has_append=True,
            order=["easy", "normal", "hard", "expert", "master", "append"],
        ),
        "event_id": 99,
        "cn_name": "测试歌曲",
        "music_jacket_path": "jacket.png",
        "event_banner_path": "banner.png",
        "limited_times": [(1_710_000_000_000, 1_710_003_600_000)],
        "leaderboard_matrix": [
            [LeaderboardInfo(rank=1, diff="master", value="100%"), None],
            [None, LeaderboardInfo(rank=10, diff="append", value="90%")],
        ],
        "leaderboard_music_num": 10,
        "leaderboard_live_types": {"solo": "单人", "extra": "额外"},
        "leaderboard_targets": {"score": "分数", "pt": "PT"},
        "title": "附加标题",
        "title_style": {"font": drawer.DEFAULT_BOLD_FONT, "size": 24, "color": (1, 2, 3)},
        "title_shadow": True,
    }
    values.update(overrides)
    return MusicDetailRequest(**values)


def _asset(color: str = "blue") -> Image.Image:
    return Image.new("RGBA", (64, 64), color)


def test_load_music_detail_assets_loads_standard_optional_assets(monkeypatch):
    calls = []

    async def load(base_dir, path):
        assert base_dir == drawer.ASSETS_BASE_DIR
        calls.append(path)
        if path == "missing.png":
            return None
        return _asset()

    monkeypatch.setattr(drawer, "get_asset_image_ref", load)
    assets = asyncio.run(drawer._load_music_detail_assets(_request()))

    assert calls == ["jacket.png", "logo.png", "missing.png", "banner.png"]
    assert list(assets.vocal_logos) == ["Miku"]
    assert assets.event_banner is not None


def test_load_music_detail_assets_skips_standard_only_assets_for_custom_chart(monkeypatch):
    calls = []

    async def load(_base_dir, path):
        calls.append(path)
        return _asset()

    custom_chart = CustomChartInfo(score_id="custom", bpm="200", published_at=1_720_000_000_000)
    monkeypatch.setattr(drawer, "get_asset_image_ref", load)
    assets = asyncio.run(drawer._load_music_detail_assets(_request(custom_chart_info=custom_chart)))

    assert calls == ["jacket.png"]
    assert assets.vocal_logos == {}
    assert assets.event_banner is None


def test_music_detail_renderer_normalizes_display_values_and_leaderboard():
    request = _request()
    renderer = drawer._MusicDetailRenderer(
        request,
        drawer._MusicDetailAssets(_asset(), {"Miku": _asset("red")}, _asset("green")),
    )

    assert renderer.name == "Test Song [FULL]"
    assert renderer.bpm_main == "180 BPM"
    assert renderer.event_id == 99
    assert renderer._mv_text() == "原版MV & 3DMV & 2DMV"
    assert renderer._difficulty_order() == (
        ["easy", "normal", "hard", "expert", "master", "append"],
        True,
    )
    assert renderer._leaderboard_keys() == (["solo", "extra"], ["score", "pt"])
    assert renderer._leaderboard_cell(0, 0)[:3] == (0.0, "#1", "100%")
    assert renderer._leaderboard_cell(0, 1) == (0.5, "-", None, (50, 50, 50))
    assert renderer._leaderboard_cell(9, 9) == (0.5, "-", None, (50, 50, 50))
    assert renderer._leaderboard_bg(0.25) is not None
    assert renderer._leaderboard_bg(0.75) is not None


def test_music_detail_renderer_defaults_missing_mv_bpm_and_append():
    music_info = _request().music_info.model_copy(update={"mv_info": None, "is_full_length": False})
    difficulty = DifficultyInfo.model_construct(
        level=[1, 2, 3, 4, 5, None],
        note_count=[10, None, 30, 40, 50, None],
        has_append=False,
        order=None,
    )
    request = _request(
        music_info=music_info,
        bpm=None,
        difficulty=difficulty,
        leaderboard_matrix=None,
        leaderboard_live_types=None,
        leaderboard_targets=None,
        event_id=None,
        event_banner_path=None,
        alias=None,
        limited_times=None,
        title=None,
        title_style=None,
        title_shadow=False,
    )
    renderer = drawer._MusicDetailRenderer(request, drawer._MusicDetailAssets(_asset(), {}, None))

    assert renderer.name == "Test Song"
    assert renderer.bpm_main == "?"
    assert renderer._mv_text() == "无"
    assert renderer._difficulty_order() == (["easy", "normal", "hard", "expert", "master"], False)


def test_music_detail_helpers_cover_default_and_edge_values():
    assert drawer._music_list_group_order("easy", 5) == (1, 5)
    assert drawer._music_list_group_order("unknown", 1) == (99, 1)
    assert drawer._ordered_music_detail_leaderboard_keys(None, ("solo",)) == []
    assert drawer._item_at(["first"], 0) == "first"
    assert drawer._item_at(["first"], 5, "missing") == "missing"
    assert drawer._custom_chart_stat_text(None) == "-"
    assert drawer._custom_chart_stat_text(1.25) == "1.25"
    assert drawer._custom_chart_stat_text("  ") == "-"

    assert (
        drawer._build_caption_vocals(
            {
                "empty": {"caption": "ver.", "characters": [None, {}, {"characterName": ""}]},
                "invalid": "ignored",
            },
            {},
        )
        == {}
    )


def test_music_detail_renderer_uses_custom_chart_overrides_and_defaults():
    custom = CustomChartInfo(
        score_id="custom",
        title="Chart",
        author="Author",
        description="Description",
        bpm="200",
        published_at=1_720_000_000_000,
        play_count=1,
        review_count=2,
        full_combo_rate=1.5,
        difficulty="unknown",
        play_level=None,
        note_count=None,
        tags=["tag", " "],
    )
    request = _request(custom_chart_info=custom)
    renderer = drawer._MusicDetailRenderer(request, drawer._MusicDetailAssets(_asset(), {}, None))

    assert renderer.bpm_main == "200 BPM"
    assert renderer.event_id is None
    assert renderer.publish_time != drawer.datetime_from_millis(
        request.music_info.release_at, request.timezone
    ).strftime("%Y-%m-%d %H:%M:%S")
    renderer.build_canvas()

    fallback = custom.model_copy(update={"bpm": None, "published_at": None, "tags": None})
    fallback_renderer = drawer._MusicDetailRenderer(
        _request(custom_chart_info=fallback), drawer._MusicDetailAssets(_asset(), {}, None)
    )
    assert fallback_renderer.bpm_main == "180 BPM"
    drawer._draw_custom_chart_info(_request(custom_chart_info=None), 100, 100)
    drawer._draw_custom_chart_difficulty(_request(custom_chart_info=None), 100, 100)
    drawer._draw_custom_chart_tags(None, 100)


def test_music_detail_renderer_supports_direct_vocal_name_and_long_text(monkeypatch):
    renderer = drawer._MusicDetailRenderer(_request(), drawer._MusicDetailAssets(_asset(), {}, None))
    renderer.caption_vocals = {"Direct": [{"vocal_name": "Very long vocalist name" * 20}]}
    monkeypatch.setattr(drawer, "get_text_size", lambda *_args, **_kwargs: (10_000, 20))

    renderer._draw_vocal()


def test_draw_request_title_covers_default_and_unshadowed_styles():
    drawer._draw_rqd_title(_request(title_style=None, title_shadow=True))
    drawer._draw_rqd_title(_request(title_style=None, title_shadow=False))


@pytest.mark.anyio
async def test_compose_standard_music_detail_exercises_event_and_leaderboard(monkeypatch):
    async def load(_base_dir, path):
        return None if path == "missing.png" else _asset()

    monkeypatch.setattr(drawer, "get_asset_image_ref", load)
    image = await drawer.compose_music_detail_image(_request())

    assert image.width > 0
    assert image.height > 0


@pytest.mark.anyio
async def test_compose_standard_music_detail_without_event_or_leaderboard(monkeypatch):
    async def load(_base_dir, _path):
        return _asset()

    request = _request(
        event_id=None,
        event_banner_path=None,
        leaderboard_matrix=None,
        leaderboard_live_types=None,
        leaderboard_targets=None,
        alias=None,
        limited_times=None,
        title=None,
    )
    monkeypatch.setattr(drawer, "get_asset_image_ref", load)
    image = await drawer.compose_music_detail_image(request)

    assert image.width > 0
    assert image.height > 0


def test_try_render_music_detail_payload_respects_skia_gate(monkeypatch):
    build_calls = []

    async def build(request):
        build_calls.append(request)
        return "canvas"

    monkeypatch.setattr(drawer, "_build_music_detail_canvas", build)
    monkeypatch.setattr(drawer, "skia_plot_enabled", lambda: False)
    assert asyncio.run(drawer.try_render_music_detail_payload("disabled")) is None
    assert build_calls == []

    payload = object()

    async def render(canvas, *, endpoint):
        assert canvas == "canvas"
        assert endpoint == "music_detail"
        return payload

    monkeypatch.setattr(drawer, "skia_plot_enabled", lambda: True)
    monkeypatch.setattr(drawer, "render_canvas_payload", render)
    assert asyncio.run(drawer.try_render_music_detail_payload("enabled")) is payload
    assert build_calls == ["enabled"]
