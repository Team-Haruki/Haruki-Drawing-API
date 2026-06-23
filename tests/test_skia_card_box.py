import pytest

from src.core.heavy_render_pool import EncodedImagePayload
from src.core.pjsk import card as card_router
from src.sekai.card.model import CardBasic, CardBoxRequest, UserCard
from src.sekai.profile.model import CardFullThumbnailRequest
from src.sekai.skia_renderer import card_box


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


def _user_card(
    card_id: int,
    character_id: int,
    release_at: int,
    *,
    has_card: bool = True,
    path: str = "cards/card.png",
) -> UserCard:
    return UserCard(
        card=CardBasic(
            card_id=card_id,
            character_id=character_id,
            release_at=release_at,
            supply_type="Fes限定",
            rare="rarity_4",
            thumbnail_info=[_thumbnail(card_id, path)],
        ),
        has_card=has_card,
    )


def _request(path: str = "cards/card.png") -> CardBoxRequest:
    return CardBoxRequest(
        cards=[
            _user_card(2, 1, 3000, path=path),
            _user_card(1, 1, 1000, path=path),
            _user_card(3, 2, 2000, has_card=False, path=path),
        ],
        region="jp",
        title="box notice",
        show_id=True,
        show_box=True,
        background_img_path="backgrounds/bg.png",
        character_icon_paths={1: "icons/chara1.png", 2: "icons/chara2.png"},
        character_color_codes={1: "#112233", 2: "#445566"},
        term_limited_icon_path="icons/term.png",
        fes_limited_icon_path="icons/fes.png",
    )


def test_card_box_ir_includes_required_fields_and_filters_in_rust():
    ir = card_box.build_card_box_ir(_request())

    assert ir["version"] == 1
    assert ir["title"] == "box notice"
    assert ir["show_id"] is True
    assert ir["show_box"] is True
    assert 0.0 <= ir["background_hour"] < 24.0
    assert ir["background_img_path"] == "backgrounds/bg.png"
    assert ir["icons"]["fes_limited"] == "icons/fes.png"
    assert ir["character_icon_paths"] == {"1": "icons/chara1.png", "2": "icons/chara2.png"}
    assert ir["character_color_codes"] == {"1": "#112233", "2": "#445566"}
    assert [card["card_id"] for card in ir["cards"]] == [2, 1, 3]
    assert ir["cards"][0]["thumbnail_info"][0]["card_thumbnail_path"] == "cards/card.png"
    assert ir["fonts"]["default"]
    assert ir["fonts"]["bold"]


def test_card_box_ir_rejects_unsafe_asset_paths():
    request = _request("../evil.png")

    with pytest.raises(ValueError, match=r"contain|relative"):
        card_box.build_card_box_ir(request)


@pytest.mark.anyio
async def test_skia_card_box_disabled_does_not_import_native(monkeypatch):
    monkeypatch.setattr(card_box.settings.drawing, "use_skia_card_box", False)
    monkeypatch.setattr(
        card_box,
        "_load_native_renderer",
        lambda: (_ for _ in ()).throw(AssertionError("native renderer should not load")),
    )

    payload = await card_box.try_render_card_box_payload(_request())

    assert payload is None


@pytest.mark.anyio
async def test_skia_card_box_falls_back_to_pillow_on_native_error(monkeypatch):
    monkeypatch.setattr(card_box.settings.drawing, "use_skia_card_box", True)
    monkeypatch.setattr(card_box.settings.drawing, "skia_card_fallback_to_pillow", True)
    monkeypatch.setattr(
        card_box,
        "_load_native_renderer",
        lambda: (_ for _ in ()).throw(card_box.SkiaCardBoxRenderError("missing native")),
    )

    payload = await card_box.try_render_card_box_payload(_request())

    assert payload is None


@pytest.mark.anyio
async def test_card_box_endpoint_uses_skia_payload(monkeypatch):
    async def fake_try_render(_request):
        return EncodedImagePayload(
            image_bytes=b"\x89PNG\r\n\x1a\n",
            media_type="image/png",
            filename="image.png",
            image_width=2,
            image_height=2,
            image_mode="RGBA",
            encode_elapsed=0.001,
        )

    async def fake_compose(_request):
        raise AssertionError("pillow composer should not be called")

    monkeypatch.setattr(card_router, "try_render_card_box_payload", fake_try_render)
    monkeypatch.setattr(card_router, "compose_box_image", fake_compose)

    response = await card_router.card_box(_request())

    assert response.media_type == "image/png"
