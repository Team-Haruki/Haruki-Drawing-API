from __future__ import annotations

import asyncio
from datetime import datetime
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

from src.core.image_payload import EncodedImagePayload
from src.sekai.base.draw import BG_PADDING, SEKAI_BLUE_BG, add_request_watermark, roundrect_bg
from src.sekai.base.plot import Canvas, CanvasImageBox, Flow, Frame, HSplit, ImageBox, TextBox, TextStyle, VSplit
from src.sekai.base.timezone import request_now
from src.sekai.base.utils import (
    build_rendered_image_cache_key,
    collect_asset_signatures,
    get_asset_image_ref,
    get_readable_timedelta,
)
from src.sekai.skia_renderer.canvas import render_canvas_payload, skia_plot_enabled
from src.settings import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT, DEFAULT_FONT

from .model import VLiveBrief, VLiveListRequest

_perf_logger = logging.getLogger("vlive.draw.perf")
_VLIVE_LIST_ENDPOINT = "vlive_list"
_VLIVE_ENTRY_CONTENT_W = 724


def _format_time(dt: datetime | None) -> str:
    if dt is None:
        return "-"
    return dt.strftime("%Y-%m-%d %H:%M:%S")


def _format_relative(target: datetime | None, now: datetime) -> str:
    if target is None:
        return "-"

    delta = target - now
    seconds = int(delta.total_seconds())
    if -60 < seconds < 60:
        return "刚刚"
    abs_seconds = abs(seconds)
    if abs_seconds >= 24 * 3600:
        days = abs_seconds // (24 * 3600)
        return f"{days}天后" if seconds > 0 else f"{days}天前"
    if seconds > 0:
        return f"{get_readable_timedelta(delta)}后"
    return f"{get_readable_timedelta(now - target)}前"


def _build_vlive_time_text(label: str, target: datetime | None, now: datetime) -> str:
    return f"{label} {_format_time(target)} ({_format_relative(target, now)})"


def _build_vlive_status_text(vlive: VLiveBrief, now: datetime) -> str:
    if vlive.living:
        return "当前Live进行中!"
    if vlive.current_start_at is not None:
        return f"下一场: {_format_relative(vlive.current_start_at, now)}"
    return "已结束"


def _get_display_window(vlive: VLiveBrief) -> tuple[datetime | None, datetime | None]:
    return vlive.current_start_at or vlive.start_at, vlive.current_end_at or vlive.end_at


def _vlive_entry_time_texts(vlive: VLiveBrief, now: datetime) -> tuple[str, str, str]:
    start, end = _get_display_window(vlive)
    return (
        _build_vlive_time_text("开始于", start, now),
        _build_vlive_time_text("结束于", end, now),
        f"{_build_vlive_status_text(vlive, now)} | 剩余场次: {vlive.rest_count}",
    )


def _build_vlive_entry_cache_key(
    vlive: VLiveBrief, now: datetime, *, time_texts: tuple[str, str, str] | None = None
) -> str:
    material = vlive.model_dump(mode="json")
    return build_rendered_image_cache_key(
        "vlive_list_entry",
        material,
        asset_signatures=collect_asset_signatures(ASSETS_BASE_DIR, material),
        extra={"time_texts": time_texts if time_texts is not None else _vlive_entry_time_texts(vlive, now)},
    )


async def _preload_vlive_entry_assets(vlive: VLiveBrief) -> dict[str, object]:
    tasks = {}
    if vlive.banner_path:
        tasks["banner"] = get_asset_image_ref(ASSETS_BASE_DIR, vlive.banner_path)
    if vlive.rewards:
        tasks["rewards"] = asyncio.gather(
            *[get_asset_image_ref(ASSETS_BASE_DIR, item.image_path) for item in vlive.rewards]
        )
    if vlive.characters:
        tasks["characters"] = asyncio.gather(
            *[get_asset_image_ref(ASSETS_BASE_DIR, item.icon_path) for item in vlive.characters]
        )
    if not tasks:
        return {}
    keys = list(tasks.keys())
    values = await asyncio.gather(*tasks.values())
    return dict(zip(keys, values))


def _build_vlive_entry_canvas(
    vlive: VLiveBrief,
    loaded: dict[str, object],
    now: datetime,
    *,
    time_texts: tuple[str, str, str] | None = None,
) -> Canvas:
    title_style = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(20, 20, 20))
    info_style = TextStyle(font=DEFAULT_FONT, size=18, color=(50, 50, 50))
    section_style = TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=(50, 50, 50))
    quantity_style = TextStyle(font=DEFAULT_BOLD_FONT, size=12, color=(50, 50, 50))

    rewards = loaded.get("rewards", [])
    characters = loaded.get("characters", [])
    banner = loaded.get("banner")
    start_text, end_text, status_text = time_texts if time_texts is not None else _vlive_entry_time_texts(vlive, now)

    with Canvas().set_padding(0) as canvas:
        with VSplit().set_content_align("l").set_item_align("l").set_sep(12):
            TextBox(
                f"【{vlive.id}】{vlive.name}",
                title_style,
                line_count=2,
                use_real_line_count=True,
            ).set_w(_VLIVE_ENTRY_CONTENT_W)

            with HSplit().set_content_align("c").set_item_align("c").set_sep(16):
                if banner is not None:
                    ImageBox(banner, size=(320, None), use_alpha_blend=True)

                with VSplit().set_content_align("l").set_item_align("l").set_sep(8):
                    TextBox(start_text, info_style).set_w(388)
                    TextBox(end_text, info_style).set_w(388)
                    TextBox(
                        status_text,
                        info_style,
                    ).set_w(388)

            if rewards or characters:
                with VSplit().set_content_align("l").set_item_align("l").set_sep(12):
                    if rewards:
                        with VSplit().set_content_align("l").set_item_align("l").set_sep(6):
                            TextBox("参与奖励", section_style)
                            with HSplit().set_content_align("l").set_item_align("t").set_sep(10):
                                for reward_model, reward_image in zip(vlive.rewards or [], rewards):
                                    quantity = max(1, reward_model.quantity)
                                    with VSplit().set_content_align("c").set_item_align("c").set_sep(4):
                                        ImageBox(reward_image, size=(44, 44), use_alpha_blend=True)
                                        TextBox(f"x{quantity}", quantity_style)

                    if characters:
                        with VSplit().set_content_align("l").set_item_align("l").set_sep(6):
                            TextBox("出演角色", section_style)
                            with (
                                Flow()
                                .set_w(_VLIVE_ENTRY_CONTENT_W)
                                .set_content_align("lt")
                                .set_item_align("lt")
                                .set_sep(4, 4)
                            ):
                                for character_image in characters:
                                    ImageBox(character_image, size=(30, 30), use_alpha_blend=True)

    return canvas


async def _compose_vlive_entry_image(vlive: VLiveBrief, loaded: dict[str, object], now: datetime) -> Image.Image:
    return await _build_vlive_entry_canvas(vlive, loaded, now).get_img()


async def _get_vlive_list_entry_canvas(vlive: VLiveBrief, now: datetime):
    from src.sekai.base.canvas_cache import prepare_cached_canvas

    time_texts = _vlive_entry_time_texts(vlive, now)
    cache_key = _build_vlive_entry_cache_key(vlive, now, time_texts=time_texts)

    async def build():
        loaded = await _preload_vlive_entry_assets(vlive)
        return _build_vlive_entry_canvas(vlive, loaded, now, time_texts=time_texts)

    return await prepare_cached_canvas(cache_key, build), cache_key


async def _build_vlive_list_canvas(rqd: VLiveListRequest, now: datetime | None = None) -> Canvas:
    lives = rqd.lives
    if now is None:
        now = request_now(rqd.timezone)

    entry_canvases = (
        await asyncio.gather(*[_get_vlive_list_entry_canvas(vlive, now) for vlive in lives]) if lives else []
    )

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_padding(0).set_sep(16).set_item_align("lt").set_content_align("lt"):
            for entry_canvas, cache_key in entry_canvases:
                with Frame().set_w(760).set_padding(18).set_bg(roundrect_bg(alpha=80, blur_glass_kwargs={"blur": 8})):
                    CanvasImageBox(entry_canvas, cache_key=cache_key)

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_vlive_list_image(rqd: VLiveListRequest) -> Image.Image:
    return await (await _build_vlive_list_canvas(rqd)).get_img()


async def try_render_vlive_list_payload(rqd: VLiveListRequest) -> EncodedImagePayload | None:
    """Skia 路径。没有整页 payload 缓存,这是有意的:调用方 (cloud) 已按 payload 去重,同一个 payload
    不会来第二次,页面级缓存永远不可能命中,而每次 miss 仍会 insert 挤占共享 LRU。"""
    if not skia_plot_enabled():
        return None
    # One `now` for the whole layout: recomputing it inside the builder could cross a minute
    # boundary mid-render and put two different living/upcoming states in one image.
    now = request_now(rqd.timezone)
    return await render_canvas_payload(lambda: _build_vlive_list_canvas(rqd, now=now), endpoint=_VLIVE_LIST_ENDPOINT)
