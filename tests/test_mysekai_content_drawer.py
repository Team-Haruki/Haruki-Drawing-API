"""JP 7.0.0 MySekai public views: shop, bulk harvest, blueprint term tabs, and the gate colour guard."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import httpx
import pytest

from src.sekai.base.plot import TextBox
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
    bulk = MysekaiBulkHarvestRequest.model_validate(BULK_PAYLOAD)
    assert bulk.sites[0].groups[0].targets[2].checked is None
    blueprint = MysekaiBlueprintTermRequest.model_validate(BLUEPRINT_PAYLOAD)
    assert blueprint.tabs[0].blueprints[0].craft_limit == 1
    assert MysekaiShopRequest.model_validate({}).shops == []


def test_shop_limit_text_covers_pass_and_sold_out() -> None:
    item = MysekaiShopItem(
        id=1,
        image_path="x.png",
        exchange_limit_type="limited_per_mysekai_colorful_pass",
        exchange_limit_value=5,
        exchanged_count=2,
    )
    assert drawer.shop_item_limit_text(item, True)[0] == "每期通行证限兑 2/5"
    assert drawer.shop_item_limit_text(item, False)[0] == "需缤纷通行证 2/5"
    sold_out = item.model_copy(update={"exchanged_count": 5})
    text, color = drawer.shop_item_limit_text(sold_out, None)
    assert text.endswith("已兑完")
    assert color == drawer.RED
    assert drawer.shop_item_limit_text(MysekaiShopItem(id=2, image_path="x.png"), None)[0] == "不限次数"


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
    assert "缤纷通行证: 生效中" in texts
    assert "素材 (2)" in texts
    assert "道具商店 (0)" in texts
    assert "future_type (0)" in texts  # unknown shop types are shown verbatim
    assert "木材" in texts
    assert "ID 2" in texts
    assert "5,000/100" in texts
    assert "每期通行证限兑 2/5" in texts


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
