from __future__ import annotations

from PIL import Image
import pytest

from src.sekai.card import drawer as card
from src.sekai.card.model import (
    CardBasic,
    CardBoxDistribution,
    CardBoxRequest,
    CardDistributionAttributeStat,
    CardDistributionCharacterStat,
    UserCard,
)
from src.sekai.profile.model import CardFullThumbnailRequest, DetailedProfileCardRequest


def _thumbnail(card_id: int, suffix: str = "normal") -> CardFullThumbnailRequest:
    return CardFullThumbnailRequest(
        card_id=card_id,
        card_thumbnail_path=f"card-{suffix}.png",
        rare="rarity_4",
        frame_img_path="frame.png",
        attr_img_path="attr.png",
        rare_img_path="star.png",
        train_rank=None,
    )


def _user_card(
    card_id: int,
    *,
    character_id: int = 1,
    attribute: str = "cool",
    rarity: str = "rarity_4",
    owned: bool = True,
    thumbnails: int = 1,
    after_training: bool = False,
    supply_type: str = "normal",
) -> UserCard:
    return UserCard(
        card=CardBasic(
            card_id=card_id,
            character_id=character_id,
            release_at=card_id * 1000,
            rare=rarity,
            attr=attribute,
            supply_type=supply_type,
            thumbnail_info=[_thumbnail(card_id, str(index)) for index in range(thumbnails)],
            is_after_training=after_training,
        ),
        has_card=owned,
    )


def _request(*cards: UserCard, **updates) -> CardBoxRequest:
    values = {
        "cards": list(cards),
        "region": "jp",
        "character_icon_paths": {item.card.character_id: f"chara-{item.card.character_id}.png" for item in cards},
    }
    values.update(updates)
    return CardBoxRequest(**values)


def _record(user_card: UserCard, layers: object | None = None) -> dict:
    return {**user_card.model_dump(), "thumb_layers": layers or object(), "has": user_card.has_card}


def _walk(widget):
    yield widget
    for child in getattr(widget, "items", None) or ():
        yield from _walk(child)


@pytest.mark.anyio
async def test_load_card_box_thumb_selects_available_training_state(monkeypatch) -> None:
    calls: list[str] = []

    async def fake_layers(thumbnail):
        calls.append(thumbnail.card_thumbnail_path)
        return thumbnail.card_thumbnail_path

    monkeypatch.setattr(card, "get_card_full_thumbnail_layers", fake_layers)
    empty = _user_card(1, thumbnails=0)
    regular = _user_card(2)
    before = _user_card(3, thumbnails=2)
    after = _user_card(4, thumbnails=2, after_training=True)

    assert await card._load_card_box_thumb(empty) is None
    assert await card._load_card_box_thumb(regular) == "card-0.png"
    assert await card._load_card_box_thumb(before) == "card-0.png"
    assert await card._load_card_box_thumb(after) == "card-1.png"
    assert calls == ["card-0.png", "card-0.png", "card-1.png"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("show_box", "unowned_only", "visible_ids"),
    [(False, False, [1, 2]), (True, False, [1]), (False, True, [2])],
)
async def test_load_card_box_records_applies_visibility_filters(
    monkeypatch,
    show_box: bool,
    unowned_only: bool,
    visible_ids: list[int],
) -> None:
    owned = _user_card(1, owned=True)
    missing = _user_card(2, owned=False)

    async def fake_thumb(user_card):
        return f"layers-{user_card.card.card_id}"

    monkeypatch.setattr(card, "_load_card_box_thumb", fake_thumb)
    request = _request(owned, missing, show_box=show_box, unowned_only=unowned_only)

    records, _elapsed = await card._load_card_box_records(request)

    assert [item["card"]["card_id"] for item in records] == visible_ids


def test_card_box_layout_covers_normal_attribute_and_empty_modes() -> None:
    first = _user_card(1, character_id=2, attribute="cute")
    second = _user_card(2, character_id=1, attribute="cool")
    records = [_record(first), _record(second)]

    normal = card._card_box_layout(_request(first, second), records)
    grouped = card._card_box_layout(_request(first, second, group_by="attr"), records)
    empty = card._card_box_layout(_request(), [])

    assert [character_id for character_id, _ in normal.character_groups] == [1, 2]
    assert normal.group_by_attribute is False
    assert grouped.group_by_attribute is True
    assert set(grouped.attribute_groups) == {"cool", "cute"}
    assert empty.best_height == 10000
    assert empty.card_size == 100
    assert empty.card_sep == 8


@pytest.mark.anyio
async def test_load_card_box_assets_filters_failures_and_keeps_categories(monkeypatch) -> None:
    user_card = _user_card(1)
    distribution = CardBoxDistribution(
        total_count=1,
        owned_count=1,
        owned_data=True,
        attribute_stats=[
            CardDistributionAttributeStat(
                attr="cool",
                count=1,
                owned_count=1,
                attr_icon_path="cool.png",
            )
        ],
    )
    request = _request(
        user_card,
        distribution=distribution,
        term_limited_icon_path="term.png",
        fes_limited_icon_path="fes.png",
    )
    layout = card._card_box_layout(request, [_record(user_card)])

    async def fake_asset(_base_dir, path, **_kwargs):
        if path in {"term.png", card.CARD_BOX_RARITY_STAR_PATH}:
            raise OSError("missing")
        return path

    async def fake_image(_base_dir, path, **_kwargs):
        return Image.new("RGBA", (8, 8), "white") if "chara" in path else path

    monkeypatch.setattr(card, "get_asset_image_ref", fake_asset)
    monkeypatch.setattr(card, "get_img_from_path", fake_image)

    assets, _elapsed = await card._load_card_box_assets(request, layout)

    assert assets.term is None
    assert assets.fes == "fes.png"
    assert 1 in assets.character_icons
    assert assets.attribute_icons == {"cool": "cool.png"}
    assert assets.rarity_star is None
    assert assets.birthday_rarity == card.CARD_BOX_BIRTHDAY_RARITY_PATH


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [None, FileNotFoundError, OSError, ValueError])
async def test_card_box_background_uses_image_or_default(monkeypatch, failure) -> None:
    request = _request(background_img_path="background.png")
    image = Image.new("RGB", (16, 16), "navy")

    async def fake_asset(_base_dir, _path, **_kwargs):
        if failure:
            raise failure
        return image

    monkeypatch.setattr(card, "get_asset_image_ref", fake_asset)

    background = await card._card_box_background(request)

    if failure:
        assert background is card.SEKAI_BLUE_BG
    else:
        assert isinstance(background, card.ImageBg)


@pytest.mark.anyio
async def test_card_box_profile_expands_panel_width(monkeypatch) -> None:
    user_info = DetailedProfileCardRequest(
        id="1",
        region="jp",
        nickname="tester",
        source="test",
        update_time=0,
        leader_image_path="leader.png",
    )
    request = _request(user_info=user_info)
    layout = card._card_box_layout(request, [])

    async def fake_profile(_request):
        return card.Spacer(w=900, h=100)

    monkeypatch.setattr(card, "get_profile_card", fake_profile)

    profile, panel_width, text_width = await card._card_box_profile(request, layout)

    assert profile is not None
    assert panel_width == 900
    assert text_width == 780


@pytest.mark.parametrize("group_by_attribute", [False, True], ids=["character", "attribute"])
def test_card_box_renderer_draws_primary_modes(monkeypatch, group_by_attribute: bool) -> None:
    first = _user_card(1, character_id=1, attribute="cool", owned=False, supply_type="期间限定")
    second = _user_card(2, character_id=2, attribute="cute", owned=True, supply_type="Fes限定")
    distribution = CardBoxDistribution(
        total_count=2,
        owned_count=1,
        owned_data=True,
        character_stats=[
            CardDistributionCharacterStat(character_id=1, count=1, owned_count=0),
            CardDistributionCharacterStat(character_id=2, count=1, owned_count=1),
        ],
        attribute_stats=[
            CardDistributionAttributeStat(attr="cool", label="帅气", count=1, owned_count=0),
            CardDistributionAttributeStat(attr="cute", label="可爱", count=1, owned_count=1),
        ],
    )
    request = _request(
        first,
        second,
        title="Notice",
        show_id=True,
        group_by="attr" if group_by_attribute else None,
        distribution=distribution,
        user_info=DetailedProfileCardRequest(
            id="1",
            region="jp",
            nickname="tester",
            source="test",
            update_time=0,
            leader_image_path="leader.png",
        ),
    )
    records = [_record(first), _record(second)]
    layout = card._card_box_layout(request, records)
    image = Image.new("RGBA", (16, 16), "white")
    assets = card._CardBoxAssets(
        term=image,
        fes=image,
        character_icons={1: image},
        attribute_icons={"cool": image},
        rarity_star=image,
        birthday_rarity=image,
    )
    monkeypatch.setattr(card, "CardFullThumbnailBox", lambda _layers, **_kwargs: card.Spacer(w=80, h=80))

    canvas = card._CardBoxRenderer(
        request, layout, assets, None, layout.panel_width, layout.panel_text_width
    ).draw_canvas(card.SEKAI_BLUE_BG)

    texts = [widget.text for widget in _walk(canvas) if isinstance(widget, card.TextBox)]
    assert "提示" in texts
    assert "1" in texts
    assert "2" in texts


def test_card_box_renderer_draws_single_character_rarity_progress(monkeypatch) -> None:
    rarities = ["rarity_1", "rarity_2", "rarity_3", "rarity_4", "rarity_birthday"]
    user_cards = [
        _user_card(index, rarity=rarity, owned=index % 2 == 1) for index, rarity in enumerate(rarities, start=1)
    ]
    request = _request(*user_cards, distribution=CardBoxDistribution(total_count=5, owned_count=3, owned_data=True))
    records = [_record(user_card) for user_card in user_cards]
    layout = card._card_box_layout(request, records)
    image = Image.new("RGBA", (16, 16), "white")
    assets = card._CardBoxAssets(
        term=None,
        fes=None,
        character_icons={},
        attribute_icons={},
        rarity_star=image,
        birthday_rarity=image,
    )
    monkeypatch.setattr(card, "CardFullThumbnailBox", lambda _layers, **_kwargs: card.Spacer(w=80, h=80))

    canvas = card._CardBoxRenderer(
        request, layout, assets, None, layout.panel_width, layout.panel_text_width
    ).draw_canvas(card.SEKAI_BLUE_BG)

    texts = [widget.text for widget in _walk(canvas) if isinstance(widget, card.TextBox)]
    assert "收集进度" in texts
    assert "全卡 3/5" in texts
