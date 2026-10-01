"""All-character challenge decks: every deck is labelled with its own character."""

from __future__ import annotations

import asyncio

from PIL import Image

from src.sekai.deck import drawer
from src.sekai.deck.model import DeckCardData, DeckData, DeckRequest
from src.sekai.profile.model import CardFullThumbnailRequest, DetailedProfileCardRequest


def _card() -> DeckCardData:
    return DeckCardData(
        card_thumbnail=CardFullThumbnailRequest(
            card_id=101,
            card_thumbnail_path="card.png",
            rare="rarity_4",
            frame_img_path="frame.png",
            attr_img_path="attr.png",
            rare_img_path="rare.png",
            train_rank=2,
            is_after_training=True,
        ),
        chara_id=1,
        skill_level="4",
        skill_rate=125.5,
        event_bonus_rate=0.0,
    )


def _deck(score: int, chara_id: int | None = None) -> DeckData:
    extra = {}
    if chara_id is not None:
        extra = {
            "challenge_character_id": chara_id,
            "chara_icon_path": f"chara_icon/chr_icon_{chara_id}.png",
            "chara_name": f"角色{chara_id}",
        }
    return DeckData(card_data=[_card()] * 5, score=score, total_power=300_000, challenge_score_delta=10, **extra)


def _request(decks: list[DeckData], recommend_type: str = "challenge_all", **overrides) -> DeckRequest:
    return DeckRequest(
        region="jp",
        profile=DetailedProfileCardRequest(
            id="1", region="jp", nickname="P", source="suite", update_time=1, leader_image_path="leader.png"
        ),
        deck_data=decks,
        recommend_type=recommend_type,
        live_type="solo",
        **overrides,
    )


def test_rows_reserve_room_for_the_character_only_in_all_character_results():
    labelled = drawer._deck_layout(_request([_deck(3, 21), _deck(2, 1)]))
    plain = drawer._deck_layout(_request([_deck(3), _deck(2)], recommend_type="challenge", chara_name="初音ミク"))
    assert labelled.rank_w == drawer._RANK_CHARA_W
    assert plain.rank_w == drawer._RANK_W
    assert labelled.list_widths()["rank"] == drawer._RANK_CHARA_W


def test_subtitle_names_the_mode_when_no_character_was_chosen():
    assert drawer._deck_subtitle(_request([_deck(3, 21)])).startswith("全部角色")
    assert drawer._deck_subtitle(_request([_deck(3)], recommend_type="challenge", chara_name="初音ミク")) == "初音ミク"


def test_assets_load_each_deck_character_once(monkeypatch):
    calls = []

    async def load(_base_dir, path):
        calls.append(path)
        return Image.new("RGBA", (8, 8))

    async def load_card(_card):
        return "layers"

    monkeypatch.setattr(drawer, "get_asset_image_ref", load)
    monkeypatch.setattr(drawer, "get_card_full_thumbnail_layers", load_card)
    request = _request([_deck(3, 21), _deck(2, 1), _deck(1, 21)])
    assets = asyncio.run(drawer._load_deck_recommend_assets(request))
    assert calls.count("chara_icon/chr_icon_21.png") == 1
    assert set(assets.deck_chara_icons) == {"chara_icon/chr_icon_21.png", "chara_icon/chr_icon_1.png"}


def test_hero_band_names_the_best_decks_character(monkeypatch):
    texts = []
    original_text = drawer.TextBox

    def text_box(text, *args, **kwargs):
        texts.append(text)
        return original_text(text, *args, **kwargs)

    monkeypatch.setattr(drawer, "TextBox", text_box)
    request = _request([_deck(3, 21), _deck(2, 1)])
    layout = drawer._deck_layout(request)
    assets = drawer._DeckRecommendAssets(
        chara_icon=None,
        wl_chara_icon=None,
        unit_logo=None,
        attr_icon=None,
        music_cover=None,
        canvas_thumbnail=None,
        card_layers={},
        compare_music_imgs={},
        planner_music_imgs={},
        deck_chara_icons={"chara_icon/chr_icon_21.png": Image.new("RGBA", (8, 8))},
    )
    monkeypatch.setattr(drawer, "_draw_deck_cards", lambda *a, **k: None)
    with drawer.Canvas():
        drawer._draw_hero_tile(request, assets, layout, request.deck_data[0], "dfs")
    assert "最佳卡组 · 角色21" in texts
