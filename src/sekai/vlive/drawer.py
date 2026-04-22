import asyncio
import logging
import time
from datetime import datetime

from PIL import Image

from src.sekai.base.draw import BG_PADDING, SEKAI_BLUE_BG, add_request_watermark, roundrect_bg
from src.sekai.base.painter import DEFAULT_BOLD_FONT, DEFAULT_FONT
from src.sekai.base.plot import Canvas, Frame, Grid, HSplit, ImageBox, Spacer, TextBox, TextStyle, VSplit
from src.sekai.base.timezone import request_now
from src.sekai.base.utils import (
    build_rendered_image_cache_key,
    get_composed_image_cached,
    get_composed_image_disk_cached,
    get_img_from_path,
    get_readable_timedelta,
    put_composed_image_cache,
    put_composed_image_disk_cache,
)
from src.settings import ASSETS_BASE_DIR

from .model import VLiveBrief, VLiveListRequest

_perf_logger = logging.getLogger("vlive.draw.perf")
_VLIVE_LIST_ENTRY_CACHE_NAMESPACE = "vlive_list_entry"


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


def _build_vlive_entry_cache_key(vlive: VLiveBrief, now: datetime) -> str:
    return build_rendered_image_cache_key(
        "vlive_list_entry",
        vlive,
        extra={
            "version": 8,
            "state": "living" if vlive.living else "upcoming",
            "bucket": now.strftime("%Y%m%d%H%M"),
        },
    )


async def _preload_vlive_entry_assets(vlive: VLiveBrief) -> dict[str, object]:
    tasks = {}
    if vlive.banner_path:
        tasks["banner"] = get_img_from_path(ASSETS_BASE_DIR, vlive.banner_path)
    if vlive.rewards:
        tasks["rewards"] = asyncio.gather(
            *[get_img_from_path(ASSETS_BASE_DIR, item.image_path) for item in vlive.rewards]
        )
    if vlive.characters:
        tasks["characters"] = asyncio.gather(
            *[get_img_from_path(ASSETS_BASE_DIR, item.icon_path) for item in vlive.characters]
        )
    if not tasks:
        return {}
    keys = list(tasks.keys())
    values = await asyncio.gather(*tasks.values())
    return dict(zip(keys, values))


async def _compose_vlive_entry_image(
    vlive: VLiveBrief,
    loaded: dict[str, object],
    now: datetime,
) -> Image.Image:
    title_style = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(20, 20, 20))
    info_style = TextStyle(font=DEFAULT_FONT, size=18, color=(50, 50, 50))
    section_style = TextStyle(font=DEFAULT_BOLD_FONT, size=18, color=(50, 50, 50))
    quantity_style = TextStyle(font=DEFAULT_BOLD_FONT, size=12, color=(50, 50, 50))

    rewards = loaded.get("rewards", [])
    characters = loaded.get("characters", [])
    banner = loaded.get("banner")
    display_start_at, display_end_at = _get_display_window(vlive)

    with Canvas().set_padding(0) as canvas:
        with VSplit().set_content_align("l").set_item_align("l").set_sep(12):
            TextBox(
                f"【{vlive.id}】{vlive.name}",
                title_style,
                line_count=2,
                use_real_line_count=True,
            ).set_w(724)

            with HSplit().set_content_align("c").set_item_align("c").set_sep(16):
                if banner is not None:
                    ImageBox(banner, size=(320, None), use_alpha_blend=True)

                with VSplit().set_content_align("l").set_item_align("l").set_sep(8):
                    TextBox(_build_vlive_time_text("开始于", display_start_at, now), info_style).set_w(388)
                    TextBox(_build_vlive_time_text("结束于", display_end_at, now), info_style).set_w(388)
                    TextBox(
                        f"{_build_vlive_status_text(vlive, now)} | 剩余场次: {vlive.rest_count}",
                        info_style,
                    ).set_w(388)

            if rewards or characters:
                with HSplit().set_content_align("t").set_item_align("t").set_sep(28):
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
                            with HSplit().set_content_align("l").set_item_align("c").set_sep(4):
                                for character_image in characters:
                                    ImageBox(character_image, size=(30, 30), use_alpha_blend=True)

    return await canvas.get_img()


async def _get_vlive_list_entry_image(vlive: VLiveBrief, now: datetime) -> Image.Image:
    cache_key = _build_vlive_entry_cache_key(vlive, now)

    cached = get_composed_image_cached(cache_key)
    if cached is not None:
        _perf_logger.info("vlive/list entry memory hit: id=%s", vlive.id)
        return cached

    disk_cached = get_composed_image_disk_cached(_VLIVE_LIST_ENTRY_CACHE_NAMESPACE, cache_key)
    if disk_cached is not None:
        put_composed_image_cache(cache_key, disk_cached)
        _perf_logger.info("vlive/list entry disk hit: id=%s", vlive.id)
        return disk_cached

    loaded = await _preload_vlive_entry_assets(vlive)
    image = await _compose_vlive_entry_image(vlive, loaded, now)
    put_composed_image_cache(cache_key, image)
    put_composed_image_disk_cache(_VLIVE_LIST_ENTRY_CACHE_NAMESPACE, cache_key, image)
    _perf_logger.info("vlive/list entry miss: id=%s size=%dx%d", vlive.id, image.width, image.height)
    return image


async def compose_vlive_list_image(rqd: VLiveListRequest) -> Image.Image:
    lives = rqd.lives
    now = request_now(rqd.timezone)

    _t0 = time.perf_counter()
    entry_images = await asyncio.gather(*[_get_vlive_list_entry_image(vlive, now) for vlive in lives]) if lives else []
    _t_entries = time.perf_counter() - _t0

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_padding(0).set_sep(16).set_item_align("lt").set_content_align("lt"):
            for entry_image in entry_images:
                with Frame().set_w(760).set_padding(18).set_bg(
                    roundrect_bg(alpha=80, blur_glass_kwargs={"blur": 8})
                ):
                    ImageBox(entry_image)

    add_request_watermark(canvas, rqd)
    _t0 = time.perf_counter()
    image = await canvas.get_img()
    _t_render = time.perf_counter() - _t0
    _perf_logger.info(
        "vlive/list total: entries=%.3fs render=%.3fs lives=%d image=%dx%d",
        _t_entries,
        _t_render,
        len(lives),
        image.width,
        image.height,
    )
    return image
