from __future__ import annotations

import asyncio
from datetime import datetime
import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from PIL import Image

from src.core.image_payload import EncodedImagePayload
from src.sekai.base.draw import BG_PADDING, SEKAI_BLUE_BG, add_request_watermark, roundrect_bg
from src.sekai.base.plot import (
    Canvas,
    CanvasImageBox,
    Flow,
    Frame,
    Grid,
    HSplit,
    ImageBox,
    TextBox,
    TextStyle,
    VSplit,
)
from src.sekai.base.timezone import request_now
from src.sekai.base.utils import (
    build_rendered_image_cache_key,
    collect_asset_signatures,
    get_asset_image_ref,
    get_readable_timedelta,
)
from src.sekai.skia_renderer.canvas import render_canvas_payload, skia_plot_enabled
from src.settings import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT, DEFAULT_FONT

from .model import (
    VLiveBrief,
    VLiveDetailLive,
    VLiveDetailRequest,
    VLiveListRequest,
    VLiveRewardItem,
    VLiveTotalCheerPointReward,
)

_perf_logger = logging.getLogger("vlive.draw.perf")
_VLIVE_LIST_ENDPOINT = "vlive_list"
_VLIVE_ENTRY_CONTENT_W = 724


# virtualLiveType -> short tag. ``normal`` (and an absent type, i.e. every pre-7.0.0 payload) draws
# no tag at all, so ordinary lives keep their exact layout. Unknown future types show verbatim.
_VLIVE_TYPE_LABELS: dict[str, str | None] = {
    "normal": None,
    "solo_virtual_live": "个人Live",
    "virtual_message": "Virtual Message",
    "cheerful_carnival": "欢乐嘉年华",
    "streaming": "直播",
    "beginner": "新手",
}
_TYPE_TAG_STYLE = TextStyle(font=DEFAULT_BOLD_FONT, size=14, color=(255, 255, 255))
_TYPE_TAG_FILL = (90, 110, 200, 220)


def vlive_type_label(virtual_live_type: str | None) -> str | None:
    kind = str(virtual_live_type or "").strip()
    if not kind:
        return None
    return _VLIVE_TYPE_LABELS.get(kind.lower(), kind)


def _type_tag(label: str) -> TextBox:
    tag_bg = roundrect_bg(fill=_TYPE_TAG_FILL, radius=6, blur_glass=False)
    return TextBox(label, _TYPE_TAG_STYLE).set_padding((8, 3)).set_bg(tag_bg)


def _vlive_entry_title(vlive: VLiveBrief) -> str:
    title = f"【{vlive.id}】{vlive.group_name or vlive.name}"
    if vlive.group_count:
        title += f" (共{vlive.group_count}场个人Live)"
    return title


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

    type_label = vlive_type_label(vlive.virtual_live_type)

    with Canvas().set_padding(0) as canvas:
        with VSplit().set_content_align("l").set_item_align("l").set_sep(12):
            if type_label is not None:
                _type_tag(type_label)
            TextBox(
                _vlive_entry_title(vlive),
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


# ========== vlive detail (JP 7.0.0 solo virtual live group + total cheer-point rewards) ==========

_VLIVE_DETAIL_ENDPOINT = "vlive_detail"
_DETAIL_PANEL_W = 760
_DETAIL_CONTENT_W = _DETAIL_PANEL_W - 36
_DETAIL_LIVE_COL_W = (_DETAIL_CONTENT_W - 12) // 2
_DETAIL_REWARD_ICON = 40
_DETAIL_REACHED_COLOR = (40, 150, 70)
_DETAIL_PENDING_COLOR = (130, 130, 130)
_DETAIL_RECEIVED_COLOR = (70, 110, 200)


def _detail_styles() -> dict[str, TextStyle]:
    return {
        "title": TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(20, 20, 20)),
        "section": TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(50, 50, 50)),
        "info": TextStyle(font=DEFAULT_FONT, size=18, color=(50, 50, 50)),
        "name": TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=(40, 40, 40)),
        "small": TextStyle(font=DEFAULT_FONT, size=14, color=(70, 70, 70)),
        "quantity": TextStyle(font=DEFAULT_BOLD_FONT, size=12, color=(50, 50, 50)),
    }


def _detail_live_status_text(live: VLiveDetailLive, now: datetime) -> str:
    if live.living:
        return "当前Live进行中!"
    if live.current_start_at is not None:
        start = live.current_start_at
        return f"下一场 {start.strftime('%m-%d %H:%M')} ({_format_relative(start, now)})"
    return "已结束"


def _detail_live_name(live: VLiveDetailLive, group_title: str) -> str:
    """The caller's ``short_name``, else the name without the shared group title (the distinguishing part)."""
    if short_name := (live.short_name or "").strip():
        return f"【{live.id}】{short_name}"
    name = (live.name or "").strip()
    title = group_title.strip()
    if title and name.startswith(title) and name[len(title) :].strip():
        name = name[len(title) :].strip()
    return f"【{live.id}】{name}"


def _detail_live_count_text(live: VLiveDetailLive) -> str:
    if live.schedule_count:
        return f"剩余 {live.rest_count}/{live.schedule_count} 场"
    return f"剩余 {live.rest_count} 场"


def _cheer_reward_state(reward: VLiveTotalCheerPointReward, total: int | None) -> tuple[str, tuple[int, int, int]]:
    if reward.received:
        return "已领取", _DETAIL_RECEIVED_COLOR
    if total is not None and total >= reward.threshold:
        return "已达成", _DETAIL_REACHED_COLOR
    return "未达成", _DETAIL_PENDING_COLOR


def _detail_summary_text(rqd: VLiveDetailRequest, now: datetime) -> str:
    living = sum(1 for live in rqd.lives if live.living)
    rest = sum(live.rest_count for live in rqd.lives)
    parts: list[str] = []
    if rqd.lives:
        parts.append(f"{len(rqd.lives)}场Live")
        if living:
            parts.append(f"{living}场进行中")
        parts.append(f"剩余场次: {rest}")
    elif rqd.end_at < now:
        parts.append("已结束")
    return " | ".join(parts)


async def _gather_reward_icons(rewards: list[VLiveRewardItem]) -> list[object]:
    if not rewards:
        return []
    return list(await asyncio.gather(*[get_asset_image_ref(ASSETS_BASE_DIR, item.image_path) for item in rewards]))


async def _preload_vlive_detail_assets(rqd: VLiveDetailRequest) -> dict[str, object]:
    """Resolve every icon concurrently. Missing assets become lazy placeholders (never an error)."""

    tasks: dict[str, object] = {}
    if rqd.banner_path:
        tasks["banner"] = get_asset_image_ref(ASSETS_BASE_DIR, rqd.banner_path)
    if rqd.lives:
        tasks["lives"] = asyncio.gather(
            *[
                get_asset_image_ref(ASSETS_BASE_DIR, live.character_icon_path) if live.character_icon_path else _none()
                for live in rqd.lives
            ]
        )
    if rqd.total_cheer_point_rewards:
        tasks["cheer_rewards"] = asyncio.gather(
            *[_gather_reward_icons(reward.rewards) for reward in rqd.total_cheer_point_rewards]
        )
    if rqd.surplus_reward is not None:
        tasks["surplus"] = _gather_reward_icons(rqd.surplus_reward.rewards)
    if rqd.override_cost is not None:
        tasks["override_cost"] = get_asset_image_ref(ASSETS_BASE_DIR, rqd.override_cost.image_path)
    if not tasks:
        return {}
    keys = list(tasks.keys())
    values = await asyncio.gather(*tasks.values())
    return dict(zip(keys, values))


async def _none() -> None:
    return None


def _reward_row(rewards: list[VLiveRewardItem], icons: list[object], styles: dict[str, TextStyle]) -> None:
    with HSplit().set_content_align("l").set_item_align("t").set_sep(8):
        for reward, icon in zip(rewards, icons):
            with VSplit().set_content_align("c").set_item_align("c").set_sep(2):
                ImageBox(icon, size=(_DETAIL_REWARD_ICON, _DETAIL_REWARD_ICON), use_alpha_blend=True)
                TextBox(f"x{max(1, reward.quantity)}", styles["quantity"])


def _detail_header(rqd: VLiveDetailRequest, banner: object | None, now: datetime, styles) -> None:
    type_label = vlive_type_label(rqd.virtual_live_type)
    with VSplit().set_content_align("l").set_item_align("l").set_sep(12):
        if type_label is not None:
            _type_tag(type_label)
        TextBox(f"【{rqd.id}】{rqd.title}", styles["title"], line_count=2, use_real_line_count=True).set_w(
            _DETAIL_CONTENT_W
        )
        with HSplit().set_content_align("c").set_item_align("c").set_sep(16):
            if banner is not None:
                ImageBox(banner, size=(320, None), use_alpha_blend=True)
            info_w = _DETAIL_CONTENT_W - (336 if banner is not None else 0)
            with VSplit().set_content_align("l").set_item_align("l").set_sep(8):
                TextBox(_build_vlive_time_text("开始于", rqd.start_at, now), styles["info"]).set_w(info_w)
                TextBox(_build_vlive_time_text("结束于", rqd.end_at, now), styles["info"]).set_w(info_w)
                summary = _detail_summary_text(rqd, now)
                if summary:
                    TextBox(summary, styles["info"]).set_w(info_w)


def _detail_lives(rqd: VLiveDetailRequest, icons: list[object | None], now: datetime, styles) -> None:
    with VSplit().set_content_align("l").set_item_align("l").set_sep(8):
        TextBox("场次一览", styles["section"])
        with Grid(col_count=2, item_align="lt", h_sep=12, v_sep=8):
            for live, icon in zip(rqd.lives, icons):
                with (
                    HSplit()
                    .set_w(_DETAIL_LIVE_COL_W)
                    .set_content_align("l")
                    .set_item_align("c")
                    .set_sep(8)
                    .set_padding(6)
                    .set_bg(roundrect_bg(fill=(255, 255, 255, 110), radius=8, blur_glass=False))
                ):
                    text_w = _DETAIL_LIVE_COL_W - 12
                    if icon is not None:
                        ImageBox(icon, size=(40, 40), use_alpha_blend=True)
                        text_w -= 48
                    with VSplit().set_content_align("l").set_item_align("l").set_sep(3):
                        TextBox(_detail_live_name(live, rqd.title), styles["name"], overflow="clip").set_w(text_w)
                        TextBox(_detail_live_status_text(live, now), styles["small"]).set_w(text_w)
                        TextBox(_detail_live_count_text(live), styles["small"]).set_w(text_w)


def _detail_cheer_rewards(rqd: VLiveDetailRequest, icons: list[list[object]], styles) -> None:
    rewards = rqd.total_cheer_point_rewards or []
    with VSplit().set_content_align("l").set_item_align("l").set_sep(8):
        header = "累计应援点奖励"
        if rqd.total_cheer_point is not None:
            header += f"  (当前累计 {rqd.total_cheer_point:,} pt)"
        TextBox(header, styles["section"])
        for reward, reward_icons in zip(rewards, icons):
            state, color = _cheer_reward_state(reward, rqd.total_cheer_point)
            with HSplit().set_content_align("l").set_item_align("c").set_sep(12):
                TextBox(f"{reward.threshold:,} pt", styles["name"]).set_w(110)
                TextBox(state, TextStyle(font=DEFAULT_BOLD_FONT, size=14, color=color)).set_w(56)
                _reward_row(reward.rewards, reward_icons, styles)


def _detail_surplus(rqd: VLiveDetailRequest, icons: list[object], styles) -> None:
    surplus = rqd.surplus_reward
    assert surplus is not None
    with VSplit().set_content_align("l").set_item_align("l").set_sep(8):
        TextBox("超额应援点奖励", styles["section"])
        with HSplit().set_content_align("l").set_item_align("c").set_sep(12):
            text = f"之后每 {surplus.base_point:,} pt"
            if surplus.received_count is not None:
                text += f" (已领取 {surplus.received_count} 次)"
            TextBox(text, styles["info"])
            _reward_row(surplus.rewards, icons, styles)


def _detail_override_cost(rqd: VLiveDetailRequest, icon: object | None, styles) -> None:
    cost = rqd.override_cost
    assert cost is not None
    with HSplit().set_content_align("l").set_item_align("c").set_sep(10):
        TextBox("虚拟道具消耗", styles["section"])
        if icon is not None:
            ImageBox(icon, size=(36, 36), use_alpha_blend=True)
        text = cost.name or ""
        if cost.have_quantity is not None:
            text = f"{text}  持有 {cost.have_quantity:,}".strip()
        if text:
            TextBox(text, styles["info"])


def _detail_panel() -> VSplit:
    return (
        VSplit()
        .set_w(_DETAIL_PANEL_W)
        .set_content_align("lt")
        .set_item_align("lt")
        .set_sep(12)
        .set_padding(18)
        .set_bg(roundrect_bg(alpha=80, blur_glass_kwargs={"blur": 8}))
    )


async def _build_vlive_detail_canvas(rqd: VLiveDetailRequest, now: datetime | None = None) -> Canvas:
    if now is None:
        now = request_now(rqd.timezone)
    loaded = await _preload_vlive_detail_assets(rqd)
    styles = _detail_styles()

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_padding(0).set_sep(16).set_item_align("lt").set_content_align("lt"):
            with _detail_panel():
                _detail_header(rqd, loaded.get("banner"), now, styles)
            if rqd.lives:
                with _detail_panel():
                    _detail_lives(rqd, loaded.get("lives", []), now, styles)
            if rqd.total_cheer_point_rewards or rqd.surplus_reward is not None or rqd.override_cost is not None:
                with _detail_panel():
                    if rqd.total_cheer_point_rewards:
                        _detail_cheer_rewards(rqd, loaded.get("cheer_rewards", []), styles)
                    if rqd.surplus_reward is not None:
                        _detail_surplus(rqd, loaded.get("surplus", []), styles)
                    if rqd.override_cost is not None:
                        _detail_override_cost(rqd, loaded.get("override_cost"), styles)

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_vlive_detail_image(rqd: VLiveDetailRequest) -> Image.Image:
    return await (await _build_vlive_detail_canvas(rqd)).get_img()


async def try_render_vlive_detail_payload(rqd: VLiveDetailRequest) -> EncodedImagePayload | None:
    """Native path. No page cache: the page carries relative times and the watermark clock."""
    if not skia_plot_enabled():
        return None
    now = request_now(rqd.timezone)
    return await render_canvas_payload(
        lambda: _build_vlive_detail_canvas(rqd, now=now), endpoint=_VLIVE_DETAIL_ENDPOINT
    )
