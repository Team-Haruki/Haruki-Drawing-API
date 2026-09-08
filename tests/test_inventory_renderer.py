from __future__ import annotations

import asyncio
from types import SimpleNamespace

from PIL import Image
import pytest

from src.sekai.base.draw import Canvas
from src.sekai.base.plot import VSplit
from src.sekai.inventory import drawer
from src.sekai.inventory.model import InventoryItem, InventorySection


def _item(**overrides) -> InventoryItem:
    values = {
        "id": 42,
        "name": "测试道具",
        "description": "",
        "category": "material",
        "resource_type": "material",
        "icon_path": "icons/item.png",
        "quantity": 1234,
        "seq": 1,
    }
    values.update(overrides)
    return InventoryItem(**values)


@pytest.mark.parametrize(
    ("overrides", "expected"),
    [
        ({"description": "  第一行\n 第二行  "}, "第一行 第二行"),
        ({"recovery_value": 10}, "+10 能量"),
        ({"resource_type": "coin"}, "金币"),
        ({"resource_type": "jewel"}, "水晶"),
        ({"resource_type": "virtual_coin"}, "虚拟币"),
        ({"resource_type": "boost_item"}, "火罐"),
        ({"resource_type": "event_item"}, "活动"),
        ({"resource_type": "gacha_ticket"}, "招募"),
        ({"resource_type": "gacha_ceil_item"}, "招募"),
        ({"resource_type": "practice_ticket"}, "育成"),
        ({"resource_type": "skill_practice_ticket"}, "育成"),
        ({"resource_type": "mysekai_material"}, "MySekai"),
        ({"resource_type": "unknown", "id": 99}, "ID 99"),
    ],
)
def test_item_description_text(overrides, expected):
    assert drawer._item_description_text(_item(**overrides)) == expected


def test_load_inventory_icons_deduplicates_and_skips_missing(monkeypatch):
    sections = [
        InventorySection(key="a", title="A", items=[_item(icon_path="one"), _item(id=2, icon_path="one")]),
        InventorySection(key="b", title="B", items=[_item(id=3, icon_path=""), _item(id=4, icon_path="two")]),
    ]
    calls = []
    first_icon = object()

    async def load(path):
        calls.append(path)
        return first_icon if path == "one" else None

    monkeypatch.setattr(drawer, "_load_inventory_icon", load)

    assert asyncio.run(drawer._load_inventory_icons(sections)) == {"one": first_icon}
    assert calls == ["one", "two"]
    assert asyncio.run(drawer._load_inventory_icons([])) == {}


def test_load_inventory_icon_returns_asset(monkeypatch):
    icon = object()

    async def load(base_dir, path, *, on_missing):
        assert base_dir == drawer.ASSETS_BASE_DIR
        assert path == "icon"
        assert on_missing == "raise"
        return icon

    monkeypatch.setattr(drawer, "get_asset_image_ref", load)
    assert asyncio.run(drawer._load_inventory_icon("icon")) is icon


@pytest.mark.parametrize("error", [FileNotFoundError(), OSError(), ValueError()])
def test_load_inventory_icon_tolerates_invalid_assets(monkeypatch, error):
    async def load(*_args, **_kwargs):
        raise error

    monkeypatch.setattr(drawer, "get_asset_image_ref", load)
    assert asyncio.run(drawer._load_inventory_icon("missing")) is None


def test_build_inventory_canvas_coordinates_sections(monkeypatch):
    sections = [SimpleNamespace(title="A"), SimpleNamespace(title="B")]
    profile_request = object()
    request = SimpleNamespace(
        sections=sections,
        profile=SimpleNamespace(to_profile_card_request=lambda: profile_request),
    )
    icon_cache = {"icon": object()}
    events = []

    async def load_icons(received):
        assert received is sections
        return icon_cache

    async def profile(received):
        assert received is profile_request
        events.append("profile")

    monkeypatch.setattr(drawer, "_load_inventory_icons", load_icons)
    monkeypatch.setattr(drawer, "get_profile_card", profile)
    monkeypatch.setattr(drawer, "_draw_header", lambda: events.append("header"))
    monkeypatch.setattr(drawer, "_draw_section", lambda section, icons: events.append((section.title, icons)))
    monkeypatch.setattr(drawer, "add_request_watermark", lambda canvas, received: events.append((canvas, received)))

    canvas = asyncio.run(drawer._build_inventory_canvas(request))

    assert isinstance(canvas, Canvas)
    assert events[:4] == ["profile", "header", ("A", icon_cache), ("B", icon_cache)]
    assert events[4] == (canvas, request)


def test_compose_inventory_list_image_uses_shared_canvas(monkeypatch):
    expected = Image.new("RGBA", (3, 2), "red")

    class FakeCanvas:
        async def get_img(self):
            return expected

    async def build(request):
        assert request == "request"
        return FakeCanvas()

    monkeypatch.setattr(drawer, "_build_inventory_canvas", build)
    assert asyncio.run(drawer.compose_inventory_list_image("request")) is expected


def test_try_render_inventory_list_payload_respects_gate(monkeypatch):
    build_calls = []

    async def build(request):
        build_calls.append(request)
        return "canvas"

    monkeypatch.setattr(drawer, "_build_inventory_canvas", build)
    monkeypatch.setattr(drawer, "skia_plot_enabled", lambda: False)
    assert asyncio.run(drawer.try_render_inventory_list_payload("disabled")) is None
    assert build_calls == []

    payload = object()

    async def render(canvas, *, endpoint):
        assert canvas == "canvas"
        assert endpoint == "inventory_list"
        return payload

    monkeypatch.setattr(drawer, "skia_plot_enabled", lambda: True)
    monkeypatch.setattr(drawer, "render_canvas_payload", render)
    assert asyncio.run(drawer.try_render_inventory_list_payload("enabled")) is payload
    assert build_calls == ["enabled"]


def test_draw_inventory_section_with_fallback_icon():
    section = InventorySection(key="materials", title="素材", items=[_item()])
    with Canvas() as canvas, VSplit():
        drawer._draw_header()
        drawer._draw_section(section, {})

    image = asyncio.run(canvas.get_img())
    assert image.width > 0
    assert image.height > 0


def test_draw_item_tile_with_loaded_icon():
    icon = Image.new("RGBA", (16, 16), "blue")
    with Canvas() as canvas, VSplit():
        drawer._draw_item_tile(_item(icon_path="icon"), {"icon": icon})

    image = asyncio.run(canvas.get_img())
    assert image.size == (drawer.TILE_WIDTH, drawer.TILE_HEIGHT)


def test_quantity_and_name_styles_shrink_until_text_fits(monkeypatch):
    monkeypatch.setattr(drawer, "get_font", lambda path, size: (path, size))
    monkeypatch.setattr(drawer, "get_text_size", lambda font, text: (font[1] * len(text), 10))

    assert drawer._format_quantity(1234567) == "1,234,567"
    assert drawer._quantity_style("x").size == drawer.QTY_STYLE.size
    assert drawer._quantity_style("x" * 100).size == 10
    assert drawer._name_style("short").size == drawer.NAME_STYLE.size
    assert drawer._name_style("x" * 100).size == 11


def test_line_fitting_and_clipping(monkeypatch):
    monkeypatch.setattr(drawer, "get_font", lambda *_args: object())
    monkeypatch.setattr(drawer, "get_text_size", lambda _font, text: (len(text) * 10, 10))

    assert drawer._fits_lines("abcd", "font", 12, 20, 2)
    assert not drawer._fits_lines("abcde", "font", 12, 20, 2)
    assert drawer._fits_lines("", "font", 12, 20, 2)
    assert drawer._clip_text_to_width("abcd", object(), 20) == 2
    assert drawer._clip_text_to_width("abcd", object(), 25) == 2
