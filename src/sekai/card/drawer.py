import asyncio
import logging
import math
import time

from src.sekai.base import (
    ASSETS_BASE_DIR,
    BG_PADDING,
    CHARACTER_COLOR_CODE,
    DEFAULT_BOLD_FONT,
    DEFAULT_FONT,
    SEKAI_BLUE_BG,
    add_request_watermark,
    color_code_to_rgb,
    get_img_from_path,
    roundrect_bg,
)
from src.sekai.base.plot import (
    Canvas,
    FillBg,
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
from src.sekai.profile.drawer import (
    get_card_full_thumbnail,
    get_profile_card,
)

# 从 model.py 导入数据模型
from .model import (
    CardBoxRequest,
    CardDetailRequest,
    CardListRequest,
)

NON_LIMITED_SUPPLY_TYPES = {"", "normal", "非限定"}
TERM_LIMITED_SUPPLY_TYPES = {"期间限定", "WL限定", "联动限定"}
FES_LIMITED_SUPPLY_TYPES = {"Fes限定", "CFes限定", "BFes限定"}

logger = logging.getLogger(__name__)


def is_non_limited_supply_type(value: str | None) -> bool:
    return (value or "").strip() in NON_LIMITED_SUPPLY_TYPES


def get_notice_dimensions(content_width: int, min_width: int = 520) -> tuple[int, int]:
    panel_width = max(min_width, content_width)
    text_width = max(240, panel_width - 120)
    return panel_width, text_width


# ========== 主要函数 ==========


async def compose_card_detail_image(
    rqd: CardDetailRequest, title: str | None = None, title_style: TextStyle = None, title_shadow: bool = False
):
    """
    合成卡牌详情图片
    """
    card_info = rqd.card_info
    region = rqd.region
    power_info = rqd.card_info.power
    skill_info = rqd.card_info.skill
    sp_skill_info = rqd.card_info.special_skill_info
    # 获取图片（并行）
    _img_tasks = [
        *[get_img_from_path(ASSETS_BASE_DIR, path) for path in rqd.card_images_path],
        *[get_img_from_path(ASSETS_BASE_DIR, path) for path in rqd.costume_images_path],
        *[get_card_full_thumbnail(thumbnail) for thumbnail in rqd.card_info.thumbnail_info],
        get_img_from_path(ASSETS_BASE_DIR, rqd.character_icon_path),
        get_img_from_path(ASSETS_BASE_DIR, rqd.unit_logo_path),
        get_img_from_path(ASSETS_BASE_DIR, skill_info.skill_type_icon_path),
    ]
    if sp_skill_info:
        _img_tasks.append(get_img_from_path(ASSETS_BASE_DIR, sp_skill_info.skill_type_icon_path))
    _t0 = time.perf_counter()
    _img_results = await asyncio.gather(*_img_tasks)
    logger.debug("[perf] compose_card_detail_image preload %d images: %.3fs", len(_img_tasks), time.perf_counter() - _t0)

    _n_cards = len(rqd.card_images_path)
    _n_costumes = len(rqd.costume_images_path)
    _n_thumbs = len(rqd.card_info.thumbnail_info)
    _offset = 0
    card_images = list(_img_results[_offset:_offset + _n_cards])
    _offset += _n_cards
    costume_images = list(_img_results[_offset:_offset + _n_costumes])
    _offset += _n_costumes
    thumbnail_images = list(_img_results[_offset:_offset + _n_thumbs])
    _offset += _n_thumbs
    character_icon = _img_results[_offset]
    _offset += 1
    unit_logo = _img_results[_offset]
    _offset += 1
    skill_type_icon = _img_results[_offset]
    _offset += 1
    if sp_skill_info:
        sp_skill_type_icon = _img_results[_offset]

    # 处理事件横幅
    event_detail = None
    if rqd.event_info:
        event_detail = rqd.event_info

    # 处理卡池横幅
    gacha_detail = None
    if rqd.gacha_info:
        gacha_detail = rqd.gacha_info

    # 预加载关联活动/卡池图片（并行）
    _extra_tasks = {}
    if event_detail:
        _extra_tasks["event_banner"] = get_img_from_path(ASSETS_BASE_DIR, event_detail.event_banner_path)
        if event_detail.bonus_attr and rqd.event_attr_icon_path:
            _extra_tasks["event_attr"] = get_img_from_path(ASSETS_BASE_DIR, rqd.event_attr_icon_path)
        if event_detail.unit and rqd.event_unit_icon_path:
            _extra_tasks["event_unit"] = get_img_from_path(ASSETS_BASE_DIR, rqd.event_unit_icon_path)
        if event_detail.banner_cid and rqd.event_chara_icon_path:
            _extra_tasks["event_chara"] = get_img_from_path(ASSETS_BASE_DIR, rqd.event_chara_icon_path)
    if gacha_detail:
        _extra_tasks["gacha_banner"] = get_img_from_path(ASSETS_BASE_DIR, gacha_detail.gacha_banner_path)
    _extra_keys = list(_extra_tasks.keys())
    _extra_imgs = dict(zip(_extra_keys, await asyncio.gather(*_extra_tasks.values()))) if _extra_tasks else {}

    # 时间格式化
    release_time = datetime_from_millis(card_info.release_at, rqd.timezone)

    # 样式定义
    title_style_def = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(0, 0, 0))
    label_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(50, 50, 50))
    text_style = TextStyle(font=DEFAULT_FONT, size=24, color=(70, 70, 70))
    small_style = TextStyle(font=DEFAULT_FONT, size=18, color=(70, 70, 70))
    tip_style = TextStyle(font=DEFAULT_FONT, size=18, color=(0, 0, 0))  # noqa: F841

    # 使用传入的背景图片，如果没有则使用默认蓝色背景
    if rqd.background_image_path:
        try:
            bg_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.background_image_path, on_missing="raise")
            bg = ImageBg(bg_img)
        except (FileNotFoundError, OSError, ValueError):
            bg = SEKAI_BLUE_BG
    else:
        bg = SEKAI_BLUE_BG

    with Canvas(bg=bg).set_padding(BG_PADDING) as canvas:
        with HSplit().set_sep(16).set_content_align("lt").set_item_align("lt"):
            # 左侧: 卡面+关联活动+关联卡池+提示
            with (
                VSplit()
                .set_padding(0)
                .set_sep(16)
                .set_content_align("lt")
                .set_item_align("lt")
                .set_item_bg(roundrect_bg(alpha=80))
            ):
                # 卡面
                with VSplit().set_padding(16).set_sep(8).set_content_align("lt").set_item_align("lt"):
                    for img in card_images:
                        ImageBox(img, size=(500, None))

                # 关联活动
                if event_detail:
                    with VSplit().set_padding(16).set_sep(12).set_content_align("lt").set_item_align("lt"):
                        with HSplit().set_padding(0).set_sep(8).set_content_align("l").set_item_align("l"):
                            TextBox("当期活动", label_style)
                            TextBox(f"【{event_detail.event_id}】{event_detail.event_name}", small_style).set_w(360)
                        with HSplit().set_padding(0).set_sep(8).set_content_align("lt").set_item_align("lt"):
                            ImageBox(
                                _extra_imgs["event_banner"],
                                size=(250, None),
                            )
                            with VSplit().set_content_align("c").set_item_align("c").set_sep(6):
                                TextBox(f"开始时间: {event_detail.start_at.strftime('%Y-%m-%d %H:%M')}", small_style)
                                TextBox(f"结束时间: {event_detail.end_at.strftime('%Y-%m-%d %H:%M')}", small_style)
                                Spacer(h=4)
                                with HSplit().set_padding(0).set_sep(8).set_content_align("l").set_item_align("l"):
                                    # 属性、团队、角色图标
                                    if event_detail.bonus_attr and rqd.event_attr_icon_path:
                                        ImageBox(
                                            _extra_imgs["event_attr"],
                                            size=(32, None),
                                        )
                                    if event_detail.unit and rqd.event_unit_icon_path:
                                        ImageBox(
                                            _extra_imgs["event_unit"],
                                            size=(32, None),
                                        )
                                    if event_detail.banner_cid and rqd.event_chara_icon_path:
                                        ImageBox(
                                            _extra_imgs["event_chara"],
                                            size=(32, None),
                                        )

                # 关联卡池
                if gacha_detail:
                    with VSplit().set_padding(16).set_sep(12).set_content_align("lt").set_item_align("lt"):
                        with HSplit().set_padding(0).set_sep(8).set_content_align("l").set_item_align("l"):
                            TextBox("当期卡池", label_style)
                            TextBox(f"【{gacha_detail.gacha_id}】{gacha_detail.gacha_name}", small_style).set_w(360)
                        with HSplit().set_padding(0).set_sep(8).set_content_align("lt").set_item_align("lt"):
                            ImageBox(
                                _extra_imgs["gacha_banner"],
                                size=(250, None),
                            )
                            with VSplit().set_content_align("c").set_item_align("c").set_sep(6):
                                TextBox(f"开始时间: {gacha_detail.start_at.strftime('%Y-%m-%d %H:%M')}", small_style)
                                TextBox(f"结束时间: {gacha_detail.end_at.strftime('%Y-%m-%d %H:%M')}", small_style)

            # 右侧: 标题+限定类型+综合力+技能+发布时间+缩略图+衣装
            w = 600
            with (
                VSplit()
                .set_padding(0)
                .set_sep(16)
                .set_content_align("lt")
                .set_item_align("lt")
                .set_item_bg(roundrect_bg(alpha=80))
            ):
                # 标题
                with HSplit().set_padding(16).set_sep(32).set_content_align("c").set_item_align("c").set_w(w):
                    ImageBox(unit_logo, size=(None, 64))
                    with VSplit().set_content_align("c").set_item_align("c").set_sep(12):
                        TextBox(card_info.prefix, title_style_def).set_w(w - 260).set_content_align("c")
                        with HSplit().set_content_align("c").set_item_align("c").set_sep(8):
                            ImageBox(character_icon, size=(None, 32))
                            TextBox(card_info.character_name, title_style_def)

                with (
                    VSplit()
                    .set_padding(16)
                    .set_sep(8)
                    .set_item_bg(roundrect_bg(alpha=80))
                    .set_content_align("l")
                    .set_item_align("l")
                ):
                    # 卡牌ID 限定类型
                    with HSplit().set_padding(16).set_sep(8).set_content_align("l").set_item_align("l"):
                        TextBox("ID", label_style)
                        TextBox(f"{card_info.card_id} ({region.upper()})", text_style)
                        Spacer(w=32)
                        TextBox("限定类型", label_style)
                        TextBox(card_info.supply_type, text_style)

                    # 综合力
                    with HSplit().set_padding(16).set_sep(8).set_content_align("lb").set_item_align("lb"):
                        TextBox("综合力", label_style)
                        TextBox(
                            f"{power_info.power_total} "
                            f"({power_info.power1}/{power_info.power2}/{power_info.power3}) "
                            "(满级0破无剧情)",
                            text_style,
                        )

                    # 技能
                    with VSplit().set_padding(16).set_sep(8).set_content_align("l").set_item_align("l"):
                        with HSplit().set_padding(0).set_sep(8).set_content_align("l").set_item_align("l"):
                            TextBox("技能", label_style)
                            if skill_type_icon:
                                ImageBox(skill_type_icon, size=(32, 32))
                            TextBox(skill_info.skill_name, text_style).set_w(w - 24 * 2 - 32 - 16)
                        TextBox(skill_info.skill_detail, text_style, use_real_line_count=True).set_w(w)
                        if skill_info.skill_detail_cn:
                            TextBox(
                                skill_info.skill_detail_cn.removesuffix("。"), text_style, use_real_line_count=True
                            ).set_w(w)

                    # 特训技能
                    if sp_skill_info:
                        with VSplit().set_padding(16).set_sep(8).set_content_align("l").set_item_align("l"):
                            with HSplit().set_padding(0).set_sep(8).set_content_align("l").set_item_align("l"):
                                TextBox("特训后技能", label_style)
                                if sp_skill_type_icon:
                                    ImageBox(sp_skill_type_icon, size=(32, 32))
                                TextBox(sp_skill_info.skill_name, text_style).set_w(w - 24 * 5 - 32 - 16)
                            TextBox(sp_skill_info.skill_detail, text_style, use_real_line_count=True).set_w(w)
                            if sp_skill_info.skill_detail_cn:
                                TextBox(
                                    sp_skill_info.skill_detail_cn.removesuffix("。"),
                                    text_style,
                                    use_real_line_count=True,
                                ).set_w(w)

                    # 发布时间
                    with HSplit().set_padding(16).set_sep(8).set_content_align("lb").set_item_align("lb"):
                        TextBox("发布时间", label_style)
                        TextBox(release_time.strftime("%Y-%m-%d %H:%M:%S"), text_style)

                    # 缩略图
                    with HSplit().set_padding(16).set_sep(16).set_content_align("l").set_item_align("l"):
                        TextBox("缩略图", label_style)
                        for img in thumbnail_images:
                            ImageBox(img, size=(100, None))

                    # 衣装
                    if len(costume_images) > 0:
                        with HSplit().set_padding(16).set_sep(16).set_content_align("l").set_item_align("l"):
                            TextBox("衣装", label_style)
                            with Grid(col_count=5).set_sep(8, 8):
                                for img in costume_images:
                                    ImageBox(img, size=(80, None))

    add_request_watermark(canvas, rqd)
    return await canvas.get_img()


async def compose_card_list_image(
    rqd: CardListRequest, title: str | None = None, title_style: TextStyle = None, title_shadow: bool = False
):
    """
    合成卡牌列表图片
    """
    cards = rqd.cards
    region = rqd.region  # noqa: F841
    user_info = rqd.user_info
    # 如果只有一张卡，调用详情函数

    # 创建用户卡牌ID到卡牌信息的映射
    user_card_map = {}
    if user_info and user_info.user_cards:
        for user_card in user_info.user_cards:
            if isinstance(user_card, dict) and "cardId" in user_card:
                user_card_map[user_card["cardId"]] = user_card

    async def get_card_list_thumbs(card):
        thumbnails = card.thumbnail_info or []
        if not thumbnails:
            return []
        if len(thumbnails) == 1:
            img = await get_card_full_thumbnail(thumbnails[0])
            return [img] if img is not None else []
        normal, after = await asyncio.gather(
            get_card_full_thumbnail(thumbnails[0]),
            get_card_full_thumbnail(thumbnails[1]),
        )
        return [img for img in (normal, after) if img is not None]

    thumbs = await asyncio.gather(*[get_card_list_thumbs(card) for card in rqd.cards])

    # 并行获取所有缩略图
    card_and_thumbs = [(card, thumb_group) for card, thumb_group in zip(cards, thumbs) if thumb_group]

    # 按发布时间和ID排序
    card_and_thumbs.sort(key=lambda x: (x[0].release_at, x[0].card_id), reverse=True)

    # 样式定义
    name_style = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(0, 0, 0))
    id_style = TextStyle(font=DEFAULT_FONT, size=20, color=(0, 0, 0))
    leak_style = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(200, 0, 0))
    notice_label_style = TextStyle(font=DEFAULT_BOLD_FONT, size=22, color=(166, 90, 0))
    notice_text_style = TextStyle(font=DEFAULT_FONT, size=22, color=(98, 68, 0))

    # 使用传入的背景图片，如果没有则使用默认背景
    if rqd.background_img_path:
        try:
            bg_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.background_img_path, on_missing="raise")
            bg = ImageBg(bg_img)
        except (FileNotFoundError, OSError, ValueError):
            bg = SEKAI_BLUE_BG
    else:
        bg = SEKAI_BLUE_BG

    term_img = None
    fes_img = None
    if rqd.term_limited_icon_path:
        try:
            term_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.term_limited_icon_path)
        except FileNotFoundError:
            pass
    if rqd.fes_limited_icon_path:
        try:
            fes_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.fes_limited_icon_path)
        except FileNotFoundError:
            pass

    list_panel_width, list_notice_text_width = get_notice_dimensions(300 * 3 + 16 * 2, min_width=300 * 3 + 16 * 2)

    with Canvas(bg=bg).set_padding(BG_PADDING) as canvas:
        with VSplit().set_sep(16).set_content_align("lt").set_item_align("lt"):
            now = request_now(rqd.timezone)
            if rqd.title:
                with (
                    HSplit()
                    .set_bg(roundrect_bg(fill=(255, 246, 219, 220)))
                    .set_padding(14)
                    .set_sep(12)
                    .set_content_align("l")
                    .set_item_align("c")
                    .set_w(list_panel_width)
                ):
                    TextBox("提示", notice_label_style)
                    TextBox(rqd.title, notice_text_style, use_real_line_count=True).set_w(list_notice_text_width)
            # 卡牌网格
            with Grid(col_count=3).set_bg(roundrect_bg(alpha=80)).set_padding(16):
                for i, (card, thumb_group) in enumerate(card_and_thumbs):
                    # 背景设置 - 确保毛玻璃效果启用
                    if not is_non_limited_supply_type(card.supply_type):
                        # 限定卡牌：使用淡黄色背景，确保有足够的透明度
                        bg = roundrect_bg(fill=(255, 250, 220, 200), blur_glass=True)
                    else:
                        # 普通卡牌：使用默认的半透明白色背景
                        bg = roundrect_bg(alpha=80)  # 默认已经是半透明+毛玻璃效果

                    with Frame().set_content_align("lb").set_bg(bg):
                        # 检查是否为未来卡牌
                        release_time = datetime_from_millis(card.release_at, rqd.timezone)
                        if release_time > now:
                            TextBox("LEAK", leak_style).set_offset((4, -4))

                        # 技能图标区域
                        with Frame().set_content_align("rb"):
                            # 根据skill_type自动匹配技能图标
                            if card.skill and card.skill.skill_type:
                                skill_icon_path = card.skill.skill_type_icon_path
                                try:
                                    skill_img = await get_img_from_path(ASSETS_BASE_DIR, skill_icon_path)
                                    ImageBox(skill_img, image_size_mode="fit").set_w(32).set_margin(8)
                                except FileNotFoundError:
                                    # 如果找不到对应的技能图标，静默跳过
                                    pass

                            # 卡牌信息区域
                            with VSplit().set_content_align("c").set_item_align("c").set_sep(5).set_padding(8):
                                GW = 300
                                with HSplit().set_content_align("c").set_w(GW).set_padding(8).set_sep(16):
                                    supply_name = card.supply_type or ""
                                    for thumb in thumb_group:
                                        with Frame().set_content_align("rt"):
                                            ImageBox(thumb, size=(100, 100), image_size_mode="fill", shadow=True)
                                            limited_icon_width = 75
                                            if supply_name in TERM_LIMITED_SUPPLY_TYPES:
                                                if term_img:
                                                    ImageBox(term_img, size=(limited_icon_width, None))
                                            elif supply_name in FES_LIMITED_SUPPLY_TYPES:
                                                if fes_img:
                                                    ImageBox(fes_img, size=(limited_icon_width, None))

                                # 卡牌名称
                                name_text = card.prefix
                                TextBox(name_text, name_style).set_w(GW).set_content_align("c")

                                # ID和限定类型
                                id_text = f"ID:{card.card_id}"
                                if not is_non_limited_supply_type(card.supply_type):
                                    id_text += f"【{card.supply_type}】"
                                TextBox(id_text, id_style).set_w(GW).set_content_align("c")

    add_request_watermark(canvas, rqd)
    return await canvas.get_img()


async def compose_box_image(
    rqd: CardBoxRequest, title: str | None = None, title_style: TextStyle = None, title_shadow: bool = False
):
    """
    合成卡牌一览图片（按角色分类的卡牌收集册）
    """
    cards = rqd.cards
    region = rqd.region  # noqa: F841
    user_info = rqd.user_info
    show_id = rqd.show_id
    show_box = rqd.show_box

    async def get_box_thumb(card):
        thumbnails = card.card.thumbnail_info or []
        if not thumbnails:
            return None
        if len(thumbnails) == 1:
            return await get_card_full_thumbnail(thumbnails[0])
        if card.card.is_after_training:
            return await get_card_full_thumbnail(thumbnails[1])
        return await get_card_full_thumbnail(thumbnails[0])

    thumbs = await asyncio.gather(*[get_box_thumb(card) for card in cards])

    # 按角色收集卡牌
    chara_cards = {}
    for card, img in zip(cards, thumbs):
        if not img:
            continue
        chara_id = card.card.character_id
        if chara_id not in chara_cards:
            chara_cards[chara_id] = []

        # 添加卡牌图片和拥有状态
        card_data = {
            **card.model_dump(),
            "img": img,
            "has": card.has_card,  # 恢复拥有状态判断
        }

        # 如果只显示拥有卡牌且用户没有此卡，跳过
        if show_box and not card_data["has"]:
            continue

        chara_cards[chara_id].append(card_data)

    # 按角色ID和稀有度排序
    chara_cards = list(chara_cards.items())
    chara_cards.sort(key=lambda x: x[0])
    for i in range(len(chara_cards)):
        chara_cards[i][1].sort(key=lambda x: (x["card"]["rare"], x["card"]["release_at"], x["card"]["card_id"]))

    # 计算最佳高度限制以优化布局
    max_card_num = max([len(cards) for _, cards in chara_cards]) if chara_cards else 0
    best_height, best_value = 10000, 1e9
    for i in range(1, max_card_num + 1):
        # 计算优化目标：max(h,w)越小越好，空白越少越好
        max_height = 0
        total_width = 0
        for _, cards in chara_cards:
            max_height = max(max_height, min(len(cards), i))
        total, space = 0, 0
        for _, cards in chara_cards:
            width = math.ceil(len(cards) / i)
            total_width += width
            total += max_height * width
            space += max_height * width - len(cards)
        # value = max(total_width, max_height) * total / (total - space)
        value = max(total_width, max_height * 0.5) if total_width > 9 else max(total_width * 0.5, max_height)
        if value < best_value:
            best_height, best_value = i, value

    # 计算总宽度并决定绘制卡牌的大小
    total_width = 0
    for _, cards in chara_cards:
        width = max(1, math.ceil(len(cards) / best_height))
        total_width += width
    area = total_width * (best_height + 4)

    start_area, start_sz, start_sep = 9 * 5, 100, 8
    end_area, end_sz, end_sep = 26 * 50, 48, 4
    interp = min(1.0, max(0.0, (area - start_area) / (end_area - start_area)))
    sep = int(start_sep + (end_sep - start_sep) * interp)
    sz = int(start_sz + (end_sz - start_sz) * interp)

    box_content_width = 16 * 2
    if chara_cards:
        group_widths = []
        for _, cards in chara_cards:
            col_num = max(1, math.ceil(len(cards) / best_height))
            group_widths.append(sz * col_num + sep * (col_num - 1))
        box_content_width += sum(group_widths) + max(0, len(group_widths) - 1) * 4
    box_notice_width, box_notice_text_width = get_notice_dimensions(box_content_width)

    # 预加载所有图标
    term_img = None
    fes_img = None
    if rqd.term_limited_icon_path:
        try:
            term_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.term_limited_icon_path)
        except FileNotFoundError:
            pass
    if rqd.fes_limited_icon_path:
        try:
            fes_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.fes_limited_icon_path)
        except FileNotFoundError:
            pass

    # 预加载角色图标
    chara_icons = {}
    if rqd.character_icon_paths:
        _cids = list(rqd.character_icon_paths.keys())
        _cpaths = list(rqd.character_icon_paths.values())
        _cimgs = await asyncio.gather(*[get_img_from_path(ASSETS_BASE_DIR, p) for p in _cpaths])
        chara_icons = dict(zip(_cids, _cimgs))
    # 绘制单张卡
    def draw_card(card_data):
        with Frame().set_content_align("rt"):
            ImageBox(card_data["img"], size=(sz, sz))

            # 限定类型图标
            supply_name = card_data["card"].get("supply_type", "")
            limited_icon_width = int(sz * 0.75)
            if supply_name in TERM_LIMITED_SUPPLY_TYPES:
                if term_img:
                    ImageBox(term_img, size=(limited_icon_width, None))
            elif supply_name in FES_LIMITED_SUPPLY_TYPES:
                if fes_img:
                    ImageBox(fes_img, size=(limited_icon_width, None))

            # 如果用户没有此卡牌，添加遮罩
            if not card_data["has"] and user_info:
                Spacer(w=sz, h=sz).set_bg(RoundRectBg(fill=(0, 0, 0, 120), radius=2))

        if show_id:
            TextBox(f"{card_data['card']['card_id']}", TextStyle(font=DEFAULT_FONT, size=12, color=(0, 0, 0))).set_w(sz)

    # 使用传入的背景图片，如果没有则使用默认背景
    if rqd.background_img_path:
        try:
            bg_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.background_img_path, on_missing="raise")
            bg = ImageBg(bg_img)
        except (FileNotFoundError, OSError, ValueError):
            bg = SEKAI_BLUE_BG
    else:
        bg = SEKAI_BLUE_BG

    with Canvas(bg=bg).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align("lt").set_item_align("lt").set_sep(16):
            if rqd.title:
                with (
                    HSplit()
                    .set_bg(roundrect_bg(fill=(255, 246, 219, 220)))
                    .set_padding(14)
                    .set_sep(12)
                    .set_content_align("l")
                    .set_item_align("c")
                    .set_w(box_notice_width)
                ):
                    TextBox("提示", TextStyle(font=DEFAULT_BOLD_FONT, size=22, color=(166, 90, 0)))
                    TextBox(
                        rqd.title,
                        TextStyle(font=DEFAULT_FONT, size=22, color=(98, 68, 0)),
                        use_real_line_count=True,
                    ).set_w(box_notice_text_width)
            if user_info:
                user_profile = await get_profile_card(user_info.to_profile_card_request())  # noqa: F841

            # 卡牌网格
            with (
                HSplit()
                .set_bg(roundrect_bg(alpha=80))
                .set_content_align("lt")
                .set_item_align("lt")
                .set_padding(16)
                .set_sep(4)
            ):
                for chara_id, cards in chara_cards:
                    with VSplit().set_content_align("t").set_item_align("t").set_sep(4):
                        # 角色图标
                        chara_icon = chara_icons.get(chara_id)
                        ImageBox(chara_icon, size=(sz, sz))
                        color_code = rqd.character_color_codes.get(chara_id) or CHARACTER_COLOR_CODE[chara_id]
                        chara_color = color_code_to_rgb(color_code)
                        col_num = max(1, len(range(0, len(cards), best_height)))
                        row_num = max(1, min(best_height, len(cards)))
                        Spacer(w=sz * col_num + sep * (col_num - 1), h=sep).set_bg(FillBg(chara_color))
                        # 卡牌列表
                        with Grid(row_count=row_num, vertical=row_num > col_num).set_content_align("lt").set_item_align(
                            "lt"
                        ).set_sep(sep, sep):
                            for card_data in cards:
                                draw_card(card_data)

    add_request_watermark(canvas, rqd)
    return await canvas.get_img()
