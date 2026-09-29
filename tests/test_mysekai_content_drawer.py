"""JP 7.0.0 MySekai public views: shop, bulk harvest, blueprint term tabs, and the gate colour guard."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from src.sekai.base.font_metrics import get_layout_font
from src.sekai.base.plot import TextBox
from src.sekai.base.text_layout import get_text_size, ink_centered_text_offset_y
from src.sekai.mysekai import content_drawer as drawer
from src.sekai.mysekai.model import (
    GATE_FALLBACK_COLOR,
    UNIT_COLORS,
    MysekaiBlueprintTermEntry,
    MysekaiBlueprintTermRequest,
    MysekaiBulkHarvestRequest,
    MysekaiShopItem,
    MysekaiShopRequest,
    gate_color,
)

SHOP_PAYLOAD = {
    "title": "マイセカイショップ",
    "pass_active": True,
    "shops": [
        {
            "shop_type": "material",
            "title": "素材",
            "items": [
                {
                    "id": 1,
                    "name": "木材",
                    "image_path": "missing/material.png",
                    "quantity": 10,
                    "costs": [{"image_path": "missing/coin.png", "quantity": 100, "have_quantity": 5000}],
                    "exchange_limit_type": "limited_per_mysekai_colorful_pass",
                    "exchange_limit_value": 5,
                    "exchanged_count": 2,
                },
                {"id": 2, "image_path": ["missing/a.png", "missing/b.png"]},
            ],
        },
        {"shop_type": "tool", "items": []},
        {"shop_type": "future_type", "items": []},
        {
            "shop_type": "blueprint_daily",
            "items": [
                {
                    "id": 844,
                    "name": "バースデーケーキ【已持有】【本期已购买】",
                    "image_path": "missing/fixture.png",
                    "costs": [{"image_path": "missing/jewel.png", "quantity": 100}],
                    "exchange_limit_type": "daily",
                    "exchange_limit_value": 1,
                    "exchanged_count": 1,
                    "owned": True,
                    "is_bought": True,
                    "available": False,
                    "remaining_count": 0,
                },
                {
                    "id": 845,
                    "name": "ガーランド",
                    "image_path": "missing/fixture2.png",
                    "costs": [{"image_path": "missing/jewel.png", "quantity": 100}],
                    "exchange_limit_type": "daily",
                    "exchange_limit_value": 1,
                    "exchanged_count": 0,
                    "owned": False,
                    "is_bought": False,
                    "available": True,
                    "remaining_count": 1,
                },
            ],
        },
    ],
}

BULK_PAYLOAD = {
    "sites": [
        {
            "site_id": 5,
            "name": "さいしょの原っぱ",
            "image_path": "missing/site.png",
            "groups": [
                {
                    "id": 1,
                    "name": "木",
                    "required_tool_name": "オノ",
                    "required_tool_image_path": "missing/tool_axe.png",
                    "targets": [
                        {"id": 1, "name": "木", "image_path": "missing/tree.png", "checked": True, "fixture_count": 12},
                        {"id": 2, "name": "花", "checked": False},
                        {"id": 3, "name": "石"},
                    ],
                },
                {"id": 2, "name": "空", "targets": []},
            ],
        }
    ]
}

BLUEPRINT_PAYLOAD = {
    "tabs": [
        {
            "tab_type": "birthday_anniversary",
            "title": "誕生日・周年",
            "blueprints": [
                {
                    "id": 1234,
                    "name": "バースデーケーキ",
                    "image_path": "missing/fixture.png",
                    "start_at": 1790694000000,
                    "end_at": 1792767599000,
                    "craft_limit": 1,
                    "craft_count": 0,
                    "cost_materials": [{"image_path": "missing/mat.png", "quantity": 3, "have_quantity": 10}],
                }
            ],
        },
        {"tab_type": "limited_term", "blueprints": []},
    ]
}


def _walk(widget):
    yield widget
    for item in getattr(widget, "items", ()):
        yield from _walk(item)


def _texts(canvas) -> list[str]:
    return [item.text for item in _walk(canvas) if isinstance(item, TextBox)]


# ---------------------------------------------------------------------------
# gate colours
# ---------------------------------------------------------------------------


def test_unit_colors_tolerate_gates_beyond_the_table() -> None:
    assert UNIT_COLORS[0] == (68, 85, 221, 255)
    assert UNIT_COLORS[4] == (136, 68, 153, 255)
    # gate 6 (shuffle) and any future id: fallback instead of IndexError
    assert UNIT_COLORS[5] == GATE_FALLBACK_COLOR == (51, 204, 187, 255)
    assert UNIT_COLORS[99] == GATE_FALLBACK_COLOR
    # gate_id 0 -> index -1 keeps plain list semantics, as before
    assert UNIT_COLORS[-1] == (136, 68, 153, 255)
    assert UNIT_COLORS[:2] == [(68, 85, 221, 255), (136, 221, 68, 255)]
    assert len(UNIT_COLORS) == 5
    assert list(UNIT_COLORS)[-1] == (136, 68, 153, 255)


def test_gate_color_helper() -> None:
    assert gate_color(1) == (68, 85, 221, 255)
    assert gate_color(6) == GATE_FALLBACK_COLOR
    assert gate_color(0) == GATE_FALLBACK_COLOR


# ---------------------------------------------------------------------------
# models and text helpers
# ---------------------------------------------------------------------------


def test_contract_payloads_parse() -> None:
    shop = MysekaiShopRequest.model_validate(SHOP_PAYLOAD)
    assert shop.shops[0].items[0].costs[0].have_quantity == 5000
    assert shop.shops[0].items[1].quantity == 1
    assert shop.shops[0].items[1].exchange_limit_type == "none"
    assert shop.shops[0].items[1].has_state_fields is False
    assert shop.shops[3].items[0].owned is True
    assert shop.shops[3].items[0].material_capacity_count is None
    assert shop.shops[3].items[0].has_state_fields is True
    bulk = MysekaiBulkHarvestRequest.model_validate(BULK_PAYLOAD)
    assert bulk.sites[0].groups[0].targets[2].checked is None
    blueprint = MysekaiBlueprintTermRequest.model_validate(BLUEPRINT_PAYLOAD)
    assert blueprint.tabs[0].blueprints[0].craft_limit == 1
    assert MysekaiShopRequest.model_validate({}).shops == []


def _state(**fields):
    pass_active = fields.pop("pass_active", None)
    return drawer.shop_item_state(MysekaiShopItem(id=1, image_path="x.png", **fields), pass_active)


def test_shop_state_legacy_fields_only() -> None:
    """Without the state fields the pass only gates pass-limited items, as in the first contract."""

    pass_item = {"exchange_limit_type": "limited_per_mysekai_colorful_pass", "exchange_limit_value": 5}
    state = _state(**pass_item, exchanged_count=2, pass_active=True)
    assert state.available is True
    assert state.limit_text == "每期通行证限购 2/5"
    assert state.badges == (("剩余3次", drawer.CHIP_GREEN),)

    state = _state(**pass_item, exchanged_count=2, pass_active=False)
    assert state.available is False
    assert ("需通行证", drawer.CHIP_AMBER) in state.badges

    state = _state(**pass_item, exchanged_count=5, pass_active=None)
    assert state.available is False
    assert state.badges == (("已兑完", drawer.CHIP_RED),)

    # a plain item is not gated by the pass when no state fields are present
    state = _state(pass_active=False)
    assert state.available is True
    assert state.limit_text == "不限次数"
    assert state.badges == ()
    assert _state(exchanged_count=4).limit_text == "不限次数 · 已兑换 4"
    assert _state(exchange_limit_type="odd", exchange_limit_value=2, exchanged_count=1).limit_text == "限购 1/2 (odd)"
    assert _state(exchange_limit_type="odd").limit_text == "限制: odd"


def test_shop_state_structured_fields() -> None:
    """The caller's ``available`` wins; the chips come from the structured fields."""

    bought = _state(
        exchange_limit_type="daily",
        exchange_limit_value=1,
        exchanged_count=1,
        owned=True,
        is_bought=True,
        available=False,
        remaining_count=0,
        pass_active=True,
    )
    assert bought.available is False
    assert bought.limit_text == "每日限购 1/1"
    assert bought.badges == (("已持有", drawer.CHIP_BLUE), ("本期已购买", drawer.CHIP_GREY))

    weekly = _state(
        exchange_limit_type="weekly",
        exchange_limit_value=1,
        exchanged_count=0,
        owned=True,
        is_bought=False,
        available=True,
        remaining_count=1,
        pass_active=True,
    )
    assert weekly.available is True
    assert weekly.limit_text == "每周限购 0/1"
    assert weekly.badges == (("已持有", drawer.CHIP_BLUE),)

    # material: remaining count comes from the caller, warehouse full blocks the purchase
    full = _state(
        exchange_limit_type="limited_per_mysekai_colorful_pass",
        exchange_limit_value=3,
        exchanged_count=1,
        remaining_count=2,
        material_capacity_count=0,
        available=False,
        pass_active=True,
    )
    assert full.available is False
    assert full.badges == (("剩余2次", drawer.CHIP_GREEN), ("仓库已满", drawer.CHIP_AMBER))

    # with state fields present, an inactive pass gates every item
    gated = _state(
        remaining_count=99, exchange_limit_value=99, exchange_limit_type="odd", available=False, pass_active=False
    )
    assert gated.badges == (("剩余99次", drawer.CHIP_GREEN), ("需通行证", drawer.CHIP_AMBER))

    # an unexplained caller verdict still gets a chip
    assert _state(available=False, pass_active=True).badges == (("不可购买", drawer.CHIP_GREY),)


@pytest.mark.parametrize("text", ["已持有", "本期已购买", "剩余99次", "5 件", "x100"])
@pytest.mark.parametrize("size", [13, 15])
def test_chip_label_ink_is_vertically_centred(text: str, size: int) -> None:
    """The chip offsets its label by the measured ink bounds, not by line height: CJK and digits alike."""

    style = drawer.CHIP_STYLE.replace(size=size)
    chip = drawer._chip(text, drawer.CHIP_GREEN, style=style)
    assert chip.text_offset_y == ink_centered_text_offset_y(style.font, size, text, size)
    font = get_layout_font(style.font, size)
    _, top, _, bottom = font.getbbox(text)
    ink_center = get_text_size(font, "哇")[1] + (top + bottom) / 2 - font.getmetrics()[0]
    assert abs(ink_center + chip.text_offset_y - size / 2) <= 0.5
    # the bundled CJK font hangs below its nominal size, so the correction lifts the label
    assert chip.text_offset_y < 0


def test_shop_display_name_strips_status_tags_only_with_state_fields() -> None:
    decorated = MysekaiShopItem(id=1, name="木材【已持有】【剩余2次】", image_path="x.png")
    assert drawer.shop_item_display_name(decorated) == "木材【已持有】【剩余2次】"
    stateful = decorated.model_copy(update={"remaining_count": 2})
    assert drawer.shop_item_display_name(stateful) == "木材"
    assert drawer.shop_item_display_name(MysekaiShopItem(id=7, image_path="x.png", available=True)) == "ID 7"
    only_tags = MysekaiShopItem(id=8, name="【已持有】", image_path="x.png", owned=True)
    assert drawer.shop_item_display_name(only_tags) == "【已持有】"
    inner = MysekaiShopItem(id=9, name="【限定】ケーキ【已持有】", image_path="x.png", owned=True)
    assert drawer.shop_item_display_name(inner) == "【限定】ケーキ"


def test_blueprint_text_helpers() -> None:
    entry = MysekaiBlueprintTermEntry(
        id=1, name="n", image_path="x.png", start_at=1_000, end_at=2_000, craft_limit=2, craft_count=2
    )
    now = datetime(2030, 1, 1, tzinfo=UTC)
    assert drawer.blueprint_period_text(entry, "UTC", now)[0].endswith("(已结束)")
    assert drawer.blueprint_craft_text(entry)[0] == "制作次数 2/2 已达上限"
    assert drawer.blueprint_craft_text(entry.model_copy(update={"craft_limit": None, "craft_count": None})) is None
    assert (
        drawer.blueprint_period_text(entry.model_copy(update={"start_at": None, "end_at": None}), "UTC", now)[0] == ""
    )


# ---------------------------------------------------------------------------
# widget trees
# ---------------------------------------------------------------------------


def test_shop_canvas_texts() -> None:
    canvas = asyncio.run(drawer._build_mysekai_shop_canvas(MysekaiShopRequest.model_validate(SHOP_PAYLOAD)))
    texts = _texts(canvas)
    assert "マイセカイショップ" in texts
    assert "生效中" in texts
    assert "共 4 件商品 · 可购买 3 件" in texts
    for section in ("素材", "工具", "每日蓝图"):
        assert section in texts
    assert "future_type" in texts  # unknown shop types are shown verbatim
    assert texts.count("2 件") == 2
    assert "0 件" in texts
    assert "可购买 1 件" in texts  # only the blueprint section has an unavailable item
    assert "每日刷新" in texts
    assert "木材" in texts
    assert "ID 2" in texts
    assert "5,000/100" in texts
    assert "每期通行证限购 2/5" in texts
    assert "剩余3次" in texts
    assert "バースデーケーキ" in texts  # decorated suffixes stripped
    assert not any("【" in text for text in texts)
    assert "已持有" in texts
    assert "本期已购买" in texts
    assert "暂无可兑换的商品" in texts


def test_shop_canvas_empty_state() -> None:
    canvas = asyncio.run(drawer._build_mysekai_shop_canvas(MysekaiShopRequest(pass_active=False)))
    texts = _texts(canvas)
    assert "烤森商店" in texts
    assert "未生效" in texts
    assert "暂无可显示的商品" in texts
    assert not any(text.startswith("共 ") for text in texts)


def test_bulk_harvest_canvas_texts() -> None:
    canvas = asyncio.run(
        drawer._build_mysekai_bulk_harvest_canvas(MysekaiBulkHarvestRequest.model_validate(BULK_PAYLOAD))
    )
    texts = _texts(canvas)
    for expected in ("さいしょの原っぱ", "木", "オノ", "x12", "已勾选", "未勾选", "石", "没有可一键采集的对象"):
        assert expected in texts


def test_blueprint_term_canvas_texts() -> None:
    now = datetime(2026, 10, 1, tzinfo=UTC)
    canvas = asyncio.run(
        drawer._build_mysekai_blueprint_term_canvas(
            MysekaiBlueprintTermRequest.model_validate(BLUEPRINT_PAYLOAD | {"timezone": "Asia/Tokyo"}), now=now
        )
    )
    texts = _texts(canvas)
    assert "誕生日・周年 (1)" in texts
    assert "期间限定 (0)" in texts
    assert "バースデーケーキ" in texts
    assert "制作次数 0/1" in texts
    assert "10/3" in texts
    assert "2026-09-30 00:00 ~ 2026-10-23 23:59" in texts


# ---------------------------------------------------------------------------
# native route smoke
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    ("path", "payload"),
    [
        ("/api/pjsk/mysekai/shop", SHOP_PAYLOAD),
        ("/api/pjsk/mysekai/bulk-harvest", BULK_PAYLOAD),
        ("/api/pjsk/mysekai/blueprint-term", BLUEPRINT_PAYLOAD),
    ],
)
def test_new_mysekai_endpoints_render_png(path: str, payload: dict) -> None:
    pytest.importorskip("haruki_skia_renderer")
    from src.core.main import app

    async def call() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.post(path, json=payload)

    response = asyncio.run(call())
    assert response.status_code == 200, response.text
    assert response.content.startswith(b"\x89PNG")
