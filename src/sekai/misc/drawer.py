import asyncio
import logging
import time

from PIL import Image

from src.sekai.base.draw import (
    BG_PADDING,
    Canvas,
    CHARACTER_COLOR_CODE,
    SEKAI_BLUE_BG,
    TextBox,
    add_request_watermark,
    roundrect_bg,
)
from src.sekai.base.painter import ADAPTIVE_WB, WHITE, color_code_to_rgb
from src.sekai.base.plot import (
    Flow,
    Grid,
    HSplit,
    ImageBg,
    ImageBox,
    RoundRectBg,
    Spacer,
    TextStyle,
    VSplit,
)
from src.sekai.base.timezone import datetime_from_millis
from src.sekai.base.utils import (
    build_rendered_image_cache_key,
    get_composed_image_cached,
    get_composed_image_disk_cached,
    get_image_asset_signature,
    get_img_from_path,
    get_img_resized,
    get_str_display_length,
    get_readable_datetime,
    put_composed_image_cache,
    put_composed_image_disk_cache,
)
from src.settings import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT, DEFAULT_FONT, DEFAULT_HEAVY_FONT

# =========================== 从.model导入数据类型 =========================== #
from .model import AliasListRequest, BirthdayEventTime, CharaBirthdayRequest

logger = logging.getLogger(__name__)

# =========================== 颜色常量 =========================== #

BLACK = (0, 0, 0, 255)
_ALIAS_LIST_CACHE_NAMESPACE = "alias_list"


def _with_alpha(color: tuple[int, int, int], alpha: int) -> tuple[int, int, int, int]:
    return (color[0], color[1], color[2], alpha)


def _resolve_alias_accent(entity_label: str, entity_id: int) -> tuple[int, int, int]:
    if "角色" in entity_label:
        if color_code := CHARACTER_COLOR_CODE.get(entity_id):
            return tuple(color_code_to_rgb(color_code))
        return (255, 204, 170)
    return (110, 180, 255)


def _build_alias_list_cache_key(rqd: AliasListRequest) -> str:
    request_payload = {
        "title": rqd.title,
        "entity_label": rqd.entity_label,
        "entity_id": rqd.entity_id,
        "entity_name": rqd.entity_name,
        "music_jacket_path": rqd.music_jacket_path,
        "aliases": [alias.strip() for alias in rqd.aliases if alias and alias.strip()],
    }
    return build_rendered_image_cache_key(
        _ALIAS_LIST_CACHE_NAMESPACE,
        request_payload,
        extra={"version": 4},
        asset_signatures={
            "music_jacket": get_image_asset_signature(ASSETS_BASE_DIR, rqd.music_jacket_path),
        },
    )


def _resolve_alias_name_box_width(name: str, has_jacket: bool) -> int:
    display_len = max(1, get_str_display_length(name.strip()))
    estimated = 112 + display_len * 14
    if has_jacket:
        return max(240, min(648, estimated))
    return max(280, min(760, estimated))


async def compose_chara_birthday_image(rqd: CharaBirthdayRequest) -> Image.Image:
    r"""compose_chara_birthday_image

    合成角色生日图片

    Args
    ----
    rqd : CharaBirthdayRequest
        绘制角色生日图片所必须的数据

    Returns
    -------
    PIL.Image.Image
    """
    cid = rqd.cid
    month = rqd.month
    day = rqd.day
    region_name = rqd.region_name
    days_until_birthday = rqd.days_until_birthday
    color_code = rqd.color_code
    cards = rqd.cards
    all_characters = rqd.all_characters

    is_fifth_anniv = rqd.is_fifth_anniv

    style1 = TextStyle(DEFAULT_BOLD_FONT, 24, BLACK)
    style2 = TextStyle(DEFAULT_FONT, 20, BLACK)

    # 加载图片（并行）
    _img_tasks = [
        get_img_from_path(ASSETS_BASE_DIR, rqd.card_image_path),
        get_img_from_path(ASSETS_BASE_DIR, rqd.sd_image_path),
        get_img_from_path(ASSETS_BASE_DIR, rqd.title_image_path),
        *[get_img_from_path(ASSETS_BASE_DIR, card.thumbnail_path) for card in cards],
    ]
    _t0 = time.perf_counter()
    _img_results = await asyncio.gather(*_img_tasks)
    logger.debug("[perf] compose_chara_birthday_image preload %d images: %.3fs", len(_img_tasks), time.perf_counter() - _t0)
    card_image, sd_image, title_image = _img_results[0], _img_results[1], _img_results[2]
    card_thumbs = list(_img_results[3:])
    # 绘制时间范围的辅助函数
    def draw_time_range(label: str, tr: BirthdayEventTime):
        start_at = datetime_from_millis(tr.start_at, rqd.timezone)
        end_at = datetime_from_millis(tr.end_at, rqd.timezone)
        timezone_label = rqd.timezone or ""
        if timezone_label == "" and (start_at and start_at.tzinfo):
            timezone_label = start_at.tzname() or ""
        if timezone_label == "" and (end_at and end_at.tzinfo):
            timezone_label = end_at.tzname() or ""
        if timezone_label:
            timezone_label = f" ({timezone_label})"
        with HSplit().set_sep(8).set_content_align("l").set_item_align("l"):
            TextBox(f"{label} ", style1)
            TextBox(
                (
                    f"{start_at.strftime('%m-%d %H:%M')}"
                    f" ~ {end_at.strftime('%m-%d %H:%M')}{timezone_label}"
                ),
                style2,
            )

    with Canvas(bg=ImageBg(card_image)).set_padding(BG_PADDING) as canvas:
        with (
            VSplit()
            .set_content_align("c")
            .set_item_align("c")
            .set_padding(16)
            .set_sep(8)
            .set_item_bg(roundrect_bg(alpha=80))
            .set_bg(roundrect_bg(alpha=80))
        ):
            # 角色信息头部
            with HSplit().set_sep(16).set_padding(16).set_content_align("c").set_item_align("c"):
                ImageBox(sd_image, size=(None, 80), shadow=True)
                ImageBox(title_image, size=(None, 60))
                TextBox(
                    f"{month}月{day}日",
                    TextStyle(
                        DEFAULT_HEAVY_FONT,
                        32,
                        (100, 100, 100),
                        use_shadow=True,
                        shadow_offset=2,
                        shadow_color=tuple(color_code_to_rgb(color_code)),
                    ),
                )

            # 基本信息
            with VSplit().set_sep(4).set_padding(16).set_content_align("l").set_item_align("l"):
                with HSplit().set_sep(8).set_padding(0).set_content_align("l").set_item_align("l"):
                    TextBox(f"({region_name}) 距离下次生日还有{days_until_birthday}天", style1)
                    Spacer(w=16)
                    TextBox("应援色", style1)
                    TextBox(color_code, TextStyle(DEFAULT_FONT, 20, ADAPTIVE_WB)).set_bg(
                        RoundRectBg(tuple(color_code_to_rgb(color_code)), radius=4)
                    ).set_padding(8)

                # 时间范围 - 固定绘制
                draw_time_range("🎰卡池开放时间", rqd.gacha_time)
                draw_time_range("🎤虚拟LIVE时间", rqd.live_time)

            # 五周年特殊时间范围
            if is_fifth_anniv:
                with VSplit().set_sep(4).set_padding(16).set_content_align("l").set_item_align("l"):
                    if rqd.drop_time:
                        draw_time_range("💧露滴掉落时间", rqd.drop_time)
                    if rqd.flower_time:
                        draw_time_range("🌱浇水开放时间", rqd.flower_time)
                    if rqd.party_time:
                        draw_time_range("🎂派对开放时间", rqd.party_time)

            # 卡牌列表
            with HSplit().set_sep(4).set_padding(16).set_content_align("l").set_item_align("l"):
                TextBox("卡牌", style1)
                Spacer(w=8)
                with Grid(col_count=6).set_sep(4, 4):
                    for i, thumb in enumerate(card_thumbs):
                        with VSplit().set_sep(2).set_content_align("c").set_item_align("c"):
                            ImageBox(thumb, size=(80, 80), shadow=True)
                            TextBox(f"{cards[i].id}", TextStyle(DEFAULT_FONT, 16, (50, 50, 50)))

            # 底部角色生日日历
            with Grid(col_count=13).set_sep(2, 2).set_padding(16).set_content_align("c").set_item_align("c"):
                # 找到起始角色（从小豆沙开始，ID=6）
                idx = 0
                start_cid = 6
                for i, item in enumerate(all_characters):
                    if item.cid == start_cid:
                        idx = i
                        break

                for _ in range(len(all_characters)):
                    chara = all_characters[idx % len(all_characters)]
                    idx += 1

                    with VSplit().set_sep(0).set_content_align("c").set_item_align("c"):
                        # 使用model中传入的icon_path
                        chara_icon = await get_img_from_path(ASSETS_BASE_DIR, chara.icon_path)

                        b = ImageBox(chara_icon, size=(40, 40)).set_padding(4)
                        if chara.cid == cid:
                            b.set_bg(roundrect_bg(radius=8, alpha=80))
                        TextBox(f"{chara.month}/{chara.day}", TextStyle(DEFAULT_FONT, 14, (50, 50, 80)))

    add_request_watermark(canvas, rqd)
    return await canvas.get_img()


async def compose_alias_list_image(rqd: AliasListRequest) -> Image.Image:
    aliases = [alias.strip() for alias in rqd.aliases if alias and alias.strip()]
    cache_key = _build_alias_list_cache_key(rqd)
    cached = get_composed_image_cached(cache_key)
    if cached is not None:
        return cached
    disk_cached = get_composed_image_disk_cached(_ALIAS_LIST_CACHE_NAMESPACE, cache_key)
    if disk_cached is not None:
        put_composed_image_cache(cache_key, disk_cached)
        return disk_cached

    _t0 = time.perf_counter()
    accent = _resolve_alias_accent(rqd.entity_label, rqd.entity_id)
    jacket_img = None
    if rqd.music_jacket_path:
        jacket_img = await get_img_resized(ASSETS_BASE_DIR, rqd.music_jacket_path, 92, 92)
    name_box_w = _resolve_alias_name_box_width(rqd.entity_name, jacket_img is not None)

    style_title = TextStyle(
        DEFAULT_HEAVY_FONT,
        30,
        BLACK,
        use_shadow=True,
        shadow_offset=2,
        shadow_color=_with_alpha(accent, 180),
    )
    style_name = TextStyle(DEFAULT_HEAVY_FONT, 28, BLACK)
    style_label = TextStyle(DEFAULT_BOLD_FONT, 20, (60, 60, 80))
    style_meta = TextStyle(DEFAULT_FONT, 17, (78, 78, 98))
    style_id = TextStyle(DEFAULT_HEAVY_FONT, 34, WHITE)
    style_badge = TextStyle(DEFAULT_BOLD_FONT, 18, (66, 66, 86))
    style_chip = TextStyle(DEFAULT_FONT, 18, (48, 48, 64))

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16):
            TextBox(rqd.title, style_title).set_padding((20, 16)).set_bg(
                roundrect_bg(alpha=88, blur_glass_kwargs={"blur": 8})
            )

            with HSplit().set_content_align("t").set_item_align("t").set_sep(16).set_padding(18).set_bg(
                roundrect_bg(alpha=86, blur_glass_kwargs={"blur": 8})
            ):
                with (
                    VSplit()
                    .set_content_align("c")
                    .set_item_align("c")
                    .set_sep(8)
                    .set_padding((18, 16))
                    .set_bg(RoundRectBg(fill=_with_alpha(accent, 205), radius=14))
                ):
                    TextBox(rqd.entity_label, TextStyle(DEFAULT_BOLD_FONT, 18, WHITE))
                    TextBox(str(rqd.entity_id), style_id)

                with HSplit().set_content_align("t").set_item_align("t").set_sep(16):
                    with VSplit().set_content_align("l").set_item_align("l").set_sep(10):
                        TextBox(rqd.entity_name, style_name, use_real_line_count=True).set_w(name_box_w)
                        TextBox("下列结果为已审核通过的别名展示", style_meta)
                        with HSplit().set_content_align("c").set_item_align("c").set_sep(10):
                            TextBox(f"已审核别名 {len(aliases)} 条", style_badge).set_padding((14, 8)).set_bg(
                                RoundRectBg(
                                    fill=(255, 255, 255, 128),
                                    radius=10,
                                    stroke=_with_alpha(accent, 96),
                                    stroke_width=1,
                                )
                            )
                            TextBox("过多时自动转为图片返回", style_meta).set_padding((14, 8)).set_bg(
                                RoundRectBg(
                                    fill=(255, 255, 255, 128),
                                    radius=10,
                                    stroke=_with_alpha(accent, 96),
                                    stroke_width=1,
                                )
                            )
                    if jacket_img is not None:
                        ImageBox(jacket_img, size=(92, 92), use_alpha_blend=True, shadow=True).set_bg(
                            RoundRectBg(
                                fill=(255, 255, 255, 128),
                                radius=12,
                                stroke=_with_alpha(accent, 96),
                                stroke_width=1,
                            )
                        ).set_padding(4)

            with VSplit().set_content_align("lt").set_item_align("lt").set_sep(12).set_padding(18).set_bg(
                roundrect_bg(alpha=84, blur_glass_kwargs={"blur": 8})
            ):
                with HSplit().set_content_align("c").set_item_align("c").set_sep(12):
                    TextBox("已审核别名", style_label)
                    TextBox(f"{len(aliases)}", style_badge).set_padding((12, 6)).set_bg(
                        RoundRectBg(
                            fill=(255, 255, 255, 128),
                            radius=9,
                            stroke=_with_alpha(accent, 96),
                            stroke_width=1,
                        )
                    )
                with (
                    Flow()
                    .set_w(980)
                    .set_content_align("lt")
                    .set_item_align("lt")
                    .set_sep(10, 10)
                ):
                    for alias in aliases:
                        TextBox(alias, style_chip).set_bg(
                            RoundRectBg(
                                fill=(255, 255, 255, 136),
                                radius=11,
                                stroke=_with_alpha(accent, 108),
                                stroke_width=1,
                            )
                        ).set_padding((14, 9))

    add_request_watermark(canvas, rqd)
    image = await canvas.get_img()
    put_composed_image_cache(cache_key, image)
    put_composed_image_disk_cache(_ALIAS_LIST_CACHE_NAMESPACE, cache_key, image)
    if time.perf_counter() - _t0 >= 0.05:
        logger.info(
            "[perf] alias_list miss: entity_label=%s entity_id=%s aliases=%d total=%.3fs",
            rqd.entity_label,
            rqd.entity_id,
            len(aliases),
            time.perf_counter() - _t0,
        )
    return image
