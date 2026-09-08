import asyncio
from datetime import UTC, datetime, timedelta

from PIL import Image
import pytest

import src.sekai.card.drawer as card_drawer
from src.sekai.card.model import (
    CardBasic,
    CardDetailRequest,
    CardEventInfo,
    CardGachaInfo,
    CardPower,
    CardSkill,
)


def _skill(*, name: str = "Skill", translated: bool = False) -> CardSkill:
    return CardSkill(
        skill_id=1,
        skill_name=name,
        skill_type="score_up",
        skill_detail="Score increases.",
        skill_type_icon_path=f"{name}.png",
        skill_detail_cn="分数提高。" if translated else None,
    )


def _card_detail_request(
    *,
    special_skill: bool = False,
    event: bool = False,
    gacha: bool = False,
    costumes: bool = False,
    background: str | None = None,
) -> CardDetailRequest:
    now = datetime(2027, 1, 1, tzinfo=UTC)
    return CardDetailRequest(
        timezone="UTC",
        region="jp",
        card_info=CardBasic(
            card_id=1001,
            character_id=1,
            character_name="Miku",
            release_at=int(now.timestamp() * 1000),
            supply_type="期间限定",
            prefix="Card title",
            skill=_skill(translated=special_skill),
            special_skill_info=_skill(name="Special", translated=True) if special_skill else None,
            thumbnail_info=[],
            power=CardPower(power_total=30000, power1=10000, power2=10000, power3=10000),
        ),
        event_info=CardEventInfo(
            event_id=10,
            event_name="Event",
            start_at=now,
            end_at=now + timedelta(days=1),
            event_banner_path="event-banner.png",
            bonus_attr="cool",
            unit="light_sound",
            banner_cid=1,
        )
        if event
        else None,
        gacha_info=CardGachaInfo(
            gacha_id=20,
            gacha_name="Gacha",
            start_at=now,
            end_at=now + timedelta(days=1),
            gacha_banner_path="gacha-banner.png",
        )
        if gacha
        else None,
        card_images_path=["card.png"],
        costume_images_path=["costume.png"] if costumes else [],
        character_icon_path="character.png",
        unit_logo_path="unit.png",
        background_image_path=background,
        event_attr_icon_path="event-attr.png" if event else None,
        event_unit_icon_path="event-unit.png" if event else None,
        event_chara_icon_path="event-chara.png" if event else None,
    )


@pytest.mark.parametrize("special_skill", [False, True], ids=["regular", "special"])
def test_load_card_detail_images_preserves_result_groups(monkeypatch, special_skill: bool) -> None:
    request = _card_detail_request(special_skill=special_skill, costumes=True)
    request.card_info.thumbnail_info = [object()]
    calls: list[str] = []

    async def fake_asset_ref(_base_dir, path, **_kwargs):
        calls.append(path)
        return path

    async def fake_thumbnail(_request):
        return "thumbnail-layers"

    monkeypatch.setattr(card_drawer, "get_asset_image_ref", fake_asset_ref)
    monkeypatch.setattr(card_drawer, "get_card_full_thumbnail_layers", fake_thumbnail)

    images = asyncio.run(card_drawer._load_card_detail_images(request))

    assert images.cards == ["card.png"]
    assert images.costumes == ["costume.png"]
    assert images.thumbnails == ["thumbnail-layers"]
    assert images.character_icon == "character.png"
    assert images.unit_logo == "unit.png"
    assert images.skill_type_icon == "Skill.png"
    assert images.special_skill_type_icon == ("Special.png" if special_skill else None)
    assert calls[-1] == ("Special.png" if special_skill else "Skill.png")


def test_load_card_detail_extra_images_covers_event_and_gacha(monkeypatch) -> None:
    request = _card_detail_request(event=True, gacha=True)

    async def fake_asset_ref(_base_dir, path, **_kwargs):
        return path

    monkeypatch.setattr(card_drawer, "get_asset_image_ref", fake_asset_ref)

    images = asyncio.run(card_drawer._load_card_detail_extra_images(request))

    assert images == {
        "event_banner": "event-banner.png",
        "event_attr": "event-attr.png",
        "event_unit": "event-unit.png",
        "event_chara": "event-chara.png",
        "gacha_banner": "gacha-banner.png",
    }
    assert asyncio.run(card_drawer._load_card_detail_extra_images(_card_detail_request())) == {}


@pytest.mark.parametrize("failure", [None, FileNotFoundError, OSError, ValueError])
def test_card_detail_background_uses_image_or_fallback(monkeypatch, failure) -> None:
    request = _card_detail_request(background="background.png")
    image = Image.new("RGB", (16, 16), "navy")

    async def fake_asset_ref(_base_dir, _path, **_kwargs):
        if failure:
            raise failure
        return image

    monkeypatch.setattr(card_drawer, "get_asset_image_ref", fake_asset_ref)

    background = asyncio.run(card_drawer._card_detail_background(request))

    if failure:
        assert background is card_drawer.SEKAI_BLUE_BG
    else:
        assert isinstance(background, card_drawer.ImageBg)


@pytest.mark.parametrize(
    ("special_skill", "event", "gacha", "costumes"),
    [(False, False, False, False), (True, True, True, True)],
    ids=["minimal", "all-optional-sections"],
)
def test_build_card_detail_canvas_covers_optional_sections(
    monkeypatch,
    special_skill: bool,
    event: bool,
    gacha: bool,
    costumes: bool,
) -> None:
    request = _card_detail_request(
        special_skill=special_skill,
        event=event,
        gacha=gacha,
        costumes=costumes,
    )
    image = Image.new("RGBA", (64, 64), (40, 80, 120, 255))
    images = card_drawer._CardDetailImages(
        cards=[image],
        costumes=[image] if costumes else [],
        thumbnails=[object()],
        character_icon=image,
        unit_logo=image,
        skill_type_icon=image if special_skill else None,
        special_skill_type_icon=image if special_skill else None,
    )
    extra_images = {
        "event_banner": image,
        "event_attr": image,
        "event_unit": image,
        "event_chara": image,
        "gacha_banner": image,
    }

    async def fake_images(_request):
        return images

    async def fake_extra_images(_request):
        return extra_images

    async def fake_background(_request):
        return card_drawer.SEKAI_BLUE_BG

    monkeypatch.setattr(card_drawer, "_load_card_detail_images", fake_images)
    monkeypatch.setattr(card_drawer, "_load_card_detail_extra_images", fake_extra_images)
    monkeypatch.setattr(card_drawer, "_card_detail_background", fake_background)
    monkeypatch.setattr(
        card_drawer,
        "CardFullThumbnailBox",
        lambda _layers, **_kwargs: card_drawer.Spacer(w=100, h=100),
    )

    canvas = asyncio.run(card_drawer._build_card_detail_canvas(request))

    assert canvas is not None


def test_card_detail_extra_tasks_skip_missing_optional_icon_paths() -> None:
    request = _card_detail_request(event=True)
    request.event_attr_icon_path = None
    request.event_unit_icon_path = None
    request.event_chara_icon_path = None

    tasks = card_drawer._card_detail_extra_tasks(request)

    assert set(tasks) == {"event_banner"}
    for task in tasks.values():
        task.close()
