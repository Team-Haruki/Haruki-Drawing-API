"""card/list draws the shared plot.py widget tree on both backends.

The hand-written IR scene builder it used to have (skia_renderer/card_render.py) is retired, so
what is worth pinning is no longer the shape of the IR it emitted, but that the tree both backends
draw is built once, that the ordering it depends on is unchanged, and that the fail-open gate
still holds now that card/list rides use_skia_plot like every other endpoint.
"""

from __future__ import annotations

from PIL import Image
import pytest

from src.sekai.card import drawer as card
from src.sekai.card.model import CardBasic, CardListRequest, CardSkill
from src.sekai.profile.model import CardFullThumbnailRequest


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


def _request(*cards: CardBasic) -> CardListRequest:
    return CardListRequest(cards=list(cards), region="jp")


def _walk(widget):
    yield widget
    for child in getattr(widget, "items", None) or ():
        yield from _walk(child)


@pytest.mark.anyio
async def test_card_list_orders_cards_newest_first():
    """The grid is ordered by (release_at, card_id) descending. That ordering used to live in the
    dedicated IR builder AND in the Pillow composer; now there is one tree, so one ordering."""
    canvas = await card._build_card_list_canvas(_request(_card(1, 1000), _card(2, 3000), _card(3, 2000)))

    ids = [w.layers.rqd.card_id for w in _walk(canvas) if isinstance(w, card.CardFullThumbnailBox)]
    assert ids == [2, 3, 1]


@pytest.mark.anyio
async def test_load_card_list_thumbs_covers_empty_single_and_pair(monkeypatch):
    no_thumbnails = _card(1, 1000)
    no_thumbnails.thumbnail_info = []
    single = _card(2, 2000)
    pair = _card(3, 3000)
    pair.thumbnail_info.append(_thumbnail(3, "cards/after.png"))

    async def fake_layers(thumbnail):
        return None if thumbnail.card_thumbnail_path.endswith("after.png") else thumbnail.card_thumbnail_path

    monkeypatch.setattr(card, "get_card_full_thumbnail_layers", fake_layers)

    assert await card._load_card_list_thumbs(no_thumbnails) == []
    assert await card._load_card_list_thumbs(single) == ["cards/card.png"]
    assert await card._load_card_list_thumbs(pair) == ["cards/card.png"]


@pytest.mark.anyio
async def test_load_card_list_assets_filters_failed_optional_images(monkeypatch):
    request = _request(_card(1, 1000))
    request.term_limited_icon_path = "term.png"
    request.fes_limited_icon_path = "fes.png"

    async def fake_asset_ref(_base_dir, path, **_kwargs):
        if path == "term.png":
            raise OSError("missing")
        return path

    monkeypatch.setattr(card, "get_asset_image_ref", fake_asset_ref)

    assets, skill_paths, _elapsed = await card._load_card_list_assets(request, [(request.cards[0], [object()])])

    assert assets.term is None
    assert assets.fes == "fes.png"
    assert assets.skills == {"icons/skill.png": "icons/skill.png"}
    assert skill_paths == ["icons/skill.png"]


@pytest.mark.anyio
@pytest.mark.parametrize("failure", [None, FileNotFoundError, OSError, ValueError])
async def test_card_list_background_uses_image_or_fallback(monkeypatch, failure):
    request = _request()
    request.background_img_path = "background.png"
    image = Image.new("RGB", (16, 16), "navy")

    async def fake_asset_ref(_base_dir, _path, **_kwargs):
        if failure:
            raise failure
        return image

    monkeypatch.setattr(card, "get_asset_image_ref", fake_asset_ref)

    background = await card._card_list_background(request)

    if failure:
        assert background is card.SEKAI_BLUE_BG
    else:
        assert isinstance(background, card.ImageBg)


@pytest.mark.anyio
async def test_card_list_draws_notice_and_supply_variants(monkeypatch):
    normal = _card(1, 1000)
    normal.supply_type = "normal"
    normal.skill = None
    term = _card(2, 2000)
    term.supply_type = "期间限定"
    future = _card(3, 4_000_000_000_000)
    request = _request(normal, term, future)
    request.title = "Notice"
    request.term_limited_icon_path = "term.png"
    request.fes_limited_icon_path = "fes.png"

    canvas = await card._build_card_list_canvas(request)

    texts = [widget.text for widget in _walk(canvas) if isinstance(widget, card.TextBox)]
    assert "提示" in texts
    assert "未上线" in texts
    assert "ID:1" in texts
    assert "ID:2【期间限定】" in texts


@pytest.mark.anyio
async def test_skia_disabled_returns_none_without_rendering(monkeypatch):
    monkeypatch.setattr(card, "skia_plot_enabled", lambda: False)

    def _boom(*args, **kwargs):
        raise AssertionError("must not render when the gate is off")

    monkeypatch.setattr(card, "render_canvas_payload", _boom)

    assert await card.try_render_card_list_payload(_request()) is None


@pytest.mark.anyio
async def test_skia_decline_falls_back_to_pillow(monkeypatch):
    """FAIL-OPEN: card/list no longer carries its own fallback flag. It rides the shared contract,
    where a Skia problem yields None and the route composes with Pillow instead."""
    monkeypatch.setattr(card, "skia_plot_enabled", lambda: True)

    async def _decline(*args, **kwargs):
        return None

    monkeypatch.setattr(card, "render_canvas_payload", _decline)

    assert await card.try_render_card_list_payload(_request(_card(1, 1000))) is None


@pytest.mark.anyio
@pytest.mark.parametrize("path", ["../../../etc/passwd", "cards/../../../../etc/hosts"])
async def test_asset_paths_cannot_escape_the_asset_root(path):
    """The retired builder validated asset paths itself. On the shared path the loader is what
    refuses to escape the asset root, so the protection has to still be there -- and it is even
    stricter: a traversal raises rather than degrading to the placeholder."""
    from src.sekai.base.utils import get_asset_image_ref
    from src.settings import ASSETS_BASE_DIR

    with pytest.raises(ValueError, match="越界"):
        await get_asset_image_ref(ASSETS_BASE_DIR, path, on_missing="placeholder")
