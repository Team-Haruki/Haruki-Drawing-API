import asyncio
import logging
import math
import time

from PIL import Image

from src.sekai.base.draw import (
    BG_PADDING,
    CHARACTER_COLOR_CODE,
    SEKAI_BLUE_BG,
    WIDGET_BG_COLOR,
    add_request_watermark,
    roundrect_bg,
)
from src.sekai.base.painter import DEFAULT_BOLD_FONT, DEFAULT_FONT, DEFAULT_HEAVY_FONT, color_code_to_rgb
from src.sekai.base.plot import (
    Canvas,
    Frame,
    Grid,
    HSplit,
    ImageBg,
    ImageBox,
    RoundRectBg,
    Spacer,
    TextBox,
    TextStyle,
    VSplit,
)
from src.sekai.base.timezone import datetime_from_millis, request_now
from src.sekai.base.utils import (
    build_rendered_image_cache_key,
    get_composed_image_cached,
    get_composed_image_disk_cached,
    get_img_from_path,
    get_readable_timedelta,
    put_composed_image_cache,
    put_composed_image_disk_cache,
)
from src.sekai.profile.drawer import (
    get_card_full_thumbnail,
    get_profile_card,
)
from src.settings import ASSETS_BASE_DIR

logger = logging.getLogger(__name__)
_perf_logger = logging.getLogger("event.draw.perf")
_EVENT_LIST_ENTRY_CACHE_NAMESPACE = "event_list_entry"

# 从 model.py 导入数据模型
from .model import (
    EventDetailRequest,
    # 兼容性别名
    EventHistoryInfo,
    EventListRequest,
    EventRecordRequest,
)


async def compose_event_detail_image(rqd: EventDetailRequest) -> Image.Image:
    detail = rqd.event_info
    now = request_now(rqd.timezone)
    _t0 = time.perf_counter()
    card_thumbs = await asyncio.gather(*[get_card_full_thumbnail(card) for card in rqd.event_cards])
    logger.debug(
        "[perf] compose_event_detail_image card thumbs %d: %.3fs",
        len(rqd.event_cards),
        time.perf_counter() - _t0,
    )

    if detail:
        banner_index = rqd.event_info.banner_index
    start_time = rqd.event_info.start_at
    end_time = rqd.event_info.end_at

    label_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(50, 50, 50))
    text_style = TextStyle(font=DEFAULT_FONT, size=24, color=(70, 70, 70))

    wl_chapters = rqd.event_info.wl_time_list
    if rqd.event_info.is_wl_event:
        for chapter in wl_chapters:
            chapter["start_time"] = datetime_from_millis(chapter["start_at"], rqd.timezone)
            chapter["end_time"] = datetime_from_millis(chapter["aggregate_at"] + 1000, rqd.timezone)
    use_story_bg = detail.event_type != "world_bloom"

    # 并行加载所有活动图片
    _event_img_tasks = {}
    bg_path = rqd.event_assets.event_story_bg_path if use_story_bg else rqd.event_assets.event_bg_path
    _event_img_tasks["bg"] = get_img_from_path(ASSETS_BASE_DIR, bg_path)
    event_chara_path = (rqd.event_assets.event_ban_chara_img or "").strip()
    if use_story_bg and event_chara_path:
        _event_img_tasks["chara"] = get_img_from_path(ASSETS_BASE_DIR, event_chara_path)
    _event_img_tasks["logo"] = get_img_from_path(ASSETS_BASE_DIR, rqd.event_assets.event_logo_path)
    if rqd.event_assets.ban_chara_icon_path:
        _event_img_tasks["ban_icon"] = get_img_from_path(ASSETS_BASE_DIR, rqd.event_assets.ban_chara_icon_path)
    if rqd.event_assets.event_attr_image_path:
        _event_img_tasks["attr"] = get_img_from_path(ASSETS_BASE_DIR, rqd.event_assets.event_attr_image_path)
    if rqd.event_assets.bonus_chara_path:
        for i, chara_path in enumerate(rqd.event_assets.bonus_chara_path):
            _event_img_tasks[f"bonus_chara_{i}"] = get_img_from_path(ASSETS_BASE_DIR, chara_path)
    _ek = list(_event_img_tasks.keys())
    _t0 = time.perf_counter()
    _ev = dict(zip(_ek, await asyncio.gather(*_event_img_tasks.values())))
    logger.debug("[perf] compose_event_detail_image event images %d: %.3fs", len(_ek), time.perf_counter() - _t0)
    event_bg = _ev["bg"]
    event_chara_img = _ev.get("chara")
    event_logo = _ev["logo"]
    ban_chara_icon = _ev.get("ban_icon")
    h = 1024
    w = min(int(h * 1.6), event_bg.size[0] * h // event_bg.size[1] if event_bg else int(h * 1.6))
    bg = ImageBg(event_bg, blur=False) if event_bg else SEKAI_BLUE_BG

    async def draw(w, h):
        with Canvas(bg=bg, w=w, h=h).set_padding(BG_PADDING).set_content_align("r") as canvas:
            with (
                Frame().set_size((w - BG_PADDING * 2, h - BG_PADDING * 2)).set_content_align("lb").set_padding((64, 0))
            ):
                if use_story_bg and event_chara_img is not None:
                    ImageBox(event_chara_img, size=(None, int(h * 0.9)), use_alpha_blend=True).set_offset(
                        (0, BG_PADDING)
                    )
            with (
                VSplit()
                .set_padding(16)
                .set_sep(16)
                .set_item_align("t")
                .set_content_align("t")
                .set_item_bg(roundrect_bg(alpha=80))
            ):
                # logo
                ImageBox(event_logo, size=(None, 150)).set_omit_parent_bg(True)

                # 活动ID和类型和箱活
                with VSplit().set_padding(16).set_sep(12).set_item_align("l").set_content_align("l"):
                    with HSplit().set_padding(0).set_sep(8).set_item_align("l").set_content_align("l"):
                        TextBox(rqd.region.upper(), label_style)
                        TextBox(f"{detail.id}", text_style)
                        Spacer(w=8)
                        TextBox("类型", label_style)
                        TextBox(f"{detail.event_type_name}", text_style)
                        if detail.banner_cid:
                            Spacer(w=8)
                            if ban_chara_icon is not None:
                                ImageBox(ban_chara_icon, size=(30, 30))
                            TextBox(f"{banner_index}箱", label_style)

                # 活动时间

                with VSplit().set_padding(16).set_sep(12).set_item_align("c").set_content_align("c"):
                    with HSplit().set_padding(0).set_sep(8).set_item_align("lb").set_content_align("lb"):
                        TextBox("开始时间", label_style)
                        TextBox(start_time.strftime("%Y-%m-%d %H:%M:%S"), text_style)
                    with HSplit().set_padding(0).set_sep(8).set_item_align("lb").set_content_align("lb"):
                        TextBox("结束时间", label_style)
                        TextBox(end_time.strftime("%Y-%m-%d %H:%M:%S"), text_style)

                    with HSplit().set_padding(0).set_sep(8).set_item_align("lb").set_content_align("lb"):
                        if start_time <= now <= end_time:
                            TextBox(f"距结束还有{get_readable_timedelta(end_time - now)}", text_style)
                        elif now > end_time:
                            TextBox("活动已结束", text_style)
                        else:
                            TextBox(f"距开始还有{get_readable_timedelta(start_time - now)}", text_style)

                    if detail.event_type == "world_bloom":
                        cur_chapter = None
                        for chapter in wl_chapters:
                            if chapter["start_time"] <= now <= chapter["end_time"]:
                                cur_chapter = chapter
                                break
                        if cur_chapter:
                            TextBox(
                                f"距章节结束还有{get_readable_timedelta(cur_chapter['end_time'] - now)}", text_style
                            )

                    # 进度条
                    progress = (now - start_time) / (end_time - start_time)
                    progress = min(max(progress, 0), 1)
                    progress_w, progress_h, border = 320, 8, 1
                    if detail.event_type == "world_bloom" and len(wl_chapters) > 1:
                        with Frame().set_padding(8).set_content_align("lt"):
                            Spacer(w=progress_w + border * 2, h=progress_h + border * 2).set_bg(
                                RoundRectBg((75, 75, 75, 255), 4)
                            )
                            for cid, chapter in enumerate(wl_chapters):
                                cprogress_start = (chapter["start_time"] - start_time) / (end_time - start_time)
                                cprogress_end = (chapter["end_time"] - start_time) / (end_time - start_time)
                                chara_color = color_code_to_rgb(CHARACTER_COLOR_CODE.get(cid + 1))
                                Spacer(w=int(progress_w * (cprogress_end - cprogress_start)), h=progress_h).set_bg(
                                    RoundRectBg(chara_color, 4)
                                ).set_offset((border + int(progress_w * cprogress_start), border))
                            Spacer(w=int(progress_w * progress), h=progress_h).set_bg(
                                RoundRectBg((255, 255, 255, 200), 4)
                            ).set_offset((border, border))
                    else:
                        with Frame().set_padding(8).set_content_align("lt"):
                            Spacer(w=progress_w + border * 2, h=progress_h + border * 2).set_bg(
                                RoundRectBg((75, 75, 75, 255), 4)
                            )
                            Spacer(w=int(progress_w * progress), h=progress_h).set_bg(
                                RoundRectBg((255, 255, 255, 255), 4)
                            ).set_offset((border, border))
                # 活动卡片
                event_cards = rqd.event_cards
                if event_cards:
                    with HSplit().set_padding(16).set_sep(16).set_item_align("c").set_content_align("c"):
                        TextBox("活动卡片", label_style)
                        event_cards = event_cards[:8]
                        card_num = len(event_cards)
                        if card_num <= 4:
                            col_count = card_num
                        elif card_num <= 6:
                            col_count = 3
                        else:
                            col_count = 4
                        with Grid(col_count=col_count).set_sep(4, 4):
                            for card, thumb in zip(event_cards, card_thumbs):
                                with VSplit().set_padding(0).set_sep(2).set_item_align("c").set_content_align("c"):
                                    ImageBox(thumb, size=(80, 80))
                                    TextBox(
                                        f"ID:{card.card_id}",
                                        TextStyle(font=DEFAULT_FONT, size=16, color=(75, 75, 75)),
                                        overflow="clip",
                                    )

                # 加成
                if detail.bonus_attr or detail.bonus_chara_id:
                    with HSplit().set_padding(16).set_sep(8).set_item_align("c").set_content_align("c"):
                        if detail.bonus_attr:
                            TextBox("加成属性", label_style)
                            ImageBox(
                                _ev["attr"],
                                size=(None, 40),
                            )
                        if rqd.event_assets.bonus_chara_path:
                            TextBox("加成角色", label_style)
                            bonus_chara_image = [
                                _ev[f"bonus_chara_{i}"] for i in range(len(rqd.event_assets.bonus_chara_path))
                            ]
                            with Grid(col_count=5 if len(bonus_chara_image) < 20 else 7).set_sep(4, 4):
                                for image in bonus_chara_image:
                                    ImageBox(image, size=(None, 40))

        add_request_watermark(canvas, rqd)
        return await canvas.get_img()

    return await draw(w, h)


# 合成活动记录图片
async def compose_event_record_image(rqd: EventRecordRequest) -> Image.Image:
    user_events = rqd.event_info
    user_wl_events = rqd.wl_event_info

    style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(50, 50, 50))
    style2 = TextStyle(font=DEFAULT_FONT, size=16, color=(70, 70, 70))
    style3 = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(70, 70, 70))
    style4 = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(50, 50, 50))

    def event_record_sort_rank(item: EventHistoryInfo) -> int | float:
        if item.rank is not None:
            return item.rank
        if item.rank_tier is not None:
            return item.rank_tier
        if item.rank_display:
            rank_display = item.rank_display.strip().upper()
            if rank_display.startswith("T") and rank_display[1:].isdigit():
                return int(rank_display[1:])
        return float("inf")

    async def draw_events(name, user_events: list[EventHistoryInfo]):
        topk = 30
        if any(item.rank is not None or item.rank_display or item.rank_tier is not None for item in user_events):
            has_rank = True
            title = f"排名前{topk}的{name}记录"
            user_events.sort(key=lambda x: (event_record_sort_rank(x), -x.event_point))
        else:
            has_rank = False
            title = f"活动点数前{topk}的{name}记录"
            user_events.sort(key=lambda x: -x.event_point)

        user_events = user_events[:topk]

        with (
            VSplit()
            .set_padding(16)
            .set_sep(16)
            .set_item_align("lt")
            .set_content_align("lt")
            .set_bg(roundrect_bg(alpha=80))
        ):
            TextBox(title, style1)

            th, sh, gh = 28, 40, 80
            with (
                HSplit()
                .set_padding(16)
                .set_sep(16)
                .set_item_align("lt")
                .set_content_align("lt")
                .set_bg(roundrect_bg(alpha=80))
            ):
                # 活动信息
                with VSplit().set_padding(0).set_sep(sh).set_item_align("c").set_content_align("c"):
                    TextBox("活动", style1).set_h(th).set_content_align("c")
                    for item in user_events:
                        event_start_at = item.start_at
                        event_end_at = item.end_at
                        with HSplit().set_padding(0).set_sep(4).set_item_align("l").set_content_align("l").set_h(gh):
                            if item.wl_chara_icon_path:
                                ImageBox(
                                    await get_img_from_path(ASSETS_BASE_DIR, item.wl_chara_icon_path), size=(None, gh)
                                )
                            ImageBox(await get_img_from_path(ASSETS_BASE_DIR, item.banner_path), size=(None, gh))
                            with VSplit().set_padding(0).set_sep(2).set_item_align("l").set_content_align("l"):
                                TextBox(f"【{item.id}】{item.event_name}", style2).set_w(150)
                                TextBox(f"S {event_start_at.strftime('%Y-%m-%d %H:%M')}", style2)
                                TextBox(f"T {event_end_at.strftime('%Y-%m-%d %H:%M')}", style2)
                # 排名
                if has_rank:
                    with VSplit().set_padding(0).set_sep(sh).set_item_align("c").set_content_align("c"):
                        TextBox("排名", style1).set_h(th).set_content_align("c")
                        for item in user_events:
                            rank_text = f"#{item.rank}" if item.rank is not None else (item.rank_display or "-")
                            TextBox(rank_text, style3, overflow="clip").set_h(gh).set_content_align("c")
                # 活动点数
                with VSplit().set_padding(0).set_sep(sh).set_item_align("c").set_content_align("c"):
                    TextBox("PT", style1).set_h(th).set_content_align("c")
                    for item in user_events:
                        TextBox(f"{item.event_point}", style3, overflow="clip").set_h(gh).set_content_align("c")

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16):
            await get_profile_card(rqd.user_info.to_profile_card_request())
            note = "每次上传时进行增量更新，未上传过的记录将会丢失"
            if rqd.rank_note:
                note = f"{note}\n{rqd.rank_note}"
            TextBox(note, style4).set_bg(roundrect_bg(alpha=80)).set_padding(12)
            with HSplit().set_sep(16).set_item_align("lt").set_content_align("lt"):
                if user_events:
                    await draw_events("活动", user_events)
                if user_wl_events:
                    await draw_events("WL单榜", user_wl_events)

    add_request_watermark(canvas, rqd)
    return await canvas.get_img()


def _resolve_event_list_entry_phase(start_at, end_at, now) -> str:
    if start_at <= now <= end_at:
        return "current"
    if now > end_at:
        return "past"
    return "upcoming"


def _resolve_event_list_entry_bg_color(phase: str) -> tuple[int, int, int, int]:
    if phase == "current":
        return (255, 250, 220, 200)
    if phase == "past":
        return (220, 220, 220, 200)
    return WIDGET_BG_COLOR


def _build_event_list_entry_cache_key(d, phase: str) -> str:
    request_payload = {
        "event": d.model_dump(mode="json"),
        "phase": phase,
    }
    return build_rendered_image_cache_key("event_list_entry", request_payload, extra={"version": 1})


async def _preload_event_entry_assets(d) -> dict[str, object]:
    tasks = {}
    if d.event_banner_path:
        tasks["banner"] = get_img_from_path(ASSETS_BASE_DIR, d.event_banner_path)
    if d.event_cards:
        tasks["cards"] = asyncio.gather(*[get_card_full_thumbnail(thumb) for thumb in d.event_cards])
    if d.event_attr_path:
        tasks["attr"] = get_img_from_path(ASSETS_BASE_DIR, d.event_attr_path)
    if d.event_unit_path:
        tasks["unit"] = get_img_from_path(ASSETS_BASE_DIR, d.event_unit_path)
    if d.event_chara_path:
        tasks["chara"] = get_img_from_path(ASSETS_BASE_DIR, d.event_chara_path)
    if not tasks:
        return {}
    keys = list(tasks.keys())
    values = await asyncio.gather(*tasks.values())
    return dict(zip(keys, values))


async def _compose_event_list_entry_image(
    d,
    loaded: dict[str, object],
    phase: str,
    style1: TextStyle,
    style2: TextStyle,
):
    bg = roundrect_bg(_resolve_event_list_entry_bg_color(phase), 5, alpha=180)

    with Canvas().set_padding(0) as canvas:
        with HSplit().set_padding(4).set_sep(4).set_item_align("lt").set_content_align("lt").set_bg(bg):
            with VSplit().set_padding(0).set_sep(2).set_item_align("lt").set_content_align("lt"):
                banner = loaded.get("banner")
                if banner is not None:
                    ImageBox(banner, size=(None, 40))
                with Grid(col_count=3).set_padding(0).set_sep(1, 1):
                    card_thumbs = loaded.get("cards", [])
                    if card_thumbs:
                        for thumb in card_thumbs:
                            ImageBox(thumb, size=(30, 30))
                if not d.event_cards:
                    Spacer(h=60)
                if d.event_cards and len(d.event_cards) <= 3:
                    Spacer(h=29)
            with VSplit().set_padding(0).set_sep(2).set_item_align("lt").set_content_align("lt"):
                TextBox(f"{d.event_name}", style1, line_count=2, use_real_line_count=False).set_w(100)
                TextBox(f"ID: {d.id} {d.event_type_name}", style2)
                TextBox(f"S {d.start_at.strftime('%Y-%m-%d %H:%M')}", style2)
                TextBox(f"T {d.end_at.strftime('%Y-%m-%d %H:%M')}", style2)
                with HSplit().set_padding(0).set_sep(4):
                    if loaded.get("attr") is not None:
                        ImageBox(loaded["attr"], size=(None, 24))
                    if loaded.get("unit") is not None:
                        ImageBox(loaded["unit"], size=(None, 24))
                    if loaded.get("chara") is not None:
                        ImageBox(loaded["chara"], size=(None, 24))
                    if not (d.event_attr_path or d.event_unit_path or d.event_chara_path):
                        Spacer(w=24, h=24)

    return await canvas.get_img()


async def _get_event_list_entry_image(d, now, style1: TextStyle, style2: TextStyle) -> Image.Image:
    phase = _resolve_event_list_entry_phase(d.start_at, d.end_at, now)
    cache_key = _build_event_list_entry_cache_key(d, phase)

    cached = get_composed_image_cached(cache_key)
    if cached is not None:
        _perf_logger.info("event/list entry memory hit: id=%s phase=%s", d.id, phase)
        return cached

    disk_cached = get_composed_image_disk_cached(_EVENT_LIST_ENTRY_CACHE_NAMESPACE, cache_key)
    if disk_cached is not None:
        put_composed_image_cache(cache_key, disk_cached)
        _perf_logger.info("event/list entry disk hit: id=%s phase=%s", d.id, phase)
        return disk_cached

    loaded = await _preload_event_entry_assets(d)
    image = await _compose_event_list_entry_image(d, loaded, phase, style1, style2)
    put_composed_image_cache(cache_key, image)
    put_composed_image_disk_cache(_EVENT_LIST_ENTRY_CACHE_NAMESPACE, cache_key, image)
    _perf_logger.info(
        "event/list entry miss: id=%s phase=%s size=%dx%d",
        d.id,
        phase,
        image.width,
        image.height,
    )
    return image


# 合成活动列表图片
async def compose_event_list_image(rqd: EventListRequest) -> Image.Image:
    event_list = rqd.event_info

    row_count = math.ceil(math.sqrt(len(event_list)))
    style1 = TextStyle(font=DEFAULT_HEAVY_FONT, size=10, color=(50, 50, 50))
    style2 = TextStyle(font=DEFAULT_FONT, size=10, color=(70, 70, 70))
    now = request_now(rqd.timezone)
    _t0 = time.perf_counter()
    entry_images = (
        await asyncio.gather(*[_get_event_list_entry_image(d, now, style1, style2) for d in event_list])
        if event_list
        else []
    )
    _t_entries = time.perf_counter() - _t0

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_padding(0).set_sep(4).set_content_align("lt").set_item_align("lt"):
            TextBox(
                "活动按时间顺序排列，黄色为当期活动，灰色为过去活动",
                TextStyle(font=DEFAULT_FONT, size=12, color=(0, 0, 100)),
            ).set_bg(roundrect_bg(radius=4, alpha=80)).set_padding(4)
            with Grid(row_count=row_count, vertical=True).set_sep(6, 6).set_item_align("lt").set_content_align("lt"):
                for entry_image in entry_images:
                    ImageBox(entry_image)

    add_request_watermark(canvas, rqd)
    _t0 = time.perf_counter()
    image = await canvas.get_img()
    _t_render = time.perf_counter() - _t0
    _perf_logger.info(
        "event/list total: entries=%.3fs render=%.3fs events=%d image=%dx%d",
        _t_entries,
        _t_render,
        len(event_list),
        image.width,
        image.height,
    )
    return image
