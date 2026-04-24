import asyncio
import logging
import time

from PIL import Image

from src.sekai.base.draw import (
    BG_PADDING,
    CHARACTER_COLOR_CODE,
    SEKAI_BLUE_BG,
    Canvas,
    TextBox,
    add_request_watermark,
    roundrect_bg,
)
from src.sekai.base.painter import ADAPTIVE_WB, WHITE, color_code_to_rgb
from src.sekai.base.plot import (
    Flow,
    Frame,
    Grid,
    HSplit,
    ImageBg,
    ImageBox,
    RoundRectBg,
    Spacer,
    TextStyle,
    VSplit,
    Widget,
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
_ALIAS_TRIM_ALPHA_FLOOR = 36


def _with_alpha(color: tuple[int, int, int], alpha: int) -> tuple[int, int, int, int]:
    return (color[0], color[1], color[2], alpha)


def _resolve_alias_accent(entity_label: str, entity_id: int) -> tuple[int, int, int]:
    if "角色" in entity_label:
        if color_code := CHARACTER_COLOR_CODE.get(entity_id):
            return tuple(color_code_to_rgb(color_code))
        return (255, 204, 170)
    return (110, 180, 255)


def _build_alias_list_cache_key(rqd: AliasListRequest) -> str:
    trim_path = _resolve_alias_trim_path(rqd)
    request_payload = {
        "title": rqd.title,
        "entity_label": rqd.entity_label,
        "entity_id": rqd.entity_id,
        "entity_name": rqd.entity_name,
        "music_jacket_path": rqd.music_jacket_path,
        "character_trim_path": rqd.character_trim_path,
        "character_silhouette_path": rqd.character_silhouette_path,
        "aliases": [alias.strip() for alias in rqd.aliases if alias and alias.strip()],
    }
    return build_rendered_image_cache_key(
        _ALIAS_LIST_CACHE_NAMESPACE,
        request_payload,
        extra={"version": 8},
        asset_signatures={
            "music_jacket": get_image_asset_signature(ASSETS_BASE_DIR, rqd.music_jacket_path),
            "character_trim": get_image_asset_signature(ASSETS_BASE_DIR, trim_path),
        },
    )


def _resolve_alias_name_box_width(
    name: str,
    has_jacket: bool,
    has_trim: bool = False,
    panel_w: int | None = None,
) -> int:
    display_len = max(1, get_str_display_length(name.strip()))
    estimated = 112 + display_len * 14
    if has_trim:
        max_w = panel_w - (214 if has_jacket else 146) if panel_w is not None else None
        if has_jacket:
            return max(220, min(max_w or 420, estimated))
        return max(260, min(max_w or 500, estimated + 24))
    if has_jacket:
        return max(240, min(648, estimated))
    return max(280, min(760, estimated))


def _resolve_alias_trim_path(rqd: AliasListRequest) -> str | None:
    if rqd.character_silhouette_path and rqd.character_silhouette_path.strip():
        return rqd.character_silhouette_path.strip()
    if rqd.character_trim_path and rqd.character_trim_path.strip():
        return rqd.character_trim_path.strip()
    return None


def _prepare_alias_trim_image(img: Image.Image) -> Image.Image:
    img = img.convert("RGBA")
    bbox = img.getbbox()
    if bbox:
        img = img.crop(bbox)
    alpha_floor = _ALIAS_TRIM_ALPHA_FLOOR
    alpha = img.getchannel("A").point(
        lambda v: 0 if v <= alpha_floor else min(255, int((v - alpha_floor) * 255 / (255 - alpha_floor)))
    )
    img.putalpha(alpha)
    return img


def _resolve_alias_trim_metrics(left_panel_h: int) -> tuple[int, int, int, tuple[int, int]]:
    trim_display_h = max(500, min(920, int(left_panel_h * 0.97)))
    trim_frame_w = max(330, min(460, int(trim_display_h * 0.56)))
    trim_frame_h = max(left_panel_h, int(trim_display_h * 0.92))
    trim_offset = (-max(34, int(trim_display_h * 0.065)), max(28, int(trim_display_h * 0.074)))
    return trim_frame_w, trim_frame_h, trim_display_h, trim_offset


def _build_alias_info_panel(
    rqd: AliasListRequest,
    accent: tuple[int, int, int],
    jacket_img: Image.Image | None,
    aliases_count: int,
    panel_w: int,
    name_box_w: int,
    style_name: TextStyle,
    style_meta: TextStyle,
    style_id: TextStyle,
    style_badge: TextStyle,
):
    info_panel = (
        HSplit()
        .set_content_align("lt")
        .set_item_align("t")
        .set_sep(16)
        .set_padding(18)
        .set_w(panel_w)
        .set_bg(roundrect_bg(alpha=86, blur_glass_kwargs={"blur": 8}))
    )

    id_block = (
        VSplit()
        .set_content_align("c")
        .set_item_align("c")
        .set_sep(8)
        .set_padding((18, 16))
        .set_bg(RoundRectBg(fill=_with_alpha(accent, 205), radius=14))
    )
    id_block.add_item(TextBox(rqd.entity_label, TextStyle(DEFAULT_BOLD_FONT, 18, WHITE)))
    id_block.add_item(TextBox(str(rqd.entity_id), style_id))

    detail_row = HSplit().set_content_align("lt").set_item_align("t").set_sep(16)
    text_col = VSplit().set_content_align("l").set_item_align("l").set_sep(10)
    text_col.add_item(TextBox(rqd.entity_name, style_name, use_real_line_count=True).set_w(name_box_w))
    text_col.add_item(TextBox("下列结果为已审核通过的别名展示", style_meta))
    badge_row = HSplit().set_content_align("l").set_item_align("c").set_sep(10)
    badge_row.add_item(
        TextBox(f"已审核别名 {aliases_count} 条", style_badge)
        .set_padding((14, 8))
        .set_bg(
            RoundRectBg(
                fill=(255, 255, 255, 128),
                radius=10,
                stroke=_with_alpha(accent, 96),
                stroke_width=1,
            )
        )
    )
    badge_row.add_item(
        TextBox("过多时自动转为图片返回", style_meta)
        .set_padding((14, 8))
        .set_bg(
            RoundRectBg(
                fill=(255, 255, 255, 128),
                radius=10,
                stroke=_with_alpha(accent, 96),
                stroke_width=1,
            )
        )
    )
    text_col.add_item(badge_row)
    detail_row.add_item(text_col)

    if jacket_img is not None:
        detail_row.add_item(
            ImageBox(jacket_img, size=(92, 92), use_alpha_blend=True, shadow=True)
            .set_bg(
                RoundRectBg(
                    fill=(255, 255, 255, 128),
                    radius=12,
                    stroke=_with_alpha(accent, 96),
                    stroke_width=1,
                )
            )
            .set_padding(4)
        )

    info_panel.add_item(id_block)
    info_panel.add_item(detail_row)
    return info_panel


def _build_alias_list_panel(
    aliases: list[str],
    accent: tuple[int, int, int],
    panel_w: int,
    flow_w: int,
    style_label: TextStyle,
    style_badge: TextStyle,
    style_chip: TextStyle,
):
    alias_panel = (
        VSplit()
        .set_w(panel_w)
        .set_content_align("lt")
        .set_item_align("lt")
        .set_sep(12)
        .set_padding(18)
        .set_bg(roundrect_bg(alpha=84, blur_glass_kwargs={"blur": 8}))
    )
    header_row = HSplit().set_content_align("l").set_item_align("c").set_sep(12)
    header_row.add_item(TextBox("已审核别名", style_label))
    header_row.add_item(
        TextBox(f"{len(aliases)}", style_badge)
        .set_padding((12, 6))
        .set_bg(
            RoundRectBg(
                fill=(255, 255, 255, 128),
                radius=9,
                stroke=_with_alpha(accent, 96),
                stroke_width=1,
            )
        )
    )
    alias_panel.add_item(header_row)

    flow = Flow().set_w(flow_w).set_content_align("lt").set_item_align("lt").set_sep(10, 10)
    for alias in aliases:
        flow.add_item(
            TextBox(alias, style_chip)
            .set_bg(
                RoundRectBg(
                    fill=(255, 255, 255, 136),
                    radius=11,
                    stroke=_with_alpha(accent, 108),
                    stroke_width=1,
                )
            )
            .set_padding((14, 9))
        )
    alias_panel.add_item(flow)
    return alias_panel


def _build_alias_left_panel(
    rqd: AliasListRequest,
    aliases: list[str],
    accent: tuple[int, int, int],
    jacket_img: Image.Image | None,
    panel_w: int,
    flow_w: int,
    name_box_w: int,
    style_name: TextStyle,
    style_meta: TextStyle,
    style_id: TextStyle,
    style_badge: TextStyle,
    style_label: TextStyle,
    style_chip: TextStyle,
) -> VSplit:
    token = Widget._thread_local.set(None)
    try:
        left_panel = VSplit().set_w(panel_w).set_content_align("lt").set_item_align("lt").set_sep(16)
        left_panel.add_item(
            _build_alias_info_panel(
                rqd,
                accent,
                jacket_img,
                len(aliases),
                panel_w,
                name_box_w,
                style_name,
                style_meta,
                style_id,
                style_badge,
            )
        )
        left_panel.add_item(
            _build_alias_list_panel(
                aliases,
                accent,
                panel_w,
                flow_w,
                style_label,
                style_badge,
                style_chip,
            )
        )
        return left_panel
    finally:
        Widget._thread_local.reset(token)


def _build_alias_trim_panel(trim_img: Image.Image, left_panel_h: int) -> Frame:
    token = Widget._thread_local.set(None)
    try:
        trim_frame_w, trim_frame_h, trim_display_h, trim_offset = _resolve_alias_trim_metrics(left_panel_h)
        trim_panel = Frame().set_size((trim_frame_w, trim_frame_h)).set_content_align("rb").set_allow_draw_outside(True)
        trim_panel.add_item(
            ImageBox(trim_img, size=(None, trim_display_h), use_alpha_blend=True).set_offset(trim_offset)
        )
        return trim_panel
    finally:
        Widget._thread_local.reset(token)


def _resolve_alias_panel_widths(
    rqd: AliasListRequest,
    aliases: list[str],
    accent: tuple[int, int, int],
    jacket_img: Image.Image | None,
    target_h: int,
    style_name: TextStyle,
    style_meta: TextStyle,
    style_id: TextStyle,
    style_badge: TextStyle,
    style_label: TextStyle,
    style_chip: TextStyle,
) -> tuple[int, int, int]:
    candidate_widths = [700, 780, 860, 940, 1020, 1100]
    best_fit: tuple[int, int, int] | None = None
    best_overflow: tuple[int, int, int, int] | None = None

    for panel_w in candidate_widths:
        flow_w = panel_w - 80
        name_box_w = _resolve_alias_name_box_width(rqd.entity_name, jacket_img is not None, True, panel_w)
        temp_left_panel = _build_alias_left_panel(
            rqd,
            aliases,
            accent,
            jacket_img,
            panel_w,
            flow_w,
            name_box_w,
            style_name,
            style_meta,
            style_id,
            style_badge,
            style_label,
            style_chip,
        )
        left_h = temp_left_panel._get_self_size()[1]
        if left_h <= target_h:
            best_fit = (panel_w, flow_w, name_box_w)
            break
        overflow = left_h - target_h
        if best_overflow is None or overflow < best_overflow[0]:
            best_overflow = (overflow, panel_w, flow_w, name_box_w)

    if best_fit is not None:
        return best_fit
    assert best_overflow is not None
    return best_overflow[1], best_overflow[2], best_overflow[3]


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
    logger.debug(
        "[perf] compose_chara_birthday_image preload %d images: %.3fs", len(_img_tasks), time.perf_counter() - _t0
    )
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
                (f"{start_at.strftime('%m-%d %H:%M')} ~ {end_at.strftime('%m-%d %H:%M')}{timezone_label}"),
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
    trim_img = None
    trim_path = _resolve_alias_trim_path(rqd)
    if trim_path:
        try:
            trim_img = _prepare_alias_trim_image(
                await get_img_from_path(ASSETS_BASE_DIR, trim_path, on_missing="raise")
            )
        except (FileNotFoundError, OSError, ValueError):
            trim_img = None

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

            if trim_img is not None:
                panel_w, flow_w, name_box_w = _resolve_alias_panel_widths(
                    rqd,
                    aliases,
                    accent,
                    jacket_img,
                    760,
                    style_name,
                    style_meta,
                    style_id,
                    style_badge,
                    style_label,
                    style_chip,
                )
                left_panel = _build_alias_left_panel(
                    rqd,
                    aliases,
                    accent,
                    jacket_img,
                    panel_w,
                    flow_w,
                    name_box_w,
                    style_name,
                    style_meta,
                    style_id,
                    style_badge,
                    style_label,
                    style_chip,
                )
                trim_panel = _build_alias_trim_panel(trim_img, left_panel._get_self_size()[1])
                HSplit().set_content_align("lt").set_item_align("t").set_sep(0).add_item(left_panel).add_item(
                    trim_panel
                )
            else:
                name_box_w = _resolve_alias_name_box_width(rqd.entity_name, jacket_img is not None)
                with (
                    HSplit()
                    .set_content_align("t")
                    .set_item_align("t")
                    .set_sep(16)
                    .set_padding(18)
                    .set_bg(roundrect_bg(alpha=86, blur_glass_kwargs={"blur": 8}))
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

                with (
                    VSplit()
                    .set_content_align("lt")
                    .set_item_align("lt")
                    .set_sep(12)
                    .set_padding(18)
                    .set_bg(roundrect_bg(alpha=84, blur_glass_kwargs={"blur": 8}))
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
                    with Flow().set_w(980).set_content_align("lt").set_item_align("lt").set_sep(10, 10):
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
