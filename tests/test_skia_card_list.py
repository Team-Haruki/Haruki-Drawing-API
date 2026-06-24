from PIL import Image
import pytest

from src.core.pjsk import card as card_router
from src.sekai.card.model import CardBasic, CardListRequest, CardSkill
from src.sekai.profile.model import CardFullThumbnailRequest
from src.sekai.skia_renderer import card_render as card_list  # merged module (card/list + card/box)


def _thumbnail(card_id: int, path: str = "cards/card.png") -> CardFullThumbnailRequest:
    return CardFullThumbnailRequest(
        card_id=card_id,
        card_thumbnail_path=path,
        rare="rarity_4",
        frame_img_path="frames/frame.png",
        attr_img_path="icons/attr.png",
        rare_img_path="icons/star.png",
        train_rank=None,
    )


def _card(card_id: int, release_at: int, path: str = "cards/card.png") -> CardBasic:
    return CardBasic(
        card_id=card_id,
        character_id=1,
        release_at=release_at,
        supply_type="Fes限定",
        prefix=f"Card {card_id}",
        skill=CardSkill(
            skill_id=1,
            skill_name="Skill",
            skill_type="score",
            skill_detail="Detail",
            skill_type_icon_path="icons/skill.png",
        ),
        thumbnail_info=[_thumbnail(card_id, path)],
    )


def test_card_list_ir_sorts_cards_and_includes_required_fields():
    request = CardListRequest(
        cards=[_card(1, 1000), _card(2, 3000), _card(3, 2000)],
        region="jp",
        title="notice",
        background_img_path="backgrounds/bg.png",
        term_limited_icon_path="icons/term.png",
        fes_limited_icon_path="icons/fes.png",
    )

    ir = card_list.build_card_list_ir(request)

    assert ir["version"] == 1
    assert ir["title"] == "notice"
    assert 0.0 <= ir["background_hour"] < 24.0
    assert ir["background_img_path"] == "backgrounds/bg.png"
    assert ir["icons"]["skill"] == ["icons/skill.png"]
    assert [card["card_id"] for card in ir["cards"]] == [2, 3, 1]
    assert ir["cards"][0]["thumbnail_info"][0]["card_thumbnail_path"] == "cards/card.png"
    assert ir["fonts"]["default"]
    assert ir["fonts"]["bold"]


@pytest.mark.parametrize("path", ["/tmp/evil.png", "../evil.png", "cards\\evil.png"])
def test_card_list_ir_rejects_unsafe_asset_paths(path):
    request = CardListRequest(cards=[_card(1, 1000, path)], region="jp")

    with pytest.raises(ValueError, match=r"relative|forward slash|contain"):
        card_list.build_card_list_ir(request)


@pytest.mark.anyio
async def test_skia_card_list_disabled_does_not_import_native(monkeypatch):
    monkeypatch.setattr(card_list.settings.drawing, "use_skia_card_list", False)
    monkeypatch.setattr(
        card_list,
        "_load_native_renderer",
        lambda: (_ for _ in ()).throw(AssertionError("native renderer should not load")),
    )

    payload = await card_list.try_render_card_list_payload(CardListRequest(cards=[], region="jp"))

    assert payload is None


@pytest.mark.anyio
async def test_skia_card_list_falls_back_to_pillow_on_native_error(monkeypatch):
    monkeypatch.setattr(card_list.settings.drawing, "use_skia_card_list", True)
    monkeypatch.setattr(card_list.settings.drawing, "skia_card_list_fallback_to_pillow", True)
    monkeypatch.setattr(
        card_list,
        "_load_native_renderer",
        lambda: (_ for _ in ()).throw(card_list.SkiaCardRenderError("missing native")),
    )

    payload = await card_list.try_render_card_list_payload(CardListRequest(cards=[], region="jp"))

    assert payload is None


@pytest.mark.anyio
async def test_card_list_endpoint_uses_pillow_when_skia_returns_none(monkeypatch):
    async def fake_try_render(_request):
        return None

    async def fake_compose(_request):
        return Image.new("RGBA", (2, 2), (255, 0, 0, 255))

    monkeypatch.setattr(card_router, "try_render_card_list_payload", fake_try_render)
    monkeypatch.setattr(card_router, "compose_card_list_image", fake_compose)

    response = await card_router.card_list(CardListRequest(cards=[], region="jp"))

    assert response.media_type == "image/png"


@pytest.mark.anyio
async def test_skia_card_list_caches_payload(monkeypatch):
    from src.sekai.skia_renderer import card_common

    card_common._skia_payload_cache.clear()
    calls = {"n": 0}

    class FakeNative:
        def render_scene(self, _ir_json):
            calls["n"] += 1
            return {
                "image_bytes": b"\x89PNG-fake",
                "media_type": "image/png",
                "filename": "image.png",
                "image_width": 1,
                "image_height": 1,
                "image_mode": "RGBA",
                "encode_elapsed": 0.0,
            }

    monkeypatch.setattr(card_list, "_load_native_renderer", lambda: FakeNative())
    request = CardListRequest(cards=[], region="jp")

    first = await card_list.render_card_list_payload(request)
    second = await card_list.render_card_list_payload(request)

    assert calls["n"] == 1  # second request served from the payload cache
    assert first is second
