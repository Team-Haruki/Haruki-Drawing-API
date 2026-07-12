import asyncio
import logging

from PIL import Image

from src.core.heavy_render_pool import EncodedImagePayload
from src.sekai.base.draw import BG_PADDING, SEKAI_BLUE_BG, Canvas, add_request_watermark, roundrect_bg
from src.sekai.base.painter import DEFAULT_BOLD_FONT, DEFAULT_FONT, get_font, get_text_size
from src.sekai.base.plot import Frame, Grid, HSplit, ImageBox, TextBox, TextStyle, VSplit
from src.sekai.base.utils import get_img_from_path
from src.sekai.profile.drawer import get_profile_card
from src.sekai.skia_renderer.canvas import render_canvas_payload, skia_plot_enabled
from src.settings import ASSETS_BASE_DIR

from .model import InventoryItem, InventoryListRequest, InventorySection

logger = logging.getLogger(__name__)

PANEL_WIDTH = 1180
TILE_WIDTH = 268
TILE_HEIGHT = 112
TILE_COL_COUNT = 4
TILE_GAP = 10
ICON_SIZE = 58
ITEM_TEXT_WIDTH = 178

TITLE_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=30, color=(45, 50, 70))
SECTION_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=22, color=(46, 52, 72))
COUNT_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(47, 76, 120))
NAME_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=17, color=(48, 52, 68))
DESC_STYLE = TextStyle(font=DEFAULT_FONT, size=12, color=(92, 100, 122))
QTY_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=(38, 50, 76))
ICON_FALLBACK_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=26, color=(112, 122, 148))


async def _build_inventory_canvas(rqd: InventoryListRequest) -> Canvas:
    icon_cache = await _load_inventory_icons(rqd.sections)

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16):
            await get_profile_card(rqd.profile.to_profile_card_request())

            with VSplit().set_w(PANEL_WIDTH).set_content_align("lt").set_item_align("lt").set_sep(14):
                _draw_header()
                for section in rqd.sections:
                    _draw_section(section, icon_cache)

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_inventory_list_image(rqd: InventoryListRequest) -> Image.Image:
    canvas = await _build_inventory_canvas(rqd)
    return await canvas.get_img()


async def try_render_inventory_list_payload(rqd: InventoryListRequest) -> EncodedImagePayload | None:
    """Skia 路径：构建同一棵 widget 树并经 IRPainter 渲染；不可用时返回 None 回退 Pillow。"""
    if not skia_plot_enabled():
        return None
    canvas = await _build_inventory_canvas(rqd)
    return await render_canvas_payload(canvas)


async def _load_inventory_icons(sections: list[InventorySection]) -> dict[str, Image.Image]:
    paths = []
    seen = set()
    for section in sections:
        for item in section.items:
            path = (item.icon_path or "").strip()
            if not path or path in seen:
                continue
            seen.add(path)
            paths.append(path)

    if not paths:
        return {}

    loaded = await asyncio.gather(*[_load_inventory_icon(path) for path in paths])
    return {path: icon for path, icon in zip(paths, loaded) if icon is not None}


async def _load_inventory_icon(path: str) -> Image.Image | None:
    try:
        return await get_img_from_path(ASSETS_BASE_DIR, path, on_missing="raise")
    except (FileNotFoundError, OSError, ValueError) as exc:
        logger.debug("背包图标缺失，使用轻量占位: %s (%s)", path, exc)
        return None


def _draw_header() -> None:
    TextBox("背包一览", TITLE_STYLE).set_padding((8, 0))


def _draw_section(section: InventorySection, icon_cache: dict[str, Image.Image]) -> None:
    with (
        VSplit()
        .set_w(PANEL_WIDTH)
        .set_content_align("lt")
        .set_item_align("lt")
        .set_sep(10)
        .set_padding(12)
        .set_bg(roundrect_bg(alpha=72, blur_glass_kwargs={"blur": 8}))
    ):
        with HSplit().set_w(PANEL_WIDTH - 24).set_content_align("lt").set_item_align("c").set_sep(8):
            TextBox(section.title, SECTION_STYLE).set_padding(0)
            TextBox(f"{len(section.items)}", COUNT_STYLE).set_padding((8, 1)).set_bg(
                roundrect_bg(fill=(255, 255, 255, 96), radius=8)
            )

        with Grid(col_count=TILE_COL_COUNT).set_sep(TILE_GAP, TILE_GAP).set_item_align("lt"):
            for item in section.items:
                _draw_item_tile(item, icon_cache)


def _draw_item_tile(item: InventoryItem, icon_cache: dict[str, Image.Image]) -> None:
    icon = icon_cache.get((item.icon_path or "").strip())
    with (
        HSplit()
        .set_size((TILE_WIDTH, TILE_HEIGHT))
        .set_content_align("lt")
        .set_item_align("t")
        .set_sep(6)
        .set_padding((8, 7))
        .set_bg(roundrect_bg(fill=(255, 255, 255, 92), radius=8, blur_glass_kwargs={"blur": 6}))
    ):
        with Frame().set_size((ICON_SIZE, ICON_SIZE)).set_content_align("c"):
            if icon is not None:
                ImageBox(icon, size=(ICON_SIZE - 8, ICON_SIZE - 8), image_size_mode="fit").set_content_align("c")
            else:
                TextBox("?", ICON_FALLBACK_STYLE).set_w(ICON_SIZE - 8).set_content_align("c")

        with VSplit().set_w(ITEM_TEXT_WIDTH).set_content_align("lt").set_item_align("lt").set_sep(3):
            TextBox(item.name, _name_style(item.name), line_count=2, overflow="clip").set_w(
                ITEM_TEXT_WIDTH
            ).set_padding(0)
            TextBox(_item_description_text(item), DESC_STYLE, line_count=2, overflow="clip").set_w(
                ITEM_TEXT_WIDTH
            ).set_padding(0)
            quantity = _format_quantity(item.quantity)
            TextBox(quantity, _quantity_style(quantity), overflow="clip").set_w(ITEM_TEXT_WIDTH).set_content_align(
                "r"
            ).set_padding(0)


def _item_description_text(item: InventoryItem) -> str:
    description = " ".join((item.description or "").split())
    if description:
        return description
    if item.recovery_value:
        return f"+{item.recovery_value} 能量"
    if item.resource_type == "coin":
        return "金币"
    if item.resource_type == "jewel":
        return "水晶"
    if item.resource_type == "virtual_coin":
        return "虚拟币"
    if item.resource_type == "boost_item":
        return "火罐"
    if item.resource_type == "event_item":
        return "活动"
    if item.resource_type in {"gacha_ticket", "gacha_ceil_item"}:
        return "招募"
    if item.resource_type in {"practice_ticket", "skill_practice_ticket"}:
        return "育成"
    if item.resource_type == "mysekai_material":
        return "MySekai"
    return f"ID {item.id}"


def _format_quantity(value: int) -> str:
    return f"{value:,}"


def _quantity_style(text: str) -> TextStyle:
    for size in range(QTY_STYLE.size, 9, -1):
        if get_text_size(get_font(QTY_STYLE.font, size), text)[0] <= ITEM_TEXT_WIDTH:
            return TextStyle(font=QTY_STYLE.font, size=size, color=QTY_STYLE.color)
    return TextStyle(font=QTY_STYLE.font, size=10, color=QTY_STYLE.color)


def _name_style(text: str) -> TextStyle:
    for size in range(NAME_STYLE.size, 10, -1):
        if _fits_lines(text, NAME_STYLE.font, size, ITEM_TEXT_WIDTH, 2):
            return TextStyle(font=NAME_STYLE.font, size=size, color=NAME_STYLE.color)
    return TextStyle(font=NAME_STYLE.font, size=11, color=NAME_STYLE.color)


def _fits_lines(text: str, font_path: str, size: int, width: int, line_count: int) -> bool:
    font = get_font(font_path, size)
    used_lines = 0
    for raw_line in str(text).split("\n"):
        line = raw_line
        while line:
            if used_lines >= line_count:
                return False
            if get_text_size(font, line)[0] <= width:
                used_lines += 1
                break
            clip_idx = _clip_text_to_width(line, font, width)
            if clip_idx <= 0:
                return False
            used_lines += 1
            line = line[clip_idx:]
    return used_lines <= line_count


def _clip_text_to_width(text: str, font, width: int) -> int:
    left_idx, right_idx = 0, len(text)
    while left_idx <= right_idx:
        mid_idx = (left_idx + right_idx) // 2
        measured_width = get_text_size(font, text[:mid_idx])[0]
        if measured_width < width:
            left_idx = mid_idx + 1
        elif measured_width > width:
            right_idx = mid_idx - 1
        else:
            return mid_idx
    return max(1, right_idx)
