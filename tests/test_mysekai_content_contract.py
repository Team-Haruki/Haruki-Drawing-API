"""JP 7.0.0 MySekai shop / bulk harvest / blueprint term: public request contract and placeholder drawer.

The drawer behind these routes is proprietary (``content_drawer.real.py``, bind-mounted over the public
``content_drawer.py`` stub in production), so its rendering tests live with it. What stays public is the
request models, the gate colour guard and the stub's failure mode.
"""

from __future__ import annotations

import asyncio

from fastapi import HTTPException
import pytest

from src.core.pjsk import mysekai
from src.sekai.mysekai import content_drawer
from src.sekai.mysekai.model import (
    GATE_FALLBACK_COLOR,
    UNIT_COLORS,
    MysekaiBlueprintTermRequest,
    MysekaiBulkHarvestRequest,
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
                    "name": "バースデーケーキ",
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

_ROUTES = (
    ("mysekai_shop", MysekaiShopRequest, SHOP_PAYLOAD),
    ("mysekai_bulk_harvest", MysekaiBulkHarvestRequest, BULK_PAYLOAD),
    ("mysekai_blueprint_term", MysekaiBlueprintTermRequest, BLUEPRINT_PAYLOAD),
)

_PUBLIC_ENTRYPOINTS = (
    ("compose_mysekai_shop_image", MysekaiShopRequest),
    ("try_render_mysekai_shop_payload", MysekaiShopRequest),
    ("compose_mysekai_bulk_harvest_image", MysekaiBulkHarvestRequest),
    ("try_render_mysekai_bulk_harvest_payload", MysekaiBulkHarvestRequest),
    ("compose_mysekai_blueprint_term_image", MysekaiBlueprintTermRequest),
    ("try_render_mysekai_blueprint_term_payload", MysekaiBlueprintTermRequest),
)


@pytest.fixture
def placeholder_drawer():
    """Skip when the real implementation has been swapped in locally (its own tests cover it then)."""
    if not hasattr(content_drawer, "_NOT_IMPL_MSG"):
        pytest.skip("the real content_drawer.py is mounted")
    return content_drawer


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
# request contract
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


# ---------------------------------------------------------------------------
# public placeholder
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(("name", "model"), _PUBLIC_ENTRYPOINTS, ids=lambda value: getattr(value, "__name__", value))
def test_placeholder_entrypoints_raise_not_implemented(placeholder_drawer, name, model) -> None:
    with pytest.raises(NotImplementedError, match=r"content_drawer\.py is a placeholder"):
        asyncio.run(getattr(placeholder_drawer, name)(model()))


@pytest.mark.parametrize(("endpoint", "model", "payload"), _ROUTES, ids=lambda value: getattr(value, "__name__", value))
def test_unmounted_routes_fail_with_a_named_placeholder_error(placeholder_drawer, endpoint, model, payload) -> None:
    """No silent None: the 500 names the missing implementation instead of a generic native failure."""
    with pytest.raises(HTTPException) as error:
        asyncio.run(getattr(mysekai, endpoint)(model.model_validate(payload)))
    assert error.value.status_code == 500
    assert "content_drawer.py is a placeholder" in error.value.detail
