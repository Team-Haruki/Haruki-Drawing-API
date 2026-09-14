"""C1 model widening (plan §6.2, task T8): every forked field takes a string or a candidate list.

The field table below is the cross-repo contract for Cloud T15. Consumers that used a field as a string
or a dict key go through `legacy_key`, so a legacy string payload behaves exactly as before.
"""

from __future__ import annotations

import asyncio
import importlib
from pathlib import Path
from types import SimpleNamespace

from pydantic import TypeAdapter
import pytest

from scripts.parity_payloads import common as parity_common
from src.sekai.base.asset_key import legacy_key
from src.sekai.chart import drawer as chart_drawer
from src.sekai.deck import drawer as deck_drawer
from src.sekai.deck.model import DeckData, DeckPlannerInfo, DeckPlannerSong
from src.sekai.education import drawer as education_drawer
from src.sekai.gacha import drawer as gacha_drawer
from src.sekai.gacha.model import GachaBehavior
from src.sekai.honor.model import HonorRequest
from src.sekai.honor.widget import HonorBadgeBox
from src.sekai.inventory import drawer as inventory_drawer
from src.sekai.inventory.model import InventoryItem, InventorySection
from src.sekai.mysekai.model import MysekaiMsrMapHarvestPoint
from src.sekai.profile.custom_profile.renderer import ONDEMAND_PREFERRED_TOP_LEVEL
from src.sekai.stamp.model import StampData

WIDENED_FIELDS: dict[str, dict[str, tuple[str, ...]]] = {
    "src.sekai.honor.model": {
        "HonorRequest": (
            "honor_img_path",
            "rank_img_path",
            "frame_img_path",
            "frame_degree_level_img_path",
            "scroll_img_path",
            "chara_icon_path",
            "chara_icon_path2",
            "word_img_path",
        )
    },
    "src.sekai.inventory.model": {"InventoryItem": ("icon_path",)},
    "src.sekai.event.model": {"EventAssets": ("event_bg_path", "event_logo_path")},
    "src.sekai.gacha.model": {
        "GachaDetailRequest": ("logo_img_path", "banner_img_path"),
        "GachaInfo": ("ceil_item_img_path",),
        "GachaBehavior": ("cost_icon_path",),
    },
    "src.sekai.card.model": {"CardGachaInfo": ("gacha_banner_path",)},
    "src.sekai.profile.model": {"CardFullThumbnailRequest": ("card_thumbnail_path",)},
    "src.sekai.vlive.model": {"VLiveBrief": ("banner_path",)},
    "src.sekai.mysekai.model": {
        "MysekaiPhenomRequest": ("image_path",),
        "MysekaiResourceNumber": ("image_path",),
        "MysekaiSiteResourceNumber": ("image_path",),
        "MysekaiMsrMapSiteInfo": ("image_path",),
        "MysekaiMsrMapHarvestPoint": ("image_path",),
        "MysekaiFixtureMainGenre": ("image_path",),
        "MysekaiFixtureColorImage": ("image_path",),
        "MysekaiFixtureMaterial": ("image_path",),
    },
    "src.sekai.stamp.model": {"StampData": ("image_path",)},
    "src.sekai.chart.model": {"GenerateMusicChartRequest": ("jacket_path", "sus_path", "style_path")},
    "src.sekai.music.model": {"MusicDetailRequest": ("music_jacket_path",), "MusicBriefList": ("music_jacket_path",)},
    "src.sekai.deck.model": {
        "DeckData": ("music_cover_path",),
        "DeckPlannerSong": ("music_cover_path",),
        "DeckRequest": ("music_cover_path",),
    },
    "src.sekai.education.model": {
        "AreaItemInfo": ("item_icon_path", "target_icon_path"),
        "AreaItemMaterial": ("material_icon_path",),
    },
}

NOT_WIDENED: dict[str, dict[str, tuple[str, ...]]] = {
    "src.sekai.card.model": {"CardDetailRequest": ("card_images_path",)},
    "src.sekai.chart.model": {"GenerateMusicChartRequest": ("note_host",)},
    "src.sekai.mysekai.model": {"MysekaiMsrMapHarvestPoint": ("fallback_image_path",)},
}


def _fields(table):
    for module_name, classes in table.items():
        module = importlib.import_module(module_name)
        for class_name, fields in classes.items():
            for field in fields:
                yield pytest.param(getattr(module, class_name), field, id=f"{class_name}.{field}")


@pytest.mark.parametrize(("model", "field"), list(_fields(WIDENED_FIELDS)))
def test_widened_field_accepts_a_string_and_a_candidate_list(model, field) -> None:
    adapter = TypeAdapter(model.model_fields[field].annotation)

    assert adapter.validate_python("a") == "a"
    assert adapter.validate_python(["a", "b"]) == ["a", "b"]
    assert adapter.validate_json('["a", "b"]') == ["a", "b"]
    assert adapter.validate_json('"a"') == "a"


@pytest.mark.parametrize(("model", "field"), list(_fields(NOT_WIDENED)))
def test_image_lists_and_directories_are_not_widened(model, field) -> None:
    adapter = TypeAdapter(model.model_fields[field].annotation)

    with pytest.raises(ValueError, match="validation error"):
        adapter.validate_python([["a", "b"]] if "images" in field else ["a", "b"])


def test_costume_images_items_become_candidate_sets() -> None:
    from src.sekai.card.model import CardDetailRequest

    adapter = TypeAdapter(CardDetailRequest.model_fields["costume_images_path"].annotation)
    assert adapter.validate_python(["a", ["b", "c"]]) == ["a", ["b", "c"]]


def test_whole_models_validate_both_shapes() -> None:
    for value in ("a.png", ["a.png", "b.png"]):
        assert HonorRequest.model_validate({"honor_img_path": value}).honor_img_path == value
        assert StampData.model_validate({"id": 1, "image_path": value}).image_path == value
        point = MysekaiMsrMapHarvestPoint.model_validate({"image_path": value, "position_x": 1, "position_z": 2})
        assert point.image_path == value
        assert point.model_dump(mode="json")["image_path"] == value


def test_legacy_key_keeps_strings_verbatim_and_takes_the_first_candidate() -> None:
    assert legacy_key(None) is None
    assert legacy_key("") == ""
    assert legacy_key("  a.png ") == "  a.png "
    assert legacy_key([" a.png ", "b.png"]) == "a.png"
    assert legacy_key([]) is None


def test_ondemand_preferred_top_level_matches_the_parity_replica() -> None:
    # Cloud assets/helper.go:704-712 == scripts/parity_payloads/common.py (inventory H6, addendum C-8).
    assert ONDEMAND_PREFERRED_TOP_LEVEL == parity_common._ONDEMAND_FIRST
    assert "unit_story" in ONDEMAND_PREFERRED_TOP_LEVEL


def _item(item_id: int, icon_path) -> InventoryItem:
    return InventoryItem(
        id=item_id,
        name="n",
        description="",
        category="c",
        resource_type="material",
        icon_path=icon_path,
        quantity=1,
        seq=item_id,
    )


def test_inventory_icons_load_candidate_lists_under_their_first_candidate(monkeypatch) -> None:
    calls = []

    async def load(path):
        calls.append(path)
        return f"icon:{path}"

    monkeypatch.setattr(inventory_drawer, "_load_inventory_icon", load)
    section = InventorySection(
        key="a",
        title="A",
        items=[_item(1, ["one.png", "two.png"]), _item(2, " one.png "), _item(3, ["three.png"]), _item(4, [])],
    )

    icons = asyncio.run(inventory_drawer._load_inventory_icons([section]))

    assert calls == [["one.png", "two.png"], ["three.png"]]
    assert list(icons) == ["one.png", "three.png"]
    assert inventory_drawer._inventory_icon_key(section.items[1].icon_path) == "one.png"


def test_gacha_cost_icon_key_uses_the_first_candidate(monkeypatch, tmp_path: Path) -> None:
    seen = []

    async def load(path, *, allow_empty=False):
        seen.append(path)
        return "icon"

    monkeypatch.setattr(gacha_drawer, "get_gacha_image_ref_or_unknown", load)
    monkeypatch.setattr(gacha_drawer, "get_card_full_thumbnail_layers", load)
    monkeypatch.setattr(gacha_drawer, "get_rarity_img", lambda rarity: load(rarity))

    behavior = {"type": "normal", "spin_count": 1, "cost_type": "jewel", "cost_icon_path": ["cost.png", "alt.png"]}
    weight = SimpleNamespace(**{f"{rarity}_rate": 0.0 for rarity in gacha_drawer.GACHA_RATE_RARITIES})
    request = SimpleNamespace(
        logo_img_path=["logo.png", "logo2.png"],
        banner_img_path=None,
        gacha=SimpleNamespace(
            ceil_item_img_path=None,
            behaviors=[GachaBehavior.model_validate(behavior), GachaBehavior.model_validate(behavior)],
        ),
        pickup_cards=None,
        weight_info=weight,
    )

    keys, coroutines = gacha_drawer._gacha_detail_preload_items(request)

    async def drain():
        return await asyncio.gather(*coroutines)

    asyncio.run(drain())

    assert keys == ["logo", "cost_cost.png"]
    assert seen == [["logo.png", "logo2.png"], ["cost.png", "alt.png"]]


def test_education_area_icons_deduplicate_candidate_lists(monkeypatch) -> None:
    def material(path):
        return SimpleNamespace(material_icon_path=path)

    item = SimpleNamespace(
        item_icon_path=["item.png", "item2.png"],
        target_icon_path=None,
        levels=[
            SimpleNamespace(materials=[material(["m.png", "m2.png"]), material(["m.png", "m2.png"]), material("")])
        ],
    )

    paths = education_drawer._collect_area_item_icon_paths([item])
    assert paths == [["item.png", "item2.png"], ["m.png", "m2.png"], ""]

    async def load(keys, _perf_name):
        return [f"ref:{legacy_key(key)}" for key in keys]

    monkeypatch.setattr(education_drawer, "_load_asset_refs", load)
    cache = asyncio.run(education_drawer._load_asset_ref_cache(paths, "test"))
    assert cache == {"item.png": "ref:item.png", "m.png": "ref:m.png", "": "ref:"}


def test_deck_asset_maps_are_keyed_by_first_candidate(monkeypatch) -> None:
    from tests.test_deck_renderer import _card, _deck, _request

    calls = []

    async def load(_base_dir, path):
        calls.append(path)
        return f"img:{legacy_key(path)}"

    async def load_card(_card):
        return "layers"

    monkeypatch.setattr(deck_drawer, "get_asset_image_ref", load)
    monkeypatch.setattr(deck_drawer, "get_card_full_thumbnail_layers", load_card)
    card = _card()
    card.card_thumbnail.card_thumbnail_path = ["card.png", "card2.png"]
    planner = DeckPlannerInfo(
        target_point=1,
        remaining_point=1,
        songs=[DeckPlannerSong(title="P", music_cover_path=["planner.png", "p2.png"], rows=[])],
    )
    request = _request(
        music_compare=True,
        deck_data=[
            _deck(card_data=[card], music_cover_path=["compare.png", "c2.png"]),
            _deck(card_data=[], music_cover_path=["compare.png", "c2.png"]),
        ],
        event_planner=planner,
    )

    assets = asyncio.run(deck_drawer._load_deck_recommend_assets(request))

    assert ["planner.png", "p2.png"] in calls
    assert calls.count(["compare.png", "c2.png"]) == 1
    assert assets.card_layers == {(101, True, "card.png"): "layers"}
    assert assets.compare_music_imgs == {"compare.png": "img:compare.png"}
    assert assets.planner_music_imgs == {"planner.png": "img:planner.png"}
    assert deck_drawer.planner_cover_key(DeckPlannerSong(title="T", music_cover_path=["x.png"], rows=[])) == "x.png"
    assert isinstance(request.deck_data[0], DeckData)


def test_world_link_rank_style_reads_the_first_candidate() -> None:
    request = HonorRequest(
        honor_type="normal",
        group_type="wl_event",
        rank_img_path=["rank_live/honor/honor_top_wl_event_1/main.png", "other/main.png"],
    )
    box = HonorBadgeBox.__new__(HonorBadgeBox)
    box.rqd = request
    box.images = {"rank_img": object()}
    pasted = []
    box._paste_overlay = lambda _p, _img, pos: pasted.append(pos)

    box._draw_rank(None, object(), "wl_event")

    assert pasted == [(0, 0)]


def test_chart_asset_path_takes_the_first_local_candidate(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setattr(chart_drawer, "ASSETS_BASE_DIR", tmp_path)
    (tmp_path / "b.txt").write_text("b")

    assert chart_drawer.chart_asset_path("a.txt") == tmp_path / "a.txt"
    assert chart_drawer.chart_asset_path(["a.txt", "b.txt"]) == tmp_path / "b.txt"
    assert chart_drawer.chart_asset_path(["a.txt", "c.txt"]) == tmp_path / "a.txt"
    with pytest.raises(ValueError, match="empty"):
        chart_drawer.chart_asset_path([" "])
