"""Public MySekai views added for JP client 7.0.0: shop, bulk harvest and blueprint term tabs.

These are standalone endpoints drawn from the shared plot.py widget tree (like
``housing_drawer.py``). Every field is caller-supplied and optional where the contract says so;
nothing here assumes a region, a shop type, a tab type or a fixed number of entries — unknown
type strings are shown verbatim.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from datetime import datetime
import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

from src.core.image_payload import EncodedImagePayload
from src.sekai.base.asset_key import AssetKey, legacy_key
from src.sekai.base.draw import BG_PADDING, SEKAI_BLUE_BG, add_request_watermark, roundrect_bg
from src.sekai.base.paint_types import Color
from src.sekai.base.plot import Canvas, Frame, Grid, HSplit, ImageBox, RoundRectBg, Spacer, TextBox, TextStyle, VSplit
from src.sekai.base.text_layout import ink_centered_text_offset_y
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

SHOP_TYPE_LABELS = {
    "blueprint_daily": "每日蓝图",
    "blueprint_weekly": "每周蓝图",
    "tool": "工具",
    "material": "素材",
}
SHOP_TYPE_CAPTIONS = {
    "blueprint_daily": "每日刷新",
    "blueprint_weekly": "每周刷新",
}
SHOP_TYPE_ACCENTS: dict[str, Color] = {
    "blueprint_daily": (255, 170, 0, 255),
    "blueprint_weekly": (187, 51, 238, 255),
    "tool": (51, 187, 238, 255),
    "material": (102, 221, 17, 255),
}
SHOP_ACCENT_FALLBACK: Color = (150, 156, 170, 255)
SHOP_LIMIT_PASS = "limited_per_mysekai_colorful_pass"
SHOP_LIMIT_LABELS = {
    "none": "不限",
    "daily": "每日",
    "weekly": "每周",
    SHOP_LIMIT_PASS: "每期通行证",
}
BLUEPRINT_TAB_LABELS = {"limited_term": "期间限定", "birthday_anniversary": "生日・周年纪念"}

# status chips (fill colours; the label is white bold text)
CHIP_GREEN: Color = (52, 168, 96, 255)
CHIP_RED: Color = (222, 72, 92, 255)
CHIP_BLUE: Color = (58, 140, 220, 255)
CHIP_GREY: Color = (140, 144, 156, 255)
CHIP_AMBER: Color = (226, 140, 20, 255)
CHIP_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=13, color=(255, 255, 255, 255))
CAPTION_STYLE = TextStyle(font=DEFAULT_FONT, size=15, color=DIM)

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


def _have_text(have: int | None, need: int, *, prefix: str = "x") -> tuple[str, tuple[int, int, int, int]]:
    if have is None:
        return f"{prefix}{_format_quantity(need)}", GRAY
    return f"{_format_quantity(have)}/{_format_quantity(need)}", GREEN if have >= need else RED


def _cost_row(images: ImageMap, costs, *, icon_size: int = 28, quantity_prefix: str = "x") -> None:
    """Icons with quantities (or have/need) for shop costs and blueprint materials."""

    with HSplit().set_content_align("l").set_item_align("c").set_sep(10):
        for cost in costs:
            with HSplit().set_content_align("l").set_item_align("c").set_sep(4):
                _icon(images, cost.image_path, icon_size)
                text, color = _have_text(cost.have_quantity, cost.quantity, prefix=quantity_prefix)
                TextBox(text, TextStyle(font=QTY_STYLE.font, size=QTY_STYLE.size, color=color))


def _empty_hint(text: str) -> None:
    TextBox(text, INFO_STYLE).set_padding((4, 2))


def _format_time(value: int | None, timezone: str) -> str:
    dt = datetime_from_millis(value, timezone) if value is not None else None
    return dt.strftime("%Y-%m-%d %H:%M") if dt is not None else "-"


# ---------------------------------------------------------------------------
# shop
# ---------------------------------------------------------------------------

SHOP_COL_COUNT = 4
SHOP_TILE_WIDTH = (PANEL_INNER_WIDTH - (SHOP_COL_COUNT - 1) * 10) // SHOP_COL_COUNT
SHOP_TILE_PADDING = 10
SHOP_TILE_INNER_WIDTH = SHOP_TILE_WIDTH - 2 * SHOP_TILE_PADDING
SHOP_ICON_WELL = 68
SHOP_COST_ROW_HEIGHT = 30
SHOP_BADGE_ROW_HEIGHT = 24
SHOP_HEADER_STATUS_WIDTH = 330

_NAME_STATUS_TAGS = re.compile(r"(?:\s*【[^【】]*】)+\s*$")


@dataclass(frozen=True)
class ShopItemState:
    """What a shop tile shows besides the goods: dimming, the limit line and its status chips."""

    available: bool
    limit_text: str
    badges: tuple[tuple[str, Color], ...]


def shop_item_display_name(item: MysekaiShopItem) -> str:
    """The item name, minus trailing ``【…】`` status tags once structured state fields are present.

    Cloud appends the status as ``【已持有】``-style suffixes for drawers that predate the state
    fields; with the fields present the chips carry that information, so the suffixes would only
    repeat it. A caller that sends no state fields keeps its name untouched.
    """

    name = item.name or f"ID {item.id}"
    if item.has_state_fields:
        stripped = _NAME_STATUS_TAGS.sub("", name).strip()
        if stripped:
            return stripped
    return name


def shop_limit_label(limit_type: str) -> str:
    return SHOP_LIMIT_LABELS.get(limit_type, limit_type)


def shop_item_state(item: MysekaiShopItem, pass_active: bool | None) -> ShopItemState:
    """Derive the tile state from the structured fields, falling back to the legacy fields.

    Legacy fallback (no state fields): only ``limited_per_mysekai_colorful_pass`` items depend on
    the pass, and an item is sold out once ``exchanged_count`` reaches ``exchange_limit_value``.
    With state fields the caller's ``available`` wins, and ``pass_active is False`` marks every
    item (the whole shop needs the pass), matching how Cloud computes ``available``.
    """

    limit_type = str(item.exchange_limit_type or "none")
    limit = item.exchange_limit_value
    count = item.exchanged_count
    limited = limit_type != "none" and limit is not None
    remaining = item.remaining_count
    if remaining is None and limited and count is not None:
        remaining = max(0, limit - count)
    bought = item.is_bought is True
    sold_out = limited and remaining == 0 and not bought
    needs_pass = pass_active is False and (item.has_state_fields or limit_type == SHOP_LIMIT_PASS)
    warehouse_full = item.material_capacity_count == 0
    if item.available is not None:
        available = item.available
    else:
        available = not (needs_pass or sold_out or bought or warehouse_full)

    if limit_type == "none":
        limit_text = "不限次数"
        if count:
            limit_text += f" · 已兑换 {count}"
    elif limit is None:
        limit_text = f"限制: {limit_type}"
        if count is not None:
            limit_text += f" · 已兑换 {count}"
    else:
        progress = f"{count if count is not None else '-'}/{limit}"
        label = shop_limit_label(limit_type)
        limit_text = f"{label}限购 {progress}" if label != limit_type else f"限购 {progress} ({limit_type})"

    badges: list[tuple[str, Color]] = []
    if item.owned is True:
        badges.append(("已持有", CHIP_BLUE))
    if bought:
        badges.append(("本期已购买", CHIP_GREY))
    elif sold_out:
        badges.append(("已兑完", CHIP_RED))
    elif limited and remaining is not None and item.is_bought is None:
        badges.append((f"剩余{remaining}次", CHIP_GREEN))
    if needs_pass:
        badges.append(("需通行证", CHIP_AMBER))
    if warehouse_full:
        badges.append(("仓库已满", CHIP_AMBER))
    if not available and not any(fill != CHIP_BLUE and fill != CHIP_GREEN for _, fill in badges):
        badges.append(("不可购买", CHIP_GREY))
    return ShopItemState(available=available, limit_text=limit_text, badges=tuple(badges))


def _chip(text: str, fill: Color, *, style: TextStyle = CHIP_STYLE, radius: int = 9) -> TextBox:
    """A rounded status chip; the label's ink is centred by its measured glyph bounds."""

    offset_y = ink_centered_text_offset_y(style.font, style.size, text, style.size)
    return (
        TextBox(text, style)
        .set_padding((8, 3))
        .set_text_offset((0, offset_y))
        .set_bg(RoundRectBg(fill, radius, blur_glass=False))
    )


def _icon_well(images: ImageMap, path: AssetKey | None, size: int, *, dim: bool = False) -> None:
    """A rounded white well with the icon centred; a dimmed well fades the icon with the tile."""

    image = _image(images, path)
    well_fill = (255, 255, 255, 120) if dim else (255, 255, 255, 190)
    with Frame().set_size((size, size)).set_content_align("c").set_bg(RoundRectBg(well_fill, 10, blur_glass=False)):
        if image is not None:
            inner = size - 10
            ImageBox(
                image,
                size=(inner, inner),
                image_size_mode="fit",
                use_alpha_blend=True,
                alpha_adjust=0.45 if dim else 1.0,
            ).set_content_align("c")
        else:
            Spacer(w=size - 10, h=size - 10)


def _draw_shop_item(item: MysekaiShopItem, images: ImageMap, pass_active: bool | None) -> None:
    state = shop_item_state(item, pass_active)
    tile_fill = (255, 255, 255, 125) if state.available else (206, 208, 216, 125)
    name_color = NAME_STYLE.color if state.available else DIM
    with (
        VSplit()
        .set_w(SHOP_TILE_WIDTH)
        .set_content_align("lt")
        .set_item_align("lt")
        .set_sep(6)
        .set_padding(SHOP_TILE_PADDING)
        .set_bg(RoundRectBg(tile_fill, 10, blur_glass=False))
    ):
        with HSplit().set_content_align("l").set_item_align("t").set_sep(8):
            _icon_well(images, item.image_path, SHOP_ICON_WELL, dim=not state.available)
            with VSplit().set_content_align("lt").set_item_align("lt").set_sep(2):
                name_width = SHOP_TILE_INNER_WIDTH - SHOP_ICON_WELL - 8
                TextBox(
                    shop_item_display_name(item),
                    NAME_STYLE.replace(color=name_color),
                    line_count=2,
                    overflow="shrink",
                ).set_w(name_width)
                TextBox(f"x{_format_quantity(item.quantity)}", QTY_STYLE.replace(color=name_color))
        with Frame().set_size((SHOP_TILE_INNER_WIDTH, SHOP_COST_ROW_HEIGHT)).set_content_align("l"):
            if item.costs:
                _cost_row(images, item.costs, icon_size=26, quantity_prefix="")
            else:
                TextBox("免费", QTY_STYLE.replace(color=GREEN))
        TextBox(state.limit_text, SMALL_STYLE.replace(color=DIM), overflow="shrink").set_w(SHOP_TILE_INNER_WIDTH)
        with Frame().set_size((SHOP_TILE_INNER_WIDTH, SHOP_BADGE_ROW_HEIGHT)).set_content_align("l"):
            if state.badges:
                with HSplit().set_content_align("l").set_item_align("c").set_sep(4):
                    for text, fill in state.badges:
                        _chip(text, fill)


def shop_title(shop: MysekaiShop) -> str:
    return shop.title or SHOP_TYPE_LABELS.get(shop.shop_type, shop.shop_type)


def _section_header(
    title: str, accent: Color, *, chips: list[tuple[str, Color]] = (), captions: list[str] = ()
) -> None:
    """Accent bar + title + status chips + dim captions: the header of every panel on these pages."""

    with HSplit().set_content_align("l").set_item_align("c").set_sep(10):
        Spacer(w=6, h=26).set_bg(RoundRectBg(accent, 3, blur_glass=False))
        TextBox(title, SECTION_STYLE)
        for text, fill in chips:
            _chip(text, fill)
        for caption in captions:
            if caption:
                TextBox(caption, CAPTION_STYLE)


def _draw_shop_header(shop: MysekaiShop, pass_active: bool | None) -> None:
    accent = SHOP_TYPE_ACCENTS.get(shop.shop_type, SHOP_ACCENT_FALLBACK)
    purchasable = sum(1 for item in shop.items if shop_item_state(item, pass_active).available)
    captions = []
    if shop.items and purchasable != len(shop.items):
        captions.append(f"可购买 {purchasable} 件")
    captions.append(SHOP_TYPE_CAPTIONS.get(shop.shop_type, ""))
    _section_header(shop_title(shop), accent, chips=[(f"{len(shop.items)} 件", accent)], captions=captions)


def _draw_shop(shop: MysekaiShop, images: ImageMap, pass_active: bool | None) -> None:
    with _panel():
        _draw_shop_header(shop, pass_active)
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


def _draw_shop_page_header(rqd: MysekaiShopRequest) -> None:
    items = [item for shop in rqd.shops for item in shop.items]
    purchasable = sum(1 for item in items if shop_item_state(item, rqd.pass_active).available)
    with _panel():
        with HSplit().set_content_align("l").set_item_align("c").set_sep(10):
            with (
                VSplit()
                .set_content_align("lt")
                .set_item_align("lt")
                .set_sep(2)
                .set_w(PANEL_INNER_WIDTH - SHOP_HEADER_STATUS_WIDTH - 10)
            ):
                TextBox(rqd.title or "烤森商店", TITLE_STYLE, overflow="shrink").set_w(
                    PANEL_INNER_WIDTH - SHOP_HEADER_STATUS_WIDTH - 14
                )
                if items:
                    TextBox(f"共 {len(items)} 件商品 · 可购买 {purchasable} 件", INFO_STYLE)
            with HSplit().set_content_align("r").set_item_align("c").set_sep(8).set_w(SHOP_HEADER_STATUS_WIDTH):
                if rqd.pass_active is not None:
                    TextBox("缤纷通行证", INFO_STYLE)
                    if rqd.pass_active:
                        _chip("生效中", CHIP_GREEN, style=CHIP_STYLE.replace(size=15), radius=11)
                    else:
                        _chip("未生效", CHIP_GREY, style=CHIP_STYLE.replace(size=15), radius=11)


async def _build_mysekai_shop_canvas(rqd: MysekaiShopRequest) -> Canvas:
    images = await _load_images(_shop_image_paths(rqd))
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16):
            if rqd.profile is not None:
                await get_profile_card(rqd.profile)
            _draw_shop_page_header(rqd)
            if not rqd.shops:
                with _panel():
                    with VSplit().set_content_align("lt").set_item_align("lt").set_sep(4).set_padding(6):
                        TextBox("暂无可显示的商品", SECTION_STYLE.replace(color=DIM))
                        hint = (
                            "通行证未生效时商店内的商品均不可购买"
                            if rqd.pass_active is False
                            else "已购买、已达上限或当前不可购买的商品不在此列表中"
                        )
                        TextBox(hint, INFO_STYLE.replace(color=DIM))
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
BULK_ACCENT: Color = (102, 204, 92, 255)
BULK_GROUP_ACCENT: Color = (96, 104, 124, 255)


def bulk_target_state_text(checked: bool | None) -> tuple[str, tuple[int, ...]] | None:
    if checked is None:
        return None
    return ("已勾选", CHIP_GREEN) if checked else ("未勾选", CHIP_GREY)


def _draw_bulk_target(target, images: ImageMap) -> None:
    dim = target.checked is False
    tile_fill = (206, 208, 216, 125) if dim else (255, 255, 255, 125)
    with (
        HSplit()
        .set_w(BULK_TARGET_WIDTH)
        .set_content_align("l")
        .set_item_align("c")
        .set_sep(8)
        .set_padding(8)
        .set_bg(RoundRectBg(tile_fill, 10, blur_glass=False))
    ):
        _icon_well(images, target.image_path, 48, dim=dim)
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(4):
            TextBox(target.name, NAME_STYLE.replace(color=DIM if dim else NAME_STYLE.color), overflow="shrink").set_w(
                BULK_TARGET_WIDTH - 80
            )
            with HSplit().set_content_align("l").set_item_align("c").set_sep(4):
                if target.fixture_count is not None:
                    _chip(f"x{target.fixture_count}", CHIP_BLUE)
                if state := bulk_target_state_text(target.checked):
                    _chip(*state)


def _draw_bulk_group(group: MysekaiBulkHarvestTargetGroup, images: ImageMap) -> None:
    checked = sum(1 for target in group.targets if target.checked)
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
            _chip(f"{len(group.targets)} 种", BULK_GROUP_ACCENT)
            if group.targets and checked:
                TextBox(f"已勾选 {checked} 种", CAPTION_STYLE)
            if group.required_tool_name or group.required_tool_image_path:
                TextBox("所需道具", CAPTION_STYLE)
                with (
                    HSplit()
                    .set_content_align("l")
                    .set_item_align("c")
                    .set_sep(4)
                    .set_padding((6, 2))
                    .set_bg(RoundRectBg((255, 255, 255, 170), 8, blur_glass=False))
                ):
                    if group.required_tool_image_path:
                        _icon(images, group.required_tool_image_path, 26)
                    if group.required_tool_name:
                        TextBox(group.required_tool_name, INFO_STYLE)
        if not group.targets:
            _empty_hint("没有可一键采集的对象")
            return
        with Grid(col_count=BULK_TARGET_COL_COUNT).set_sep(8, 8).set_item_align("lt"):
            for target in group.targets:
                _draw_bulk_target(target, images)


def _draw_bulk_site(site: MysekaiBulkHarvestSite, images: ImageMap) -> None:
    targets = sum(len(group.targets) for group in site.groups)
    with _panel():
        with HSplit().set_content_align("l").set_item_align("c").set_sep(10):
            Spacer(w=6, h=26).set_bg(RoundRectBg(BULK_ACCENT, 3, blur_glass=False))
            if site.image_path:
                with (
                    Frame()
                    .set_size((104, 62))
                    .set_content_align("c")
                    .set_bg(RoundRectBg((255, 255, 255, 150), 10, blur_glass=False))
                ):
                    _icon(images, site.image_path, (96, 56))
            TextBox(site.name, SECTION_STYLE)
            _chip(f"{len(site.groups)} 组", BULK_ACCENT)
            if targets:
                TextBox(f"{targets} 种采集对象", CAPTION_STYLE)
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
                with HSplit().set_content_align("l").set_item_align("c").set_sep(10):
                    TextBox("一键采集对象", TITLE_STYLE)
                    if rqd.sites:
                        _chip(f"{len(rqd.sites)} 个地点", BULK_ACCENT)
                TextBox("灰色为未勾选的对象；x 数字为该对象在地图上的数量", CAPTION_STYLE)
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
BLUEPRINT_TAB_ACCENTS: dict[str, Color] = {
    "limited_term": (255, 170, 0, 255),
    "birthday_anniversary": (150, 110, 230, 255),
}
BLUEPRINT_ACCENT_FALLBACK: Color = (58, 140, 220, 255)


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


def blueprint_period_state(entry: MysekaiBlueprintTermEntry, timezone: str, now: datetime) -> tuple[str, Color] | None:
    """``(chip label, chip fill)`` for the entry's period, or None when it carries no period."""

    if entry.start_at is None and entry.end_at is None:
        return None
    end = datetime_from_millis(entry.end_at, timezone) if entry.end_at is not None else None
    start = datetime_from_millis(entry.start_at, timezone) if entry.start_at is not None else None
    if end is not None and end < now:
        return "已结束", CHIP_GREY
    if start is not None and start > now:
        return "未开始", CHIP_BLUE
    return "进行中", CHIP_GREEN


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


def blueprint_craft_chip(entry: MysekaiBlueprintTermEntry) -> tuple[str, Color] | None:
    craft = blueprint_craft_text(entry)
    if craft is None:
        return None
    text, color = craft
    if color == RED:
        return "已达上限", CHIP_RED
    if color == GREEN:
        return text.replace("制作次数 ", "制作 "), CHIP_GREEN
    return text, CHIP_GREY


def _draw_blueprint(entry: MysekaiBlueprintTermEntry, images: ImageMap, timezone: str, now: datetime) -> None:
    state = blueprint_period_state(entry, timezone, now)
    ended = state is not None and state[0] == "已结束"
    tile_fill = (206, 208, 216, 125) if ended else (255, 255, 255, 125)
    name_color = DIM if ended else NAME_STYLE.color
    with (
        VSplit()
        .set_w(BLUEPRINT_TILE_WIDTH)
        .set_content_align("lt")
        .set_item_align("lt")
        .set_sep(6)
        .set_padding(SHOP_TILE_PADDING)
        .set_bg(RoundRectBg(tile_fill, 10, blur_glass=False))
    ):
        with HSplit().set_content_align("l").set_item_align("t").set_sep(10):
            _icon_well(images, entry.image_path, 80, dim=ended)
            with VSplit().set_content_align("lt").set_item_align("lt").set_sep(4):
                width = BLUEPRINT_TILE_WIDTH - 2 * SHOP_TILE_PADDING - 90
                TextBox(
                    entry.name,
                    NAME_STYLE.replace(color=name_color),
                    line_count=2,
                    use_real_line_count=True,
                    overflow="shrink",
                ).set_w(width)
                period, _color = blueprint_period_text(entry, timezone, now)
                if period:
                    for suffix in (" (已结束)", " (未开始)"):
                        period = period.removesuffix(suffix)
                    TextBox(period, SMALL_STYLE.replace(color=DIM)).set_w(width)
                with HSplit().set_content_align("l").set_item_align("c").set_sep(4):
                    if state is not None:
                        _chip(*state)
                    if craft := blueprint_craft_chip(entry):
                        _chip(*craft)
        if entry.cost_materials:
            _cost_row(images, entry.cost_materials, icon_size=32)


def _draw_blueprint_tab(tab: MysekaiBlueprintTermTab, images: ImageMap, timezone: str, now: datetime) -> None:
    accent = BLUEPRINT_TAB_ACCENTS.get(tab.tab_type, BLUEPRINT_ACCENT_FALLBACK)
    active = sum(
        1 for entry in tab.blueprints if (blueprint_period_state(entry, timezone, now) or ("", None))[0] == "进行中"
    )
    with _panel():
        title = tab.title or BLUEPRINT_TAB_LABELS.get(tab.tab_type, tab.tab_type)
        captions = [f"进行中 {active} 件"] if tab.blueprints and active != len(tab.blueprints) else []
        _section_header(title, accent, chips=[(f"{len(tab.blueprints)} 件", accent)], captions=captions)
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
    entries = [entry for tab in rqd.tabs for entry in tab.blueprints]
    active = sum(
        1 for entry in entries if (blueprint_period_state(entry, rqd.timezone, now) or ("", None))[0] == "进行中"
    )
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16):
            if rqd.profile is not None:
                await get_profile_card(rqd.profile)
            with _panel():
                with HSplit().set_content_align("l").set_item_align("c").set_sep(10):
                    TextBox("期间限定蓝图", TITLE_STYLE)
                    if entries:
                        _chip(f"共 {len(entries)} 件", BLUEPRINT_ACCENT_FALLBACK)
                        _chip(f"进行中 {active} 件", CHIP_GREEN if active else CHIP_GREY)
                TextBox("材料为「已有/所需」，绿色表示足够；灰色为已结束的蓝图", CAPTION_STYLE)
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
