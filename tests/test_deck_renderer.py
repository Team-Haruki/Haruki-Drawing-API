from __future__ import annotations

import asyncio

from PIL import Image
import pytest

from src.sekai.deck import drawer
from src.sekai.deck.model import (
    DeckCardData,
    DeckData,
    DeckPlannerBoostRow,
    DeckPlannerInfo,
    DeckPlannerSong,
    DeckRequest,
)
from src.sekai.profile.model import CardFullThumbnailRequest, DetailedProfileCardRequest


def _asset(color: str = "blue") -> Image.Image:
    return Image.new("RGBA", (96, 96), color)


def _profile() -> DetailedProfileCardRequest:
    return DetailedProfileCardRequest(
        id="1",
        region="jp",
        nickname="Player",
        source="suite",
        update_time=1_710_000_000_000,
        leader_image_path="leader.png",
    )


def _card(**overrides) -> DeckCardData:
    values = {
        "card_thumbnail": CardFullThumbnailRequest(
            card_id=101,
            card_thumbnail_path="card.png",
            rare="rarity_4",
            frame_img_path="frame.png",
            attr_img_path="attr.png",
            rare_img_path="rare.png",
            train_rank=2,
            is_after_training=True,
        ),
        "chara_id": 1,
        "skill_level": "4",
        "skill_rate": 125.5,
        "event_bonus_rate": 25.5,
        "is_before_story": True,
        "is_after_story": False,
        "has_canvas_bonus": True,
    }
    values.update(overrides)
    return DeckCardData(**values)


def _deck(**overrides) -> DeckData:
    values = {
        "card_data": [_card()],
        "music_title": "Song",
        "music_id": 10,
        "music_diff": "master",
        "music_cover_path": "compare.png",
        "music_query": "query",
        "event_bonus_rate": 200.0,
        "support_deck_bonus_rate": 20.0,
        "total_power": 300_000,
        "challenge_score_delta": 123,
        "score": 1_000_000,
        "live_score": 900_000,
        "mysekai_event_point": 5000,
        "multi_live_score_up": 150.0,
    }
    values.update(overrides)
    return DeckData(**values)


def _request(**overrides) -> DeckRequest:
    values = {
        "region": "jp",
        "profile": _profile(),
        "deck_data": [_deck()],
        "is_max_deck": True,
        "recommend_type": "event",
        "event_id": 99,
        "event_name": "Event",
        "live_type": "multi",
        "live_name": "Multi",
        "music_title": "Song",
        "music_id": 10,
        "music_diff": "master",
        "music_cover_path": "music.png",
        "multi_live_teammate_power": 200_000,
        "multi_live_teammate_score_up": 100.0,
        "boost": 3,
        "target": "score",
        "unit_filter": "unit",
        "attr_filter": "cool",
        "unit_logo_path": "unit.png",
        "attr_icon_path": "attr.png",
        "excluded_cards": [999],
        "multi_live_score_up_lower_bound": 100,
        "keep_after_training_state": True,
        "model_name": ["dfs+ga"],
        "canvas_thumbnail_path": "canvas.png",
        "fixed_cards_id": [101],
        "cost_times": {"dfs": 1.25},
        "wait_times": {"dfs": 0.5},
    }
    values.update(overrides)
    return DeckRequest(**values)


def _assets(**overrides) -> drawer._DeckRecommendAssets:
    values = {
        "chara_icon": _asset("red"),
        "wl_chara_icon": _asset("green"),
        "unit_logo": _asset("yellow"),
        "attr_icon": _asset("purple"),
        "music_cover": _asset("orange"),
        "canvas_thumbnail": _asset("cyan"),
        "card_layers": {(101, True, "card.png"): object()},
        "compare_music_imgs": {"compare.png": _asset("pink")},
        "planner_music_imgs": {"planner.png": _asset("gray")},
    }
    values.update(overrides)
    return drawer._DeckRecommendAssets(**values)


def test_load_deck_recommend_assets_collects_unique_sources(monkeypatch):
    calls = []

    async def load(_base_dir, path):
        calls.append(path)
        return _asset()

    async def load_card(card):
        assert card.card_id == 101
        return "layers"

    planner = DeckPlannerInfo(
        target_point=1000,
        remaining_point=500,
        songs=[
            DeckPlannerSong(
                title="Planner",
                music_cover_path="planner.png",
                rows=[DeckPlannerBoostRow(boost=1, point_per_play=100, plays=5, energy=5)],
            )
        ],
    )
    request = _request(
        chara_icon_path="chara.png",
        wl_chara_icon_path="wl.png",
        event_planner=planner,
    )
    monkeypatch.setattr(drawer, "get_asset_image_ref", load)
    monkeypatch.setattr(drawer, "get_card_full_thumbnail_layers", load_card)

    assets = asyncio.run(drawer._load_deck_recommend_assets(request))

    assert calls == ["chara.png", "wl.png", "unit.png", "attr.png", "music.png", "canvas.png", "planner.png"]
    assert assets.card_layers[(101, True, "card.png")] == "layers"
    assert list(assets.planner_music_imgs) == ["planner.png"]


def test_load_deck_recommend_assets_uses_compare_covers(monkeypatch):
    async def load(_base_dir, _path):
        return _asset()

    async def load_card(_card):
        return "layers"

    monkeypatch.setattr(drawer, "get_asset_image_ref", load)
    monkeypatch.setattr(drawer, "get_card_full_thumbnail_layers", load_card)
    assets = asyncio.run(drawer._load_deck_recommend_assets(_request(music_compare=True)))

    assert list(assets.compare_music_imgs) == ["compare.png"]
    assert assets.music_cover is None


@pytest.mark.anyio
@pytest.mark.parametrize(
    "deck_request",
    [
        pytest.param(_request(), id="event"),
        pytest.param(
            _request(
                recommend_type="challenge",
                music_compare=True,
                deck_data=[_deck(music_query=None)],
                chara_name="Miku",
                chara_icon_path="chara.png",
                live_type="solo",
            ),
            id="challenge-compare",
        ),
        pytest.param(
            _request(
                recommend_type="wl_bonus",
                is_wl=True,
                wl_chara_name="Miku",
                wl_chara_icon_path="wl.png",
                target="skill",
            ),
            id="wl-bonus",
        ),
        pytest.param(
            _request(
                recommend_type="mysekai",
                target="total_power",
                live_type="solo",
                music_id=None,
            ),
            id="mysekai",
        ),
        pytest.param(_request(deck_data=[]), id="empty"),
    ],
)
async def test_build_deck_recommend_canvas_covers_render_variants(monkeypatch, deck_request):
    async def load_assets(_request):
        return _assets()

    monkeypatch.setattr(drawer, "_load_deck_recommend_assets", load_assets)
    monkeypatch.setattr(drawer, "CardFullThumbnailBox", lambda *_args, **_kwargs: drawer.Spacer(w=80, h=80))
    canvas = await drawer._build_deck_recommend_canvas(deck_request)
    image = await canvas.get_img()

    assert image.width > 0
    assert image.height > 0


def test_deck_renderer_helpers_cover_story_and_score_defaults():
    request = _request(boost=None)
    deck = _deck(score=None, live_score=None, mysekai_event_point=None)

    assert drawer._deck_score(request, deck, False, 1) == 0
    assert drawer._story_read_color(None) == (255, 255, 255, 255)
    assert drawer._story_read_color(True) == (50, 150, 50, 255)
    assert drawer._story_read_color(False) == (150, 50, 50, 255)
    assert drawer._deck_card_is_fixed(request, 101, 999)
