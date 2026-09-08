import asyncio
from dataclasses import dataclass
from datetime import timedelta
import logging
import math
import time

from PIL import Image

from src.core.image_payload import EncodedImagePayload
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
    collect_asset_signatures,
    get_asset_image_ref,
    get_composed_image_cached,
    get_composed_image_disk_cached,
    get_readable_timedelta,
    put_composed_image_cache,
    put_composed_image_disk_cache,
)
from src.sekai.deck.drawer import compose_deck_recommend_image, try_render_deck_recommend_payload
from src.sekai.deck.model import (
    DeckCardData,
    DeckData,
    DeckPlannerBoostRow,
    DeckPlannerInfo,
    DeckPlannerSong,
    DeckRequest,
)
from src.sekai.profile.drawer import (
    CardFullThumbnailBox,
    CardFullThumbnailLayers,
    get_card_full_thumbnail_layers,
    get_profile_card,
)
from src.sekai.skia_renderer.canvas import render_canvas_payload, skia_plot_enabled
from src.settings import ASSETS_BASE_DIR

logger = logging.getLogger(__name__)
_perf_logger = logging.getLogger("event.draw.perf")
_EVENT_LIST_ENTRY_CACHE_NAMESPACE = "event_list_entry"
_EVENT_LIST_CARD_SIZE = 30
_EVENT_LIST_CARD_ID_WIDTH = 40
_EVENT_LIST_CARD_ID_HEIGHT = 10
_EVENT_LIST_CARD_ROW_HEIGHT = _EVENT_LIST_CARD_SIZE + _EVENT_LIST_CARD_ID_HEIGHT
_EVENT_LIST_CARD_GRID_HEIGHT = _EVENT_LIST_CARD_ROW_HEIGHT * 2 + 1
_DEFAULT_WL_CHAPTER_COLOR = (75, 75, 75, 255)
_WL_PROGRESS_BORDER_COLOR = (75, 75, 75, 255)


@dataclass(frozen=True)
class _EventDetailStyles:
    label: TextStyle
    text: TextStyle
    chapter_label: TextStyle
    chapter_time: TextStyle
    current_chapter_badge: TextStyle


# 从 model.py 导入数据模型
from .model import (
    EventDetailRequest,
    # 兼容性别名
    EventHistoryInfo,
    EventListRequest,
    EventPlannerRequest,
    EventRecordRequest,
)


def _dict_value(data: dict, *keys: str):
    for key in keys:
        value = data.get(key)
        if value is not None:
            return value
    return None


def _dict_int(data: dict, *keys: str) -> int | None:
    value = _dict_value(data, *keys)
    if value is None:
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _dict_str(data: dict, *keys: str) -> str | None:
    value = _dict_value(data, *keys)
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _resolve_wl_chapter_color(chapter: dict, chapter_index: int) -> tuple[int, int, int, int]:
    chara_id = _dict_int(chapter, "game_character_id", "character_id")
    color_code = _dict_str(chapter, "color_code", "character_color_code")
    if color_code is None and chara_id is not None:
        color_code = CHARACTER_COLOR_CODE.get(chara_id)
    if color_code is None:
        color_code = CHARACTER_COLOR_CODE.get(chapter_index)
    if color_code is None:
        return _DEFAULT_WL_CHAPTER_COLOR
    try:
        return color_code_to_rgb(color_code)
    except ValueError:
        return _DEFAULT_WL_CHAPTER_COLOR


def _normalize_wl_chapters(chapters: list[dict] | None, timezone: str | None) -> list[dict]:
    normalized = []
    for index, chapter in enumerate(chapters or [], start=1):
        if not isinstance(chapter, dict):
            continue
        start_time = datetime_from_millis(_dict_value(chapter, "chapter_start_at", "start_at"), timezone)
        aggregate_time = datetime_from_millis(
            _dict_value(chapter, "chapter_aggregate_at", "aggregate_at"),
            timezone,
        )
        end_raw = _dict_value(chapter, "chapter_end_at", "end_at")
        end_time = datetime_from_millis(end_raw, timezone) if end_raw is not None else None
        if end_time is None and aggregate_time is not None:
            end_time = aggregate_time + timedelta(seconds=1)
        if start_time is None or end_time is None or end_time <= start_time:
            continue

        item = dict(chapter)
        item["start_time"] = start_time
        item["end_time"] = end_time
        item["chapter_no"] = _dict_int(chapter, "chapter_no", "chapter_id") or index
        item["game_character_id"] = _dict_int(chapter, "game_character_id", "character_id")
        item["character_name"] = _dict_str(chapter, "character_name", "chara_name")
        item["character_icon_path"] = _dict_str(chapter, "character_icon_path", "chara_icon_path", "icon_path")
        item["chapter_label"] = (
            f"{item['character_name']} 章节" if item["character_name"] else f"{item['chapter_no']}章"
        )
        item["color"] = _resolve_wl_chapter_color(chapter, index)
        normalized.append(item)

    normalized.sort(key=lambda item: item["start_time"])
    return normalized


def _clamp(value: float, low: float = 0.0, high: float = 1.0) -> float:
    return min(max(value, low), high)


def _is_wl_chapter_current(chapter: dict, now) -> bool:
    return chapter["start_time"] <= now <= chapter["end_time"]


def _wl_chapter_progress_segments(
    wl_chapters: list[dict],
    start_time,
    end_time,
    now,
) -> list[tuple[float, float, tuple]]:
    event_duration = end_time - start_time
    if event_duration.total_seconds() <= 0:
        return []

    visible_until = min(max(now, start_time), end_time)
    segments = []
    for chapter in wl_chapters:
        visible_end_time = min(chapter["end_time"], visible_until)
        if visible_end_time <= chapter["start_time"]:
            continue
        segment_start = _clamp((chapter["start_time"] - start_time) / event_duration)
        segment_end = _clamp((visible_end_time - start_time) / event_duration)
        if segment_end <= segment_start:
            continue
        segments.append((segment_start, segment_end, chapter["color"]))
    return segments


async def _load_event_detail_card_layers(rqd: EventDetailRequest) -> list[CardFullThumbnailLayers]:
    started_at = time.perf_counter()
    card_layers = list(await asyncio.gather(*(get_card_full_thumbnail_layers(card) for card in rqd.event_cards)))
    logger.debug(
        "[perf] compose_event_detail_image card thumbs %d: %.3fs",
        len(rqd.event_cards),
        time.perf_counter() - started_at,
    )
    return card_layers


def _event_detail_image_tasks(rqd: EventDetailRequest, wl_chapters: list[dict], use_story_bg: bool) -> dict:
    tasks = {}
    bg_path = rqd.event_assets.event_story_bg_path if use_story_bg else rqd.event_assets.event_bg_path
    # ImageBg 的 fade/blur 已由共享 Painter 原语表达；保持 ref，Skia 直接解码路径。
    tasks["bg"] = get_asset_image_ref(ASSETS_BASE_DIR, bg_path)
    event_chara_path = (rqd.event_assets.event_ban_chara_img or "").strip()
    if use_story_bg and event_chara_path:
        tasks["chara"] = get_asset_image_ref(ASSETS_BASE_DIR, event_chara_path)
    tasks["logo"] = get_asset_image_ref(ASSETS_BASE_DIR, rqd.event_assets.event_logo_path)
    if rqd.event_assets.ban_chara_icon_path:
        tasks["ban_icon"] = get_asset_image_ref(ASSETS_BASE_DIR, rqd.event_assets.ban_chara_icon_path)
    if rqd.event_assets.event_attr_image_path:
        tasks["attr"] = get_asset_image_ref(ASSETS_BASE_DIR, rqd.event_assets.event_attr_image_path)
    if rqd.event_assets.bonus_chara_path:
        for index, chara_path in enumerate(rqd.event_assets.bonus_chara_path):
            tasks[f"bonus_chara_{index}"] = get_asset_image_ref(ASSETS_BASE_DIR, chara_path)
    for index, chapter in enumerate(wl_chapters):
        if chapter.get("character_icon_path"):
            key = f"wl_chapter_icon_{index}"
            chapter["character_icon_key"] = key
            tasks[key] = get_asset_image_ref(ASSETS_BASE_DIR, chapter["character_icon_path"])
    return tasks


async def _load_event_detail_images(
    rqd: EventDetailRequest,
    wl_chapters: list[dict],
    use_story_bg: bool,
) -> dict:
    tasks = _event_detail_image_tasks(rqd, wl_chapters, use_story_bg)
    keys = list(tasks)
    started_at = time.perf_counter()
    images = dict(zip(keys, await asyncio.gather(*tasks.values())))
    logger.debug(
        "[perf] compose_event_detail_image event images %d: %.3fs",
        len(keys),
        time.perf_counter() - started_at,
    )
    return images


def _event_status_text(start_time, end_time, now) -> str:
    if start_time <= now <= end_time:
        return f"距结束还有{get_readable_timedelta(end_time - now)}"
    if now > end_time:
        return "活动已结束"
    return f"距开始还有{get_readable_timedelta(start_time - now)}"


def _current_wl_chapter(wl_chapters: list[dict], now) -> dict | None:
    return next((chapter for chapter in wl_chapters if _is_wl_chapter_current(chapter, now)), None)


def _draw_event_identity(
    rqd: EventDetailRequest,
    images: dict,
    styles: _EventDetailStyles,
) -> None:
    detail = rqd.event_info
    with VSplit().set_padding(16).set_sep(12).set_item_align("l").set_content_align("l"):
        with HSplit().set_padding(0).set_sep(8).set_item_align("l").set_content_align("l"):
            TextBox(rqd.region.upper(), styles.label)
            TextBox(f"{detail.id}", styles.text)
            Spacer(w=8)
            TextBox("类型", styles.label)
            TextBox(f"{detail.event_type_name}", styles.text)
            if detail.banner_cid:
                Spacer(w=8)
                if (ban_chara_icon := images.get("ban_icon")) is not None:
                    ImageBox(ban_chara_icon, size=(30, 30))
                TextBox(f"{detail.banner_index}箱", styles.label)


def _draw_wl_chapter_row(
    chapter: dict,
    current_chapter: dict | None,
    images: dict,
    styles: _EventDetailStyles,
) -> None:
    with HSplit().set_padding(0).set_sep(6).set_item_align("c").set_content_align("l"):
        icon = images.get(chapter.get("character_icon_key"))
        if icon is not None:
            ImageBox(icon, size=(22, 22), image_size_mode="fill")
        else:
            Spacer(w=22, h=22).set_bg(RoundRectBg(chapter["color"], 11))
        is_current = chapter is current_chapter
        with VSplit().set_padding(0).set_sep(0).set_item_align("l").set_content_align("l"):
            with HSplit().set_padding(0).set_sep(4).set_item_align("c").set_content_align("l"):
                label_box = TextBox(
                    chapter["chapter_label"],
                    styles.chapter_label,
                    overflow="shrink",
                    wrap=False,
                )
                if not is_current:
                    label_box.set_w(260)
                if is_current:
                    TextBox(
                        "当前",
                        styles.current_chapter_badge,
                        overflow="shrink",
                        wrap=False,
                    ).set_padding((5, 1)).set_bg(RoundRectBg((255, 231, 105, 255), 4))
            TextBox(
                f"{chapter['start_time'].strftime('%m-%d %H:%M')} ~ {chapter['end_time'].strftime('%m-%d %H:%M')}",
                styles.chapter_time,
                overflow="shrink",
                wrap=False,
            ).set_w(260)


def _draw_wl_chapters(
    wl_chapters: list[dict],
    current_chapter: dict | None,
    images: dict,
    styles: _EventDetailStyles,
) -> None:
    if not wl_chapters:
        return
    with VSplit().set_padding(0).set_sep(5).set_item_align("l").set_content_align("l"):
        for chapter in wl_chapters:
            _draw_wl_chapter_row(
                chapter,
                current_chapter,
                images,
                styles,
            )


def _draw_event_progress(detail, wl_chapters: list[dict], now) -> None:
    start_time = detail.start_at
    end_time = detail.end_at
    event_duration = end_time - start_time
    progress = _clamp((now - start_time) / event_duration) if event_duration.total_seconds() > 0 else 0
    progress_w, progress_h, border = 360, 8, 1
    with Frame().set_padding(8).set_content_align("lt"):
        Spacer(w=progress_w + border * 2, h=progress_h + border * 2).set_bg(RoundRectBg(_WL_PROGRESS_BORDER_COLOR, 4))
        if detail.event_type == "world_bloom" and wl_chapters and event_duration.total_seconds() > 0:
            for segment_start, segment_end, color in _wl_chapter_progress_segments(
                wl_chapters,
                start_time,
                end_time,
                now,
            ):
                segment_x = round(progress_w * segment_start)
                segment_end_x = round(progress_w * segment_end)
                segment_width = max(1, segment_end_x - segment_x)
                Spacer(w=segment_width, h=progress_h).set_bg(RoundRectBg(color, 0)).set_offset(
                    (border + segment_x, border)
                )
        else:
            Spacer(w=int(progress_w * progress), h=progress_h).set_bg(RoundRectBg((255, 255, 255, 255), 4)).set_offset(
                (border, border)
            )


def _draw_event_timing(
    detail,
    now,
    wl_chapters: list[dict],
    images: dict,
    styles: _EventDetailStyles,
) -> None:
    with VSplit().set_padding(16).set_sep(12).set_item_align("c").set_content_align("c"):
        with HSplit().set_padding(0).set_sep(8).set_item_align("lb").set_content_align("lb"):
            TextBox("开始时间", styles.label)
            TextBox(detail.start_at.strftime("%Y-%m-%d %H:%M:%S"), styles.text)
        with HSplit().set_padding(0).set_sep(8).set_item_align("lb").set_content_align("lb"):
            TextBox("结束时间", styles.label)
            TextBox(detail.end_at.strftime("%Y-%m-%d %H:%M:%S"), styles.text)
        with HSplit().set_padding(0).set_sep(8).set_item_align("lb").set_content_align("lb"):
            TextBox(_event_status_text(detail.start_at, detail.end_at, now), styles.text)

        if detail.event_type == "world_bloom":
            current_chapter = _current_wl_chapter(wl_chapters, now)
            if current_chapter:
                TextBox(
                    f"距章节结束还有{get_readable_timedelta(current_chapter['end_time'] - now)}",
                    styles.text,
                )
            _draw_wl_chapters(
                wl_chapters,
                current_chapter,
                images,
                styles,
            )
        _draw_event_progress(detail, wl_chapters, now)


def _event_card_column_count(card_count: int) -> int:
    if card_count <= 4:
        return card_count
    return 3 if card_count <= 6 else 4


def _draw_event_cards(
    event_cards: list,
    card_layers: list[CardFullThumbnailLayers],
    styles: _EventDetailStyles,
) -> None:
    if not event_cards:
        return
    with HSplit().set_padding(16).set_sep(16).set_item_align("c").set_content_align("c"):
        TextBox("活动卡片", styles.label)
        visible_cards = event_cards[:8]
        with Grid(col_count=_event_card_column_count(len(visible_cards))).set_sep(4, 4):
            for card, layers in zip(visible_cards, card_layers):
                with VSplit().set_padding(0).set_sep(2).set_item_align("c").set_content_align("c"):
                    CardFullThumbnailBox(layers, size=(80, 80))
                    TextBox(
                        f"ID:{card.card_id}",
                        TextStyle(font=DEFAULT_FONT, size=16, color=(75, 75, 75)),
                        overflow="clip",
                    )


def _draw_event_bonus(rqd: EventDetailRequest, images: dict, styles: _EventDetailStyles) -> None:
    detail = rqd.event_info
    if not (detail.bonus_attr or detail.bonus_chara_id):
        return
    with HSplit().set_padding(16).set_sep(8).set_item_align("c").set_content_align("c"):
        if detail.bonus_attr:
            TextBox("加成属性", styles.label)
            ImageBox(images["attr"], size=(None, 40))
        if rqd.event_assets.bonus_chara_path:
            TextBox("加成角色", styles.label)
            bonus_chara_images = [
                images[f"bonus_chara_{index}"] for index in range(len(rqd.event_assets.bonus_chara_path))
            ]
            with Grid(col_count=5 if len(bonus_chara_images) < 20 else 7).set_sep(4, 4):
                for image in bonus_chara_images:
                    ImageBox(image, size=(None, 40))


def _draw_event_detail_panel(
    rqd: EventDetailRequest,
    now,
    wl_chapters: list[dict],
    images: dict,
    card_layers: list[CardFullThumbnailLayers],
) -> None:
    styles = _EventDetailStyles(
        label=TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(50, 50, 50)),
        text=TextStyle(font=DEFAULT_FONT, size=24, color=(70, 70, 70)),
        chapter_label=TextStyle(font=DEFAULT_BOLD_FONT, size=15, color=(50, 50, 50)),
        chapter_time=TextStyle(font=DEFAULT_FONT, size=13, color=(70, 70, 70)),
        current_chapter_badge=TextStyle(font=DEFAULT_BOLD_FONT, size=11, color=(70, 70, 70)),
    )
    with (
        VSplit()
        .set_padding(16)
        .set_sep(16)
        .set_item_align("t")
        .set_content_align("t")
        .set_item_bg(roundrect_bg(alpha=80))
    ):
        ImageBox(images["logo"], size=(None, 150)).set_omit_parent_bg(True)
        _draw_event_identity(rqd, images, styles)
        _draw_event_timing(
            rqd.event_info,
            now,
            wl_chapters,
            images,
            styles,
        )
        _draw_event_cards(rqd.event_cards, card_layers, styles)
        _draw_event_bonus(rqd, images, styles)


async def _build_event_detail_canvas(rqd: EventDetailRequest) -> Canvas:
    detail = rqd.event_info
    now = request_now(rqd.timezone)
    wl_chapters = _normalize_wl_chapters(detail.wl_time_list, rqd.timezone)
    use_story_bg = detail.event_type != "world_bloom"
    card_layers, images = await asyncio.gather(
        _load_event_detail_card_layers(rqd),
        _load_event_detail_images(rqd, wl_chapters, use_story_bg),
    )
    event_bg = images["bg"]
    h = 1024
    w = min(int(h * 1.6), event_bg.size[0] * h // event_bg.size[1] if event_bg else int(h * 1.6))
    bg = ImageBg(event_bg, blur=False) if event_bg else SEKAI_BLUE_BG
    with Canvas(bg=bg, w=w, h=h).set_padding(BG_PADDING).set_content_align("r") as canvas:
        with Frame().set_size((w - BG_PADDING * 2, h - BG_PADDING * 2)).set_content_align("lb").set_padding((64, 0)):
            if use_story_bg and (event_chara_img := images.get("chara")) is not None:
                ImageBox(event_chara_img, size=(None, int(h * 0.9)), use_alpha_blend=True).set_offset((0, BG_PADDING))
        _draw_event_detail_panel(rqd, now, wl_chapters, images, card_layers)

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_event_detail_image(rqd: EventDetailRequest) -> Image.Image:
    return await (await _build_event_detail_canvas(rqd)).get_img()


async def try_render_event_detail_payload(rqd: EventDetailRequest) -> EncodedImagePayload | None:
    # NOT payload-cached: the canvas bakes in a live countdown ("距结束还有…" / "距章节结束还有…")
    # and a now-derived progress bar, so any cached image would show a frozen remaining time.
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_event_detail_canvas(rqd), endpoint="event_detail")


def _event_record_sort_rank(item: EventHistoryInfo) -> int | float:
    if item.rank is not None:
        return item.rank
    if item.rank_tier is not None:
        return item.rank_tier
    rank_display = (item.rank_display or "").strip().upper()
    if rank_display.startswith("T") and rank_display[1:].isdigit():
        return int(rank_display[1:])
    return float("inf")


def _event_record_point(item: EventHistoryInfo) -> int:
    return item.event_point if item.event_point is not None else 0


def _event_record_rows(name: str, events: list[EventHistoryInfo]) -> tuple[str, bool, list[EventHistoryInfo]]:
    topk = 30
    has_rank = any(item.rank is not None or item.rank_display or item.rank_tier is not None for item in events)
    if has_rank:
        events.sort(key=lambda item: (_event_record_sort_rank(item), -_event_record_point(item)))
        return f"排名前{topk}的{name}记录", True, events[:topk]
    events.sort(key=lambda item: -_event_record_point(item))
    return f"活动点数前{topk}的{name}记录", False, events[:topk]


async def _draw_event_record_events_column(
    events: list[EventHistoryInfo],
    header_style: TextStyle,
    detail_style: TextStyle,
    header_height: int,
    row_sep: int,
    row_height: int,
) -> None:
    with VSplit().set_padding(0).set_sep(row_sep).set_item_align("c").set_content_align("c"):
        TextBox("活动", header_style).set_h(header_height).set_content_align("c")
        for item in events:
            with HSplit().set_padding(0).set_sep(4).set_item_align("l").set_content_align("l").set_h(row_height):
                if item.wl_chara_icon_path:
                    ImageBox(
                        await get_asset_image_ref(ASSETS_BASE_DIR, item.wl_chara_icon_path),
                        size=(None, row_height),
                    )
                ImageBox(await get_asset_image_ref(ASSETS_BASE_DIR, item.banner_path), size=(None, row_height))
                with VSplit().set_padding(0).set_sep(2).set_item_align("l").set_content_align("l"):
                    TextBox(f"【{item.id}】{item.event_name}", detail_style).set_w(150)
                    TextBox(f"S {item.start_at.strftime('%Y-%m-%d %H:%M')}", detail_style)
                    TextBox(f"T {item.end_at.strftime('%Y-%m-%d %H:%M')}", detail_style)


def _draw_event_record_rank_column(
    events: list[EventHistoryInfo],
    style: TextStyle,
    header_style: TextStyle,
    header_height: int,
    row_sep: int,
    row_height: int,
) -> None:
    with VSplit().set_padding(0).set_sep(row_sep).set_item_align("c").set_content_align("c"):
        TextBox("排名", header_style).set_h(header_height).set_content_align("c")
        for item in events:
            rank_text = f"#{item.rank}" if item.rank is not None else (item.rank_display or "-")
            TextBox(rank_text, style, overflow="clip").set_h(row_height).set_content_align("c")


def _draw_event_record_point_column(
    events: list[EventHistoryInfo],
    style: TextStyle,
    header_style: TextStyle,
    header_height: int,
    row_sep: int,
    row_height: int,
) -> None:
    with VSplit().set_padding(0).set_sep(row_sep).set_item_align("c").set_content_align("c"):
        TextBox("PT", header_style).set_h(header_height).set_content_align("c")
        for item in events:
            point_text = str(item.event_point) if item.event_point is not None else "-"
            TextBox(point_text, style, overflow="clip").set_h(row_height).set_content_align("c")


async def _draw_event_record_group(
    name: str,
    events: list[EventHistoryInfo],
    header_style: TextStyle,
    detail_style: TextStyle,
    value_style: TextStyle,
) -> None:
    title, has_rank, rows = _event_record_rows(name, events)
    with (
        VSplit().set_padding(16).set_sep(16).set_item_align("lt").set_content_align("lt").set_bg(roundrect_bg(alpha=80))
    ):
        TextBox(title, header_style)
        header_height, row_sep, row_height = 28, 40, 80
        with (
            HSplit()
            .set_padding(16)
            .set_sep(16)
            .set_item_align("lt")
            .set_content_align("lt")
            .set_bg(roundrect_bg(alpha=80))
        ):
            await _draw_event_record_events_column(rows, header_style, detail_style, header_height, row_sep, row_height)
            if has_rank:
                _draw_event_record_rank_column(rows, value_style, header_style, header_height, row_sep, row_height)
            _draw_event_record_point_column(rows, value_style, header_style, header_height, row_sep, row_height)


# 合成活动记录图片
async def _build_event_record_canvas(rqd: EventRecordRequest) -> Canvas:
    header_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(50, 50, 50))
    detail_style = TextStyle(font=DEFAULT_FONT, size=16, color=(70, 70, 70))
    value_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(70, 70, 70))
    note_style = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(50, 50, 50))

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16):
            await get_profile_card(rqd.user_info.to_profile_card_request())
            note = "每次上传时进行增量更新，未上传过的记录将会丢失"
            if rqd.rank_note:
                note = f"{note}\n{rqd.rank_note}"
            TextBox(note, note_style).set_bg(roundrect_bg(alpha=80)).set_padding(12)
            with HSplit().set_sep(16).set_item_align("lt").set_content_align("lt"):
                if rqd.event_info:
                    await _draw_event_record_group("活动", rqd.event_info, header_style, detail_style, value_style)
                if rqd.wl_event_info:
                    await _draw_event_record_group("WL单榜", rqd.wl_event_info, header_style, detail_style, value_style)

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_event_record_image(rqd: EventRecordRequest) -> Image.Image:
    return await (await _build_event_record_canvas(rqd)).get_img()


async def try_render_event_record_payload(rqd: EventRecordRequest) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_event_record_canvas(rqd), endpoint="event_record")


def _event_planner_info(rqd: EventPlannerRequest) -> DeckPlannerInfo:
    return DeckPlannerInfo(
        target_point=rqd.target_point,
        current_point=rqd.current_point,
        remaining_point=rqd.remaining_point,
        daily_point=rqd.daily_point,
        target_source=rqd.target_source,
        songs=[
            DeckPlannerSong(
                music_id=song.music_id,
                query=song.query,
                title=song.title,
                music_cover_path=song.music_cover_path,
                difficulty=song.difficulty,
                rows=[
                    DeckPlannerBoostRow(
                        boost=row.boost,
                        point_per_play=row.point_per_play,
                        plays=row.plays,
                        energy=row.energy,
                    )
                    for row in song.rows
                ],
            )
            for song in rqd.songs
        ],
        warnings=rqd.warnings,
    )


def _event_planner_fallback_deck_request(rqd: EventPlannerRequest) -> DeckRequest:
    if rqd.profile is None:
        raise ValueError("活动规划缺少组卡请求，且没有用户信息可用于回退绘制")

    deck = DeckData(
        card_data=[
            DeckCardData(
                card_thumbnail=card.card_thumbnail,
                chara_id=0,
                skill_level=card.skill_level or "",
                skill_rate=card.skill_rate or 0,
                event_bonus_rate=card.event_bonus_rate or 0,
            )
            for card in rqd.deck_cards or []
        ],
        score=0,
        event_bonus_rate=rqd.deck_event_bonus,
        total_power=rqd.deck_total_power,
        multi_live_score_up=rqd.deck_skill_up,
    )
    planner_title = (
        f"目标 {rqd.target_point:,}pt / 当前 {(rqd.current_point or 0):,}pt / 还需 {rqd.remaining_point:,}pt"
    )
    return DeckRequest(
        region=rqd.region,
        profile=rqd.profile,
        deck_data=[deck],
        event_name=rqd.event_name,
        music_title=planner_title,
        music_id=10000,
        event_banner_path=rqd.event_banner_path,
        is_max_deck=False,
        recommend_type="event",
        event_id=rqd.event_id,
        live_type="multi",
        live_name=rqd.live_name or "协力",
        multi_live_teammate_power=250000,
        multi_live_teammate_score_up=200,
        target="score",
        model_name=[""],
    )


def _build_event_planner_deck_request(rqd: EventPlannerRequest) -> DeckRequest:
    deck_request = (
        rqd.deck_request.model_copy(deep=True)
        if rqd.deck_request is not None
        else _event_planner_fallback_deck_request(rqd)
    )
    deck_request.event_planner = _event_planner_info(rqd)
    if not deck_request.event_banner_path and rqd.event_banner_path:
        deck_request.event_banner_path = rqd.event_banner_path
    if not deck_request.event_name and rqd.event_name:
        deck_request.event_name = rqd.event_name
    deck_request.music_title = None
    deck_request.music_id = None
    deck_request.music_diff = None
    deck_request.music_cover_path = None
    deck_request.target = deck_request.target or "score"
    deck_request.live_type = deck_request.live_type or "multi"
    deck_request.live_name = deck_request.live_name or rqd.live_name or "协力"
    deck_request.recommend_type = deck_request.recommend_type or "event"
    return deck_request


async def compose_event_planner_image(rqd: EventPlannerRequest) -> Image.Image:
    return await compose_deck_recommend_image(_build_event_planner_deck_request(rqd))


async def try_render_event_planner_payload(rqd: EventPlannerRequest) -> EncodedImagePayload | None:
    """Skia 路径：planner 委托 deck 渲染,直接走 deck 的 Skia 路径。

    ``endpoint`` 必须显式传入：deck 的默认名是 ``deck_recommend``,不传会把规划图的渲染
    记到组卡端点上。
    """
    if not skia_plot_enabled():
        return None
    return await try_render_deck_recommend_payload(
        _build_event_planner_deck_request(rqd),
        endpoint="event_planner",
    )


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
    return build_rendered_image_cache_key(
        "event_list_entry",
        request_payload,
        asset_signatures=collect_asset_signatures(ASSETS_BASE_DIR, request_payload),
    )


async def _preload_event_entry_assets(d) -> dict[str, object]:
    tasks = {}
    if d.event_banner_path:
        tasks["banner"] = get_asset_image_ref(ASSETS_BASE_DIR, d.event_banner_path)
    if d.event_cards:
        tasks["cards"] = asyncio.gather(*[get_card_full_thumbnail_layers(thumb) for thumb in d.event_cards])
    if d.event_attr_path:
        tasks["attr"] = get_asset_image_ref(ASSETS_BASE_DIR, d.event_attr_path)
    if d.event_unit_path:
        tasks["unit"] = get_asset_image_ref(ASSETS_BASE_DIR, d.event_unit_path)
    if d.event_chara_path:
        tasks["chara"] = get_asset_image_ref(ASSETS_BASE_DIR, d.event_chara_path)
    if not tasks:
        return {}
    keys = list(tasks.keys())
    values = await asyncio.gather(*tasks.values())
    return dict(zip(keys, values))


def _add_event_list_card_cell(layers: CardFullThumbnailLayers, card_id_style: TextStyle) -> None:
    with VSplit().set_padding(0).set_sep(0).set_content_align("c").set_item_align("c"):
        CardFullThumbnailBox(layers, size=(_EVENT_LIST_CARD_SIZE, _EVENT_LIST_CARD_SIZE))
        TextBox(f"#{layers.rqd.card_id}", card_id_style, overflow="shrink").set_padding(0).set_size(
            (_EVENT_LIST_CARD_ID_WIDTH, _EVENT_LIST_CARD_ID_HEIGHT)
        ).set_content_align("c")


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
                    card_layers = loaded.get("cards", [])
                    if card_layers:
                        for layers in card_layers:
                            _add_event_list_card_cell(layers, TextStyle(font=DEFAULT_FONT, size=7, color=(70, 70, 70)))
                if not d.event_cards:
                    Spacer(h=_EVENT_LIST_CARD_GRID_HEIGHT)
                if d.event_cards and len(d.event_cards) <= 3:
                    Spacer(h=_EVENT_LIST_CARD_GRID_HEIGHT - _EVENT_LIST_CARD_ROW_HEIGHT - 2)
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
async def _build_event_list_canvas(rqd: EventListRequest) -> Canvas:
    event_list = rqd.event_info

    row_count = max(1, math.ceil(math.sqrt(len(event_list))))
    style1 = TextStyle(font=DEFAULT_HEAVY_FONT, size=10, color=(50, 50, 50))
    style2 = TextStyle(font=DEFAULT_FONT, size=10, color=(70, 70, 70))
    now = request_now(rqd.timezone)
    entry_images = (
        await asyncio.gather(*[_get_event_list_entry_image(d, now, style1, style2) for d in event_list])
        if event_list
        else []
    )

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
    return canvas


async def compose_event_list_image(rqd: EventListRequest) -> Image.Image:
    return await (await _build_event_list_canvas(rqd)).get_img()


async def try_render_event_list_payload(rqd: EventListRequest) -> EncodedImagePayload | None:
    # NOT payload-cached at the canvas level, deliberately. `add_request_watermark` bakes a
    # second-resolution "DT: %Y-%m-%d %H:%M:%S" stamp into the image (from `rqd.dt`, or from
    # `request_now()` when the caller omits it), so a key that can ever hit — i.e. one that
    # drops the per-request `dt` — would serve a visibly stale timestamp: `event_info` is
    # stable for the whole event period, so the stale window is the 7d cache TTL, not seconds.
    # Keying on the full payload (dt included) is airtight but hits 0% of the time and would
    # just churn the shared payload LRU. The entry sub-images ARE cached, keyed by
    # (event, phase) in `_get_event_list_entry_image`, which is where the real cost sits.
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_event_list_canvas(rqd), endpoint="event_list")
