import asyncio
import logging
import math
import time

from PIL import Image

from src.core.image_payload import EncodedImagePayload
from src.sekai.base.draw import (
    BG_PADDING,
    SEKAI_BLUE_BG,
    add_request_watermark,
    roundrect_bg,
)
from src.sekai.base.painter import (
    BLACK,
    DEFAULT_BOLD_FONT,
    DEFAULT_FONT,
    DEFAULT_HEAVY_FONT,
)
from src.sekai.base.plot import Canvas, Grid, HSplit, ImageBg, ImageBox, Spacer, TextBox, TextStyle, VSplit
from src.sekai.base.timezone import datetime_from_millis, request_now
from src.sekai.base.utils import (
    ImageSource,
    concat_images,
    get_asset_image_ref,
    get_float_str,
    get_img_from_path,
    get_readable_timedelta,
)
from src.sekai.profile.drawer import CardFullThumbnailBox, CardFullThumbnailLayers, get_card_full_thumbnail_layers
from src.sekai.skia_renderer.canvas import render_canvas_payload, skia_plot_enabled
from src.settings import ASSETS_BASE_DIR, RESULT_ASSET_PATH

# 从 model.py 导入数据模型
from .model import (
    GachaBehavior,
    GachaBrief,
    GachaDetailRequest,
    GachaListRequest,
)

IMAGE_LOAD_EXCEPTIONS = (FileNotFoundError, OSError, ValueError)
logger = logging.getLogger(__name__)
GACHA_LIST_LOGO_BOX_SIZE = (130, 60)


async def get_unknown_fallback_image(path: str | None = None) -> Image.Image:
    """加载缺失图；优先按目标路径返回比例合适的 placeholder。"""
    if path:
        try:
            return await get_img_from_path(ASSETS_BASE_DIR, path, on_missing="placeholder")
        except IMAGE_LOAD_EXCEPTIONS:
            pass
    try:
        return await get_img_from_path(ASSETS_BASE_DIR, f"{RESULT_ASSET_PATH}/unknown.jpg")
    except IMAGE_LOAD_EXCEPTIONS:
        return Image.new("RGBA", (256, 256), (220, 220, 220, 255))


async def get_gacha_image_or_unknown(path: str | None, *, allow_empty: bool = False) -> Image.Image | None:
    """加载卡池图片，缺图时自动回退到 UnKnown 占位图。"""
    if path:
        try:
            return await get_img_from_path(ASSETS_BASE_DIR, path)
        except IMAGE_LOAD_EXCEPTIONS:
            return await get_unknown_fallback_image(path)
    if allow_empty:
        return None
    return await get_unknown_fallback_image()


async def get_gacha_image_ref_or_unknown(path: str | None, *, allow_empty: bool = False) -> ImageSource | None:
    """加载卡池图片（惰性引用），缺图时自动回退到 UnKnown 占位图。

    与 :func:`get_gacha_image_or_unknown` 语义一致，但只探测图片头部而不解码像素，
    仅供 ImageBox/ImageBg 等支持 ImageSource 的消费者使用。
    """
    if path:
        try:
            return await get_asset_image_ref(ASSETS_BASE_DIR, path)
        except IMAGE_LOAD_EXCEPTIONS:
            return await get_unknown_fallback_image(path)
    if allow_empty:
        return None
    return await get_unknown_fallback_image()


async def get_gacha_list_image_with_fallback(
    logo_path: str | None,
    banner_path: str | None,
) -> tuple[ImageSource, str]:
    """优先使用 logo，缺失时回退到 banner，再退回 unknown。

    注意 ref 只探测文件头：回退触发条件是"文件缺失/不是图片"，而非旧版的"像素解码失败"。
    头部合法但像素截断的坏文件不再回退到 banner，而是画成占位图(不抛错)。缺图——也就是这条
    回退链真正服务的场景——行为不变。
    """
    if logo_path:
        try:
            return await get_asset_image_ref(ASSETS_BASE_DIR, logo_path, on_missing="raise"), "logo"
        except IMAGE_LOAD_EXCEPTIONS:
            pass
    if banner_path:
        try:
            return await get_asset_image_ref(ASSETS_BASE_DIR, banner_path, on_missing="raise"), "banner"
        except IMAGE_LOAD_EXCEPTIONS:
            pass
    return await get_unknown_fallback_image(), "unknown"


async def get_rarity_img(
    rarity: str,
    rarity_img_path: str = f"{RESULT_ASSET_PATH}/card/rare_star_normal.png",
    birthday_img_path: str | None = f"{RESULT_ASSET_PATH}/card/rare_birthday.png",
) -> Image.Image | None:
    """获取稀有度图片"""
    if rarity == "rarity_birthday":
        rare_img = await get_gacha_image_or_unknown(birthday_img_path)
        rare_num = 1
    else:
        rare_img = await get_gacha_image_or_unknown(rarity_img_path)
        rare_num = int(rarity.split("_")[-1])

    if rare_img:
        return await concat_images([rare_img] * rare_num, "h")
    return None


# ======================= Constants ======================= #

GACHA_TYPE_NAMES = {
    "beginner": "新手",
    "normal": "一般",
    "ceil": "天井",
    "gift": "礼物",
}

# 保底行为类型映射
GACHA_BEHAVIOR_NAMES = {
    "normal": "普通",
    "over_rarity_3_once": "保底3星",
    "over_rarity_4_once": "保底4星",
}

GACHA_RATE_RARITIES = ["rarity_1", "rarity_2", "rarity_3", "rarity_4", "rarity_birthday"]

GACHA_RARE_NAMES = {
    "rarity_1": "1星",
    "rarity_2": "2星",
    "rarity_3": "3星",
    "rarity_4": "4星",
    "rarity_birthday": "生日",
    "pickup": "当期",
}

# ======================= Drawing Functions ======================= #


def _paginate_gacha_list(rqd: GachaListRequest) -> tuple[list[GachaBrief], int, int]:
    gachas = list(rqd.gachas)
    pre_paginated = rqd.pre_paginated or (rqd.current_page is not None and rqd.total_page is not None)
    if pre_paginated:
        total_pages = max(1, rqd.total_page or 1)
        page = rqd.current_page if rqd.current_page is not None else total_pages
        page = max(1, min(page, total_pages))
        return gachas, page, total_pages

    gachas.sort(key=lambda g: g.start_at)
    page_size = rqd.page_size if rqd.page_size else 20
    total_pages = max(1, math.ceil(len(gachas) / page_size))
    page = max(1, min(rqd.filter.page, total_pages)) if rqd.filter.page else total_pages
    start_index = (page - 1) * page_size
    return gachas[start_index : start_index + page_size], page, total_pages


async def _preload_gacha_list_images(
    rqd: GachaListRequest,
    gachas: list[GachaBrief],
) -> dict[int, tuple[ImageSource, str]]:
    image_inputs = [(rqd.gacha_logos.get(g.id), rqd.gacha_banners.get(g.id)) for g in gachas]
    started_at = time.perf_counter()
    results = (
        await asyncio.gather(
            *[get_gacha_list_image_with_fallback(logo_path, banner_path) for logo_path, banner_path in image_inputs]
        )
        if image_inputs
        else []
    )
    logger.debug(
        "[perf] compose_gacha_list_image preload %d list images: %.3fs",
        len(image_inputs),
        time.perf_counter() - started_at,
    )
    return {g.id: result for g, result in zip(gachas, results)}


async def _build_gacha_list_canvas(rqd: GachaListRequest) -> Canvas:
    """合成卡池一览图片"""
    gachas, page, total_pages = _paginate_gacha_list(rqd)

    row_count = max(1, math.ceil(math.sqrt(len(gachas))))
    style1 = TextStyle(font=DEFAULT_HEAVY_FONT, size=10, color=(50, 50, 50))
    style2 = TextStyle(font=DEFAULT_FONT, size=10, color=(70, 70, 70))

    # 预加载所有列表缩略图，优先 logo，缺失时回退 banner。
    list_image_cache = await _preload_gacha_list_images(rqd, gachas)

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_padding(0).set_sep(4).set_content_align("lt").set_item_align("lt"):
            TextBox(
                f"卡池按时间顺序排列，黄色为开放中卡池，当前为第 {page}/{total_pages} 页",
                TextStyle(font=DEFAULT_FONT, size=12, color=(0, 0, 100)),
                overflow="shrink",
            ).set_w(280).set_bg(roundrect_bg(radius=4, alpha=80)).set_padding(4)
            with Grid(row_count=row_count, vertical=True).set_sep(8, 2).set_item_align("c").set_content_align("c"):
                now = request_now(rqd.timezone)
                for g in gachas:
                    bg_color = (255, 255, 255, 200)
                    if g.start_at <= now <= g.end_at:
                        bg_color = (255, 250, 220, 200)
                    elif now > g.end_at:
                        bg_color = (220, 220, 220, 200)
                    bg = roundrect_bg(bg_color, 5)
                    with HSplit().set_padding(4).set_sep(4).set_item_align("lt").set_content_align("lt").set_bg(bg):
                        with VSplit().set_padding(0).set_sep(2).set_item_align("lt").set_content_align("lt"):
                            list_image_data = list_image_cache.get(g.id)
                            if list_image_data is None:
                                fallback_path = rqd.gacha_banners.get(g.id) or rqd.gacha_logos.get(g.id)
                                list_image_data = (await get_unknown_fallback_image(fallback_path), "unknown")
                            list_image, image_kind = list_image_data
                            if image_kind == "banner":
                                ImageBox(list_image, size=(160, 60), image_size_mode="fit").set_content_align("c")
                            else:
                                ImageBox(list_image, size=GACHA_LIST_LOGO_BOX_SIZE)
                            title_box = TextBox(
                                f"【{g.id}】{g.name}",
                                style1,
                                line_count=2,
                                use_real_line_count=False,
                            )
                            title_box.set_w(130)
                            TextBox(f"S {g.start_at.strftime('%Y-%m-%d %H:%M')}", style2)
                            TextBox(f"T {g.end_at.strftime('%Y-%m-%d %H:%M')}", style2)

    add_request_watermark(canvas, rqd)
    return canvas


async def compose_gacha_list_image(rqd: GachaListRequest) -> Image.Image:
    return await (await _build_gacha_list_canvas(rqd)).get_img()


async def try_render_gacha_list_payload(rqd: GachaListRequest) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_gacha_list_canvas(rqd), endpoint="gacha_list")


async def _gacha_detail_background(rqd: GachaDetailRequest):
    if not rqd.bg_img_path:
        return SEKAI_BLUE_BG
    bg_img = await get_gacha_image_ref_or_unknown(rqd.bg_img_path)
    return ImageBg(bg_img) if bg_img else SEKAI_BLUE_BG


def _gacha_detail_preload_items(rqd: GachaDetailRequest) -> tuple[list[str], list]:
    keys: list[str] = []
    coroutines = []

    def add(key: str, coroutine) -> None:
        keys.append(key)
        coroutines.append(coroutine)

    if rqd.logo_img_path:
        add("logo", get_gacha_image_ref_or_unknown(rqd.logo_img_path))
    if rqd.banner_img_path:
        add("banner", get_gacha_image_ref_or_unknown(rqd.banner_img_path))
    if rqd.gacha.ceil_item_img_path:
        add("ceil_item", get_gacha_image_ref_or_unknown(rqd.gacha.ceil_item_img_path))
    for behavior in rqd.gacha.behaviors:
        key = f"cost_{behavior.cost_icon_path}"
        if behavior.cost_type and behavior.cost_icon_path and key not in keys:
            add(key, get_gacha_image_ref_or_unknown(behavior.cost_icon_path))
    for index, card in enumerate(rqd.pickup_cards or []):
        add(f"card_{index}", get_card_full_thumbnail_layers(card.thumbnail_request))
    for rarity in GACHA_RATE_RARITIES:
        rate = getattr(rqd.weight_info, f"{rarity}_rate", 0.0)
        if not math.isclose(rate, 0.0, abs_tol=1.0e-12):
            add(f"rarity_{rarity}", get_rarity_img(rarity))
    return keys, coroutines


async def _preload_gacha_detail_assets(
    rqd: GachaDetailRequest,
) -> dict[str, ImageSource | CardFullThumbnailLayers | None]:
    keys, coroutines = _gacha_detail_preload_items(rqd)
    started_at = time.perf_counter()
    results = await asyncio.gather(*coroutines, return_exceptions=True) if coroutines else []
    logger.debug(
        "[perf] compose_gacha_detail_image preload %d items: %.3fs",
        len(coroutines),
        time.perf_counter() - started_at,
    )
    return {key: value if not isinstance(value, BaseException) else None for key, value in zip(keys, results)}


def _draw_gacha_detail_heading(
    rqd: GachaDetailRequest,
    assets: dict[str, ImageSource | CardFullThumbnailLayers | None],
    title_style: TextStyle,
    label_style: TextStyle,
    text_style: TextStyle,
    width: int,
) -> None:
    with HSplit().set_padding(8).set_sep(32).set_content_align("c").set_item_align("c").set_omit_parent_bg(True):
        if rqd.logo_img_path and (logo_img := assets.get("logo")):
            ImageBox(logo_img, size=(None, 100))
        if rqd.banner_img_path and (banner_img := assets.get("banner")):
            ImageBox(banner_img, size=(None, 100))

    TextBox(rqd.gacha.name, title_style, use_real_line_count=True).set_w(width).set_padding(16).set_content_align("c")
    with HSplit().set_padding(16).set_sep(8).set_content_align("c").set_item_align("c"):
        TextBox("ID", label_style)
        TextBox(f"{rqd.gacha.id} ({rqd.region.upper()})", text_style)
        Spacer(w=24)
        TextBox("类型", label_style)
        TextBox(GACHA_TYPE_NAMES.get(rqd.gacha.gacha_type, rqd.gacha.gacha_type), text_style)
        if rqd.gacha.ceil_item_img_path:
            Spacer(w=24)
            TextBox("交换物品", label_style)
            if ceil_item_img := assets.get("ceil_item"):
                ImageBox(ceil_item_img, size=(None, 30))


def _draw_gacha_detail_timing(rqd: GachaDetailRequest, label_style: TextStyle, text_style: TextStyle) -> None:
    start_time = datetime_from_millis(rqd.gacha.start_at, rqd.timezone)
    end_time = datetime_from_millis(rqd.gacha.end_at, rqd.timezone)
    now = request_now(rqd.timezone)
    with VSplit().set_padding(16).set_sep(8).set_content_align("c").set_item_align("c"):
        with HSplit().set_padding(0).set_sep(8).set_content_align("c").set_item_align("c"):
            TextBox("开始时间", label_style)
            TextBox(start_time.strftime("%Y-%m-%d %H:%M"), text_style)
        with HSplit().set_padding(0).set_sep(8).set_content_align("c").set_item_align("c"):
            TextBox("结束时间", label_style)
            TextBox(end_time.strftime("%Y-%m-%d %H:%M"), text_style)
        with HSplit().set_padding(0).set_sep(8).set_content_align("c").set_item_align("c"):
            if start_time >= now:
                TextBox("距离开始还有", label_style)
                TextBox(get_readable_timedelta(end_time - now), text_style)
            elif end_time >= now:
                TextBox("距离结束还有", label_style)
                TextBox(get_readable_timedelta(end_time - now), text_style)
            else:
                TextBox("卡池已结束", label_style)


def _gacha_behavior_label(behavior: GachaBehavior) -> str:
    text = GACHA_BEHAVIOR_NAMES.get(behavior.type, "未知")
    if behavior.type == "once_a_day":
        text = "每日"
    elif behavior.type == "once_a_week":
        text = "每周"
    if behavior.spin_count == 1:
        text += "/单抽"
    elif behavior.spin_count == 10:
        text += "/十连"
    if behavior.colorful_pass:
        text = "月卡" + text
    if behavior.execute_limit:
        text += f"(限{behavior.execute_limit}次)"
    return text


def _group_gacha_behaviors(behaviors: list[GachaBehavior]) -> dict[str, list[GachaBehavior]]:
    grouped: dict[str, list[GachaBehavior]] = {}
    for behavior in behaviors:
        grouped.setdefault(_gacha_behavior_label(behavior), []).append(behavior)
    return grouped


def _draw_gacha_behavior_cost(
    behavior: GachaBehavior,
    assets: dict[str, ImageSource | CardFullThumbnailLayers | None],
    text_style: TextStyle,
) -> None:
    if not behavior.cost_type:
        TextBox("免费", text_style)
        return
    if behavior.cost_icon_path and (cost_icon := assets.get(f"cost_{behavior.cost_icon_path}")):
        ImageBox(cost_icon, size=(None, 48))
    if "paid" in behavior.cost_type:
        TextBox("(付费)", text_style)
    if behavior.cost_quantity and behavior.cost_quantity > 1:
        TextBox(f"x{behavior.cost_quantity}", text_style)


def _draw_gacha_detail_behaviors(
    rqd: GachaDetailRequest,
    assets: dict[str, ImageSource | CardFullThumbnailLayers | None],
    label_style: TextStyle,
    text_style: TextStyle,
) -> None:
    with VSplit().set_padding(16).set_sep(16).set_content_align("c").set_item_align("c"):
        with Grid(col_count=2).set_padding(0).set_sep(8, 8).set_content_align("l").set_item_align("l"):
            for text, behaviors in _group_gacha_behaviors(rqd.gacha.behaviors).items():
                TextBox(text, label_style)
                with HSplit().set_padding(0).set_sep(8).set_content_align("l").set_item_align("l"):
                    for index, behavior in enumerate(behaviors):
                        if index > 0:
                            TextBox(" / ", text_style)
                        _draw_gacha_behavior_cost(behavior, assets, text_style)


async def _draw_gacha_detail_pickups(
    rqd: GachaDetailRequest,
    assets: dict[str, ImageSource | CardFullThumbnailLayers | None],
    label_style: TextStyle,
    small_style: TextStyle,
) -> None:
    if not rqd.pickup_cards:
        return
    with HSplit().set_padding(16).set_sep(16).set_content_align("c").set_item_align("c"):
        TextBox("当期卡片", label_style)
        with (
            Grid(col_count=min(5, len(rqd.pickup_cards)))
            .set_padding(0)
            .set_sep(8, 8)
            .set_content_align("c")
            .set_item_align("c")
        ):
            card_size = 80
            for index, card in enumerate(rqd.pickup_cards):
                with VSplit().set_padding(0).set_sep(1).set_content_align("c").set_item_align("c"):
                    card_layers = assets.get(f"card_{index}")
                    if card_layers is not None:
                        CardFullThumbnailBox(card_layers, size=(card_size, card_size), shadow=True)
                    else:
                        ImageBox(await get_unknown_fallback_image(), size=(card_size, card_size), shadow=True)
                    TextBox(f"{card.id} ({get_float_str(card.rate * 100, 4)}%)", small_style)


def _rate_text(rate: float, guaranteed_rate: float) -> str:
    normal_text = f"{get_float_str(rate * 100, 4)}%"
    if guaranteed_rate <= 0:
        return normal_text
    guaranteed_text = f"{get_float_str(guaranteed_rate * 100, 4)}%"
    return f"{normal_text} / {guaranteed_text} (保底)"


def _pickup_rate_text(rqd: GachaDetailRequest) -> str:
    pickup_total_rate = sum(card.rate for card in rqd.pickup_cards or [])
    guaranteed_rate = 0.0
    guaranteed_4star_rate = rqd.weight_info.guaranteed_rates.get("rarity_4", 0.0)
    normal_4star_rate = rqd.weight_info.rarity_4_rate
    if guaranteed_4star_rate > 0 and pickup_total_rate > 0 and normal_4star_rate > 0:
        guaranteed_rate = guaranteed_4star_rate * (pickup_total_rate / normal_4star_rate)
    return _rate_text(pickup_total_rate, guaranteed_rate)


def _draw_gacha_rarity_label(
    rarity: str,
    count: int,
    assets: dict[str, ImageSource | CardFullThumbnailLayers | None],
    label_style: TextStyle,
    text_style: TextStyle,
) -> None:
    rarity_name = GACHA_RARE_NAMES.get(rarity, rarity.replace("rarity_", ""))
    with HSplit().set_padding(0).set_sep(8).set_content_align("l").set_item_align("l"):
        if rarity_img := assets.get(f"rarity_{rarity}"):
            ImageBox(rarity_img, size=(None, 24))
        else:
            TextBox(rarity_name, label_style)
        if count > 0:
            TextBox(f"({count})", text_style)


def _draw_gacha_detail_rates(
    rqd: GachaDetailRequest,
    assets: dict[str, ImageSource | CardFullThumbnailLayers | None],
    label_style: TextStyle,
    text_style: TextStyle,
) -> None:
    with VSplit().set_padding(16).set_sep(8).set_content_align("c").set_item_align("c"):
        with Grid(col_count=2).set_padding(0).set_sep(8, 8).set_content_align("l").set_item_align("l"):
            if rqd.pickup_cards:
                with HSplit().set_padding(0).set_sep(8).set_content_align("l").set_item_align("l"):
                    TextBox("当期", label_style)
                    TextBox(f"({len(rqd.pickup_cards)})", text_style)
                TextBox(_pickup_rate_text(rqd), text_style)
            for rarity in GACHA_RATE_RARITIES:
                rate = getattr(rqd.weight_info, f"{rarity}_rate", 0.0)
                if math.isclose(rate, 0.0, abs_tol=1.0e-12):
                    continue
                count = getattr(rqd.gacha, f"{rarity}_count", 0)
                _draw_gacha_rarity_label(rarity, count, assets, label_style, text_style)
                guaranteed_rate = rqd.weight_info.guaranteed_rates.get(rarity, 0.0)
                TextBox(_rate_text(rate, guaranteed_rate), text_style)


async def _draw_gacha_detail_content(
    rqd: GachaDetailRequest,
    assets: dict[str, ImageSource | CardFullThumbnailLayers | None],
) -> None:
    width = 600
    title_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK)
    label_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(50, 50, 50))
    text_style = TextStyle(font=DEFAULT_FONT, size=24, color=(70, 70, 70))
    small_style = TextStyle(font=DEFAULT_FONT, size=12, color=(70, 70, 70))
    with (
        VSplit()
        .set_padding(8)
        .set_sep(8)
        .set_content_align("c")
        .set_item_align("c")
        .set_item_bg(roundrect_bg(alpha=80))
        .set_bg(roundrect_bg(alpha=80))
    ):
        _draw_gacha_detail_heading(rqd, assets, title_style, label_style, text_style, width)
        _draw_gacha_detail_timing(rqd, label_style, text_style)
        _draw_gacha_detail_behaviors(rqd, assets, label_style, text_style)
        await _draw_gacha_detail_pickups(rqd, assets, label_style, small_style)
        _draw_gacha_detail_rates(rqd, assets, label_style, text_style)


async def _build_gacha_detail_canvas(rqd: GachaDetailRequest) -> Canvas:
    """合成卡池详情图片。"""
    background, assets = await asyncio.gather(
        _gacha_detail_background(rqd),
        _preload_gacha_detail_assets(rqd),
    )
    with Canvas(bg=background).set_padding(BG_PADDING) as canvas:
        with HSplit().set_sep(16).set_content_align("lt").set_item_align("lt"):
            await _draw_gacha_detail_content(rqd, assets)
    add_request_watermark(canvas, rqd)
    return canvas


async def compose_gacha_detail_image(rqd: GachaDetailRequest) -> Image.Image:
    return await (await _build_gacha_detail_canvas(rqd)).get_img()


async def try_render_gacha_detail_payload(rqd: GachaDetailRequest) -> EncodedImagePayload | None:
    if not skia_plot_enabled():
        return None
    return await render_canvas_payload(await _build_gacha_detail_canvas(rqd), endpoint="gacha_detail")
