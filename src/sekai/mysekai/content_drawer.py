"""Public MySekai views added for JP client 7.0.0: shop, bulk harvest and blueprint term tabs.

These are standalone endpoints drawn from the shared plot.py widget tree (like
``housing_drawer.py``). Every field is caller-supplied and optional where the contract says so;
nothing here assumes a region, a shop type, a tab type or a fixed number of entries — unknown
type strings are shown verbatim.
"""

from __future__ import annotations

import asyncio
from datetime import datetime
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

from src.core.image_payload import EncodedImagePayload
from src.sekai.base.asset_key import AssetKey, legacy_key
from src.sekai.base.draw import BG_PADDING, SEKAI_BLUE_BG, add_request_watermark, roundrect_bg
from src.sekai.base.plot import Canvas, Frame, Grid, HSplit, ImageBox, Spacer, TextBox, TextStyle, VSplit
from src.sekai.base.timezone import datetime_from_millis, request_now
from src.sekai.base.utils import ImageSource, get_asset_image_ref
from src.sekai.profile.drawer import get_profile_card
from src.sekai.skia_renderer.canvas import render_canvas_payload, skia_plot_enabled
from src.settings import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT, DEFAULT_FONT, DEFAULT_HEAVY_FONT

from .model import (
    MysekaiBlueprintTermEntry,
    MysekaiBlueprintTermRequest,
    MysekaiBlueprintTermTab,
    MysekaiBulkHarvestRequest,
    MysekaiBulkHarvestSite,
    MysekaiBulkHarvestTargetGroup,
    MysekaiShop,
    MysekaiShopItem,
    MysekaiShopRequest,
)

PANEL_WIDTH = 1120
PANEL_INNER_WIDTH = PANEL_WIDTH - 24

GRAY = (50, 50, 50, 255)
DIM = (120, 120, 130, 255)
RED = (200, 0, 0, 255)
GREEN = (0, 150, 60, 255)

TITLE_STYLE = TextStyle(font=DEFAULT_HEAVY_FONT, size=30, color=(35, 35, 35, 255))
SECTION_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(40, 44, 64, 255))
NAME_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=(45, 48, 64, 255))
INFO_STYLE = TextStyle(font=DEFAULT_FONT, size=16, color=GRAY)
SMALL_STYLE = TextStyle(font=DEFAULT_FONT, size=14, color=GRAY)
QTY_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=GRAY)

SHOP_TYPE_LABELS = {"material": "素材商店", "tool": "道具商店"}
SHOP_LIMIT_PASS = "limited_per_mysekai_colorful_pass"
BLUEPRINT_TAB_LABELS = {"limited_term": "期间限定", "birthday_anniversary": "生日・周年纪念"}

ImageMap = dict[str, ImageSource]


# ---------------------------------------------------------------------------
# shared helpers
# ---------------------------------------------------------------------------


def _image_key(path: AssetKey | None) -> str | None:
    key = legacy_key(path)
    return key.strip() if key and key.strip() else None


async def _load_images(paths: list[AssetKey | None]) -> ImageMap:
    """Resolve every distinct asset key once, concurrently; missing assets become placeholders."""

    requests: dict[str, AssetKey] = {}
    for path in paths:
        key = _image_key(path)
        if key is not None and key not in requests:
            requests[key] = path
    if not requests:
        return {}
    keys = list(requests)
    refs = await asyncio.gather(*[get_asset_image_ref(ASSETS_BASE_DIR, requests[key]) for key in keys])
    return dict(zip(keys, refs, strict=True))


def _image(images: ImageMap, path: AssetKey | None) -> ImageSource | None:
    key = _image_key(path)
    return images.get(key) if key is not None else None


def _icon(images: ImageMap, path: AssetKey | None, size: int | tuple[int, int]) -> None:
    """A fixed-size icon slot; an absent path keeps the slot so rows stay aligned."""

    w, h = (size, size) if isinstance(size, int) else size
    image = _image(images, path)
    with Frame().set_size((w, h)).set_content_align("c"):
        if image is not None:
            ImageBox(image, size=(w, h), image_size_mode="fit", use_alpha_blend=True).set_content_align("c")
        else:
            Spacer(w=w, h=h)


def _panel() -> VSplit:
    return (
        VSplit()
        .set_w(PANEL_WIDTH)
        .set_content_align("lt")
        .set_item_align("lt")
        .set_sep(10)
        .set_padding(12)
        .set_bg(roundrect_bg(alpha=80))
    )


def _tile(width: int) -> VSplit:
    return (
        VSplit()
        .set_w(width)
        .set_content_align("lt")
        .set_item_align("lt")
        .set_sep(6)
        .set_padding(8)
        .set_bg(roundrect_bg(fill=(255, 255, 255, 110), radius=8))
    )


def _format_quantity(value: int) -> str:
    return f"{value:,}"


def _have_text(have: int | None, need: int) -> tuple[str, tuple[int, int, int, int]]:
    if have is None:
        return f"x{_format_quantity(need)}", GRAY
    return f"{_format_quantity(have)}/{_format_quantity(need)}", GREEN if have >= need else RED


def _cost_row(images: ImageMap, costs, *, icon_size: int = 28) -> None:
    """Icons with quantities (or have/need) for shop costs and blueprint materials."""

    with HSplit().set_content_align("l").set_item_align("c").set_sep(10):
        for cost in costs:
            with HSplit().set_content_align("l").set_item_align("c").set_sep(4):
                _icon(images, cost.image_path, icon_size)
                text, color = _have_text(cost.have_quantity, cost.quantity)
                TextBox(text, TextStyle(font=QTY_STYLE.font, size=QTY_STYLE.size, color=color))


def _empty_hint(text: str) -> None:
    TextBox(text, INFO_STYLE).set_padding((4, 2))


def _format_time(value: int | None, timezone: str) -> str:
    dt = datetime_from_millis(value, timezone) if value is not None else None
    return dt.strftime("%Y-%m-%d %H:%M") if dt is not None else "-"


# ---------------------------------------------------------------------------
# shop
# ---------------------------------------------------------------------------

SHOP_COL_COUNT = 3
SHOP_TILE_WIDTH = (PANEL_INNER_WIDTH - (SHOP_COL_COUNT - 1) * 10) // SHOP_COL_COUNT


def shop_item_limit_text(item: MysekaiShopItem, pass_active: bool | None) -> tuple[str, tuple[int, ...]]:
    """Exchange-limit line and its colour; ``pass_active is False`` marks pass-only items unavailable."""

    limit_type = str(item.exchange_limit_type or "none")
    count = item.exchanged_count
    limit = item.exchange_limit_value
    if limit_type == "none" or limit is None:
        text = "不限次数" if limit_type == "none" else f"限制: {limit_type}"
        return (text if count is None else f"{text} (已兑换 {count})"), DIM
    progress = f"{count if count is not None else '-'}/{limit}"
    if limit_type == SHOP_LIMIT_PASS:
        if pass_active is False:
            return f"需缤纷通行证 {progress}", DIM
        text = f"每期通行证限兑 {progress}"
    else:
        text = f"限兑 {progress} ({limit_type})"
    if count is not None and count >= limit:
        return f"{text} 已兑完", RED
    return text, GREEN


def _shop_item_available(item: MysekaiShopItem, pass_active: bool | None) -> bool:
    if item.exchange_limit_type == SHOP_LIMIT_PASS and pass_active is False:
        return False
    limit = item.exchange_limit_value
    return not (limit is not None and item.exchanged_count is not None and item.exchanged_count >= limit)


def _draw_shop_item(item: MysekaiShopItem, images: ImageMap, pass_active: bool | None) -> None:
    available = _shop_item_available(item, pass_active)
    tile = _tile(SHOP_TILE_WIDTH)
    if not available:
        tile.set_bg(roundrect_bg(fill=(200, 200, 208, 150), radius=8))
    with tile:
        with HSplit().set_content_align("l").set_item_align("c").set_sep(8):
            _icon(images, item.image_path, 56)
            with VSplit().set_content_align("lt").set_item_align("lt").set_sep(4):
                name = item.name or f"ID {item.id}"
                TextBox(name, NAME_STYLE, line_count=2, use_real_line_count=True, overflow="shrink").set_w(
                    SHOP_TILE_WIDTH - 88
                )
                TextBox(f"x{_format_quantity(item.quantity)}", QTY_STYLE)
        if item.costs:
            _cost_row(images, item.costs)
        text, color = shop_item_limit_text(item, pass_active)
        TextBox(text, TextStyle(font=SMALL_STYLE.font, size=SMALL_STYLE.size, color=color), overflow="shrink").set_w(
            SHOP_TILE_WIDTH - 16
        )


def _draw_shop(shop: MysekaiShop, images: ImageMap, pass_active: bool | None) -> None:
    with _panel():
        title = shop.title or SHOP_TYPE_LABELS.get(shop.shop_type, shop.shop_type)
        TextBox(f"{title} ({len(shop.items)})", SECTION_STYLE)
        if not shop.items:
            _empty_hint("暂无可兑换的商品")
            return
        with Grid(col_count=SHOP_COL_COUNT).set_sep(10, 10).set_item_align("lt"):
            for item in shop.items:
                _draw_shop_item(item, images, pass_active)


def _shop_image_paths(rqd: MysekaiShopRequest) -> list[AssetKey | None]:
    paths: list[AssetKey | None] = []
    for shop in rqd.shops:
        for item in shop.items:
            paths.append(item.image_path)
            paths.extend(cost.image_path for cost in item.costs)
    return paths


async def _build_mysekai_shop_canvas(rqd: MysekaiShopRequest) -> Canvas:
    images = await _load_images(_shop_image_paths(rqd))
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16):
            if rqd.profile is not None:
                await get_profile_card(rqd.profile)
            with _panel():
                TextBox(rqd.title or "烤森商店", TITLE_STYLE)
                if rqd.pass_active is not None:
                    status = "缤纷通行证: 生效中" if rqd.pass_active else "缤纷通行证: 未生效"
                    TextBox(
                        status,
                        TextStyle(font=INFO_STYLE.font, size=INFO_STYLE.size, color=GREEN if rqd.pass_active else DIM),
                    )
            if not rqd.shops:
                with _panel():
                    _empty_hint("暂无商店数据")
            for shop in rqd.shops:
                _draw_shop(shop, images, rqd.pass_active)
    add_request_watermark(canvas, rqd)
    return canvas


async def compose_mysekai_shop_image(rqd: MysekaiShopRequest) -> Image.Image:
    return await (await _build_mysekai_shop_canvas(rqd)).get_img()


async def try_render_mysekai_shop_payload(rqd: MysekaiShopRequest) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_mysekai_shop_canvas(rqd), endpoint="mysekai_shop")


# ---------------------------------------------------------------------------
# bulk harvest
# ---------------------------------------------------------------------------

BULK_TARGET_COL_COUNT = 4
BULK_TARGET_WIDTH = (PANEL_INNER_WIDTH - 24 - (BULK_TARGET_COL_COUNT - 1) * 8) // BULK_TARGET_COL_COUNT


def bulk_target_state_text(checked: bool | None) -> tuple[str, tuple[int, ...]] | None:
    if checked is None:
        return None
    return ("已勾选", GREEN) if checked else ("未勾选", DIM)


def _draw_bulk_group(group: MysekaiBulkHarvestTargetGroup, images: ImageMap) -> None:
    with (
        VSplit()
        .set_w(PANEL_INNER_WIDTH)
        .set_content_align("lt")
        .set_item_align("lt")
        .set_sep(8)
        .set_padding(8)
        .set_bg(roundrect_bg(fill=(255, 255, 255, 90), radius=8))
    ):
        with HSplit().set_content_align("l").set_item_align("c").set_sep(8):
            TextBox(group.name, NAME_STYLE)
            if group.required_tool_name or group.required_tool_image_path:
                TextBox("所需道具:", INFO_STYLE)
                if group.required_tool_image_path:
                    _icon(images, group.required_tool_image_path, 32)
                if group.required_tool_name:
                    TextBox(group.required_tool_name, INFO_STYLE)
        if not group.targets:
            _empty_hint("没有可一键采集的对象")
            return
        with Grid(col_count=BULK_TARGET_COL_COUNT).set_sep(8, 8).set_item_align("lt"):
            for target in group.targets:
                with (
                    HSplit()
                    .set_w(BULK_TARGET_WIDTH)
                    .set_content_align("l")
                    .set_item_align("c")
                    .set_sep(6)
                    .set_padding(6)
                    .set_bg(roundrect_bg(fill=(255, 255, 255, 110), radius=6))
                ):
                    if target.image_path:
                        _icon(images, target.image_path, 40)
                    with VSplit().set_content_align("lt").set_item_align("lt").set_sep(2):
                        TextBox(target.name, INFO_STYLE, overflow="shrink").set_w(BULK_TARGET_WIDTH - 64)
                        details = []
                        if target.fixture_count is not None:
                            details.append((f"x{target.fixture_count}", GRAY))
                        if state := bulk_target_state_text(target.checked):
                            details.append(state)
                        if details:
                            with HSplit().set_content_align("l").set_item_align("c").set_sep(6):
                                for text, color in details:
                                    TextBox(text, TextStyle(font=SMALL_STYLE.font, size=SMALL_STYLE.size, color=color))


def _draw_bulk_site(site: MysekaiBulkHarvestSite, images: ImageMap) -> None:
    with _panel():
        with HSplit().set_content_align("l").set_item_align("c").set_sep(10):
            if site.image_path:
                _icon(images, site.image_path, (96, 56))
            TextBox(site.name, SECTION_STYLE)
        if not site.groups:
            _empty_hint("该地点没有一键采集对象")
        for group in site.groups:
            _draw_bulk_group(group, images)


def _bulk_image_paths(rqd: MysekaiBulkHarvestRequest) -> list[AssetKey | None]:
    paths: list[AssetKey | None] = []
    for site in rqd.sites:
        paths.append(site.image_path)
        for group in site.groups:
            paths.append(group.required_tool_image_path)
            paths.extend(target.image_path for target in group.targets)
    return paths


async def _build_mysekai_bulk_harvest_canvas(rqd: MysekaiBulkHarvestRequest) -> Canvas:
    images = await _load_images(_bulk_image_paths(rqd))
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16):
            if rqd.profile is not None:
                await get_profile_card(rqd.profile)
            with _panel():
                TextBox("一键采集对象", TITLE_STYLE)
            if not rqd.sites:
                with _panel():
                    _empty_hint("暂无一键采集数据")
            for site in rqd.sites:
                _draw_bulk_site(site, images)
    add_request_watermark(canvas, rqd)
    return canvas


async def compose_mysekai_bulk_harvest_image(rqd: MysekaiBulkHarvestRequest) -> Image.Image:
    return await (await _build_mysekai_bulk_harvest_canvas(rqd)).get_img()


async def try_render_mysekai_bulk_harvest_payload(rqd: MysekaiBulkHarvestRequest) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_mysekai_bulk_harvest_canvas(rqd), endpoint="mysekai_bulk_harvest")


# ---------------------------------------------------------------------------
# blueprint term tabs
# ---------------------------------------------------------------------------

BLUEPRINT_COL_COUNT = 2
BLUEPRINT_TILE_WIDTH = (PANEL_INNER_WIDTH - (BLUEPRINT_COL_COUNT - 1) * 10) // BLUEPRINT_COL_COUNT


def blueprint_period_text(entry: MysekaiBlueprintTermEntry, timezone: str, now: datetime) -> tuple[str, tuple]:
    if entry.start_at is None and entry.end_at is None:
        return "", GRAY
    text = f"{_format_time(entry.start_at, timezone)} ~ {_format_time(entry.end_at, timezone)}"
    end = datetime_from_millis(entry.end_at, timezone) if entry.end_at is not None else None
    start = datetime_from_millis(entry.start_at, timezone) if entry.start_at is not None else None
    if end is not None and end < now:
        return f"{text} (已结束)", DIM
    if start is not None and start > now:
        return f"{text} (未开始)", DIM
    return text, GRAY


def blueprint_craft_text(entry: MysekaiBlueprintTermEntry) -> tuple[str, tuple] | None:
    if entry.craft_limit is None and entry.craft_count is None:
        return None
    count = entry.craft_count
    if entry.craft_limit is None or entry.craft_limit <= 0:
        return f"已制作 {count if count is not None else '-'} 次", GRAY
    progress = f"{count if count is not None else '-'}/{entry.craft_limit}"
    if count is not None and count >= entry.craft_limit:
        return f"制作次数 {progress} 已达上限", RED
    return f"制作次数 {progress}", GREEN


def _draw_blueprint(entry: MysekaiBlueprintTermEntry, images: ImageMap, timezone: str, now: datetime) -> None:
    with _tile(BLUEPRINT_TILE_WIDTH):
        with HSplit().set_content_align("l").set_item_align("c").set_sep(10):
            _icon(images, entry.image_path, 80)
            with VSplit().set_content_align("lt").set_item_align("lt").set_sep(4):
                width = BLUEPRINT_TILE_WIDTH - 116
                TextBox(entry.name, NAME_STYLE, line_count=2, use_real_line_count=True, overflow="shrink").set_w(width)
                period, color = blueprint_period_text(entry, timezone, now)
                if period:
                    TextBox(period, TextStyle(font=SMALL_STYLE.font, size=SMALL_STYLE.size, color=color)).set_w(width)
                if craft := blueprint_craft_text(entry):
                    text, craft_color = craft
                    TextBox(text, TextStyle(font=SMALL_STYLE.font, size=SMALL_STYLE.size, color=craft_color))
        if entry.cost_materials:
            _cost_row(images, entry.cost_materials, icon_size=32)


def _draw_blueprint_tab(tab: MysekaiBlueprintTermTab, images: ImageMap, timezone: str, now: datetime) -> None:
    with _panel():
        title = tab.title or BLUEPRINT_TAB_LABELS.get(tab.tab_type, tab.tab_type)
        TextBox(f"{title} ({len(tab.blueprints)})", SECTION_STYLE)
        if not tab.blueprints:
            _empty_hint("暂无蓝图")
            return
        with Grid(col_count=BLUEPRINT_COL_COUNT).set_sep(10, 10).set_item_align("lt"):
            for entry in tab.blueprints:
                _draw_blueprint(entry, images, timezone, now)


def _blueprint_image_paths(rqd: MysekaiBlueprintTermRequest) -> list[AssetKey | None]:
    paths: list[AssetKey | None] = []
    for tab in rqd.tabs:
        for entry in tab.blueprints:
            paths.append(entry.image_path)
            paths.extend(material.image_path for material in entry.cost_materials)
    return paths


async def _build_mysekai_blueprint_term_canvas(rqd: MysekaiBlueprintTermRequest, now: datetime | None = None) -> Canvas:
    images = await _load_images(_blueprint_image_paths(rqd))
    if now is None:
        now = request_now(rqd.timezone)
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16):
            if rqd.profile is not None:
                await get_profile_card(rqd.profile)
            with _panel():
                TextBox("期间限定蓝图", TITLE_STYLE)
            if not rqd.tabs:
                with _panel():
                    _empty_hint("暂无期间限定蓝图")
            for tab in rqd.tabs:
                _draw_blueprint_tab(tab, images, rqd.timezone, now)
    add_request_watermark(canvas, rqd)
    return canvas


async def compose_mysekai_blueprint_term_image(rqd: MysekaiBlueprintTermRequest) -> Image.Image:
    return await (await _build_mysekai_blueprint_term_canvas(rqd)).get_img()


async def try_render_mysekai_blueprint_term_payload(rqd: MysekaiBlueprintTermRequest) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    now = request_now(rqd.timezone)
    return await render_canvas_payload(
        await _build_mysekai_blueprint_term_canvas(rqd, now=now), endpoint="mysekai_blueprint_term"
    )
