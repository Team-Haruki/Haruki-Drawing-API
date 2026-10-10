from __future__ import annotations

import asyncio
import base64
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

from src.core.image_payload import EncodedImagePayload
from src.sekai.base.draw import BG_PADDING, SEKAI_BLUE_BG, add_request_watermark, roundrect_bg
from src.sekai.base.paint_types import Color
from src.sekai.base.plot import (
    Canvas,
    Frame,
    HSplit,
    ImageBox,
    RoundClipFrame,
    RoundRectBg,
    Spacer,
    TextBox,
    TextStyle,
    VSplit,
)
from src.sekai.base.text_layout import ink_centered_text_offset_y
from src.sekai.base.timezone import datetime_from_millis, region_tag
from src.sekai.base.utils import EncodedImageRef, ImageSource, get_asset_image_ref, get_encoded_image_ref, run_in_pool
from src.sekai.mysekai.model import MysekaiHousingCompetitionEntry, MysekaiHousingCompetitionRequest
from src.sekai.skia_renderer.canvas import render_canvas_payload, skia_plot_enabled
from src.settings import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT, DEFAULT_FONT, DEFAULT_HEAVY_FONT

CARD_WIDTH = 680
CARD_PADDING = 12
CARD_INNER_WIDTH = CARD_WIDTH - 2 * CARD_PADDING
THUMBNAIL_SIZE = (CARD_INNER_WIDTH, 360)
HEADER_BANNER_SIZE = (210, 86)

INK = (40, 44, 64, 255)
INK_SOFT = (70, 74, 92, 255)
DIM = (120, 120, 130, 255)
WELL = (255, 255, 255, 190)
ACCENT: Color = (240, 120, 170, 255)
CHIP_SLATE: Color = (60, 64, 80, 235)
CHIP_BLUE: Color = (58, 140, 220, 255)
CHIP_GREEN: Color = (52, 168, 96, 255)
RANK_FILLS: dict[int, Color] = {1: (232, 170, 40, 255), 2: (150, 158, 176, 255), 3: (196, 120, 70, 255)}

TITLE_STYLE = TextStyle(font=DEFAULT_HEAVY_FONT, size=28, color=INK)
SUBTITLE_STYLE = TextStyle(font=DEFAULT_FONT, size=16, color=INK_SOFT)
CAPTION_STYLE = TextStyle(font=DEFAULT_FONT, size=14, color=DIM)
OWNER_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=INK)
WORK_STYLE = TextStyle(font=DEFAULT_FONT, size=16, color=INK_SOFT)
COUNT_STYLE = TextStyle(font=DEFAULT_HEAVY_FONT, size=30, color=INK)
CHIP_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=13, color=(255, 255, 255, 255))
RANK_STYLE = TextStyle(font=DEFAULT_HEAVY_FONT, size=20, color=(255, 255, 255, 255))


def _chip(text: str, fill: Color, *, style: TextStyle = CHIP_STYLE, radius: int = 9, padding=(8, 3)) -> TextBox:
    offset_y = ink_centered_text_offset_y(style.font, style.size, text, style.size)
    return (
        TextBox(text, style)
        .set_padding(padding)
        .set_text_offset((0, offset_y))
        .set_bg(RoundRectBg(fill, radius, blur_glass=False))
    )


def rank_chip_fill(rank: int) -> Color:
    return RANK_FILLS.get(rank, CHIP_SLATE)


async def _build_mysekai_housing_competition_canvas(rqd: MysekaiHousingCompetitionRequest) -> Canvas:
    banner_task = _load_optional_image(rqd.banner_image_base64, rqd.banner_image_path)
    entry_tasks = [
        _load_optional_image(entry.thumbnail_image_base64, entry.thumbnail_path) for entry in rqd.entries[:5]
    ]
    banner, *entry_images = await asyncio.gather(banner_task, *entry_tasks)
    entries = list(rqd.entries[:5])

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_sep(12).set_content_align("lt").set_item_align("lt"):
            with VSplit().set_w(CARD_WIDTH).set_padding(CARD_PADDING).set_sep(8).set_bg(roundrect_bg(alpha=80)):
                with HSplit().set_sep(12).set_content_align("lt").set_item_align("c"):
                    with RoundClipFrame(10).set_size(HEADER_BANNER_SIZE).set_content_align("c"):
                        ImageBox(banner, size=HEADER_BANNER_SIZE, image_size_mode="fill")
                    with VSplit().set_sep(6).set_content_align("lt").set_item_align("lt"):
                        TextBox(rqd.name, TITLE_STYLE, line_count=2, overflow="shrink").set_w(420)
                        with HSplit().set_sep(6).set_content_align("l").set_item_align("c"):
                            _chip(region_tag(rqd.region, rqd.region_label, rqd.competition_id), CHIP_SLATE)
                            _chip(f"统计 {rqd.unique_count} 个投稿", CHIP_BLUE)
                            if rqd.sampled_at:
                                sampled = datetime_from_millis(rqd.sampled_at, rqd.timezone)
                                TextBox(f"采样 {sampled.strftime('%m-%d %H:%M')}", CAPTION_STYLE)
                        if rqd.description:
                            TextBox(rqd.description, SUBTITLE_STYLE, line_count=2, overflow="shrink").set_w(420)

            if not entries:
                with VSplit().set_w(CARD_WIDTH).set_padding(18).set_bg(roundrect_bg(alpha=80)):
                    TextBox("没有采样到可显示的百景投稿", TITLE_STYLE.replace(size=22, color=DIM))
            for entry, image in zip(entries, entry_images, strict=False):
                _entry_block(entry, image)

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_mysekai_housing_competition_image(rqd: MysekaiHousingCompetitionRequest) -> Image.Image:
    return await (await _build_mysekai_housing_competition_canvas(rqd)).get_img()


async def try_render_mysekai_housing_competition_payload(
    rqd: MysekaiHousingCompetitionRequest,
) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(
        await _build_mysekai_housing_competition_canvas(rqd), endpoint="mysekai_housing_competition"
    )


async def _load_optional_image(image_base64: str | None, image_path: str | None) -> ImageSource:
    if image_base64 and image_base64.strip():
        return await run_in_pool(_decode_base64_ref, image_base64)
    return await get_asset_image_ref(ASSETS_BASE_DIR, image_path, on_missing="placeholder")


def _decode_base64_ref(data: str) -> EncodedImageRef:
    """base64 → EncodedImageRef（仅 header 探测）：Skia 路径原始 encoded bytes 直传 Rust，
    Pillow 回退在 paste 时按需解码——两边都不再于此处解码整图。"""
    payload = data.strip()
    if payload.lower().startswith("data:") and "," in payload:
        payload = payload.split(",", 1)[1]
    return get_encoded_image_ref(base64.b64decode(payload))


def _entry_block(entry: MysekaiHousingCompetitionEntry, image: ImageSource) -> None:
    with (
        VSplit()
        .set_w(CARD_WIDTH)
        .set_padding(CARD_PADDING)
        .set_sep(8)
        .set_bg(roundrect_bg(alpha=80))
        .set_content_align("lt")
        .set_item_align("lt")
    ):
        with HSplit().set_sep(10).set_content_align("l").set_item_align("c"):
            _chip(f"#{entry.rank}", rank_chip_fill(entry.rank), style=RANK_STYLE, radius=12, padding=(10, 4))
            with VSplit().set_sep(2).set_content_align("lt").set_item_align("lt"):
                TextBox(_owner_line(entry), OWNER_STYLE, overflow="shrink").set_w(CARD_INNER_WIDTH - 90)
                TextBox(_work_line(entry), WORK_STYLE, line_count=2, overflow="shrink").set_w(CARD_INNER_WIDTH - 90)
        with RoundClipFrame(12).set_size(THUMBNAIL_SIZE).set_content_align("c"):
            ImageBox(image, size=THUMBNAIL_SIZE, image_size_mode="fill")
        with HSplit().set_sep(10).set_content_align("l").set_item_align("c"):
            with Frame().set_size((6, 30)).set_bg(RoundRectBg(ACCENT, 3, blur_glass=False)):
                Spacer(w=6, h=30)
            TextBox("点赞数", CAPTION_STYLE)
            TextBox(f"{entry.review_count:,}", COUNT_STYLE)
            TextBox(f"排名 {entry.rank}", SUBTITLE_STYLE)
            _chip(_neighbor_text("上一名", entry.previous_review_count, entry.previous_delta, "还差"), CHIP_SLATE)
            _chip(_neighbor_text("下一名", entry.next_review_count, entry.next_delta, "领先"), CHIP_GREEN)
        if entry.word:
            TextBox(entry.word, WORK_STYLE.replace(color=DIM), line_count=2, overflow="shrink").set_w(CARD_INNER_WIDTH)


def _owner_line(entry: MysekaiHousingCompetitionEntry) -> str:
    owner = str(entry.owner_user_name or "").strip()
    return owner or "匿名玩家"


def _work_line(entry: MysekaiHousingCompetitionEntry) -> str:
    name = str(entry.name or "").strip() or "未命名投稿"
    return f"作品: {name}"


def _neighbor_text(label: str, score: int | None, delta: int | None, verb: str) -> str:
    if score is None:
        return f"{label} 无"
    return f"{label} {score:,}，{verb} {max(0, int(delta or 0)):,}"
