import math
from PIL import Image
from datetime import datetime
from pydantic import BaseModel, field_validator
from src.base.painter import (
    color_code_to_rgb,
    DEFAULT_FONT,
    DEFAULT_BOLD_FONT,
)
from src.base.plot import (
    Canvas,
    HSplit,
    VSplit,
    ImageBox,
    TextBox,
    Grid,
    Frame,
    Spacer,
    TextStyle,
    ImageBg,
    RoundRectBg,
    FillBg
)
from typing import List, Optional, Union

from src.base.utils import get_img_from_path
from src.base.configs import ASSETS_BASE_DIR
from src.base.draw import (
    BG_PADDING,
    SEKAI_BLUE_BG,
    roundrect_bg,
    add_watermark,
    CHARACTER_COLOR_CODE
)
from src.profile.drawer import (
    DetailedProfileCardRequest,
    get_detailed_profile_card,
    get_card_full_thumbnail,
    CardFullThumbnailRequest
)


# ========== 数据模型定义 ==========

class CardPowerInfo(BaseModel):
    power_total: int #综合力，以下所有数据皆为0破未读剧情的数值
    power1: int #表现力
    power2: int #技术力
    power3: int #活力

class SkillInfo(BaseModel):
    skill_id: int #技能ID
    skill_name: str #技能名
    skill_type: str #技能类型
    skill_detail: str #技能详情
    skill_type_icon_path: Optional[str] = None #技能类型图片路径
    skill_detail_cn: Optional[str] = None #技能详情中文

class EventInfo(BaseModel):
    event_id: int #活动ID
    event_name: str #活动名
    start_time: Union[datetime, int, str] #活动开始时间
    end_time: Union[datetime, int, str]  #活动结束时间
    event_banner_path: str #活动横幅图片路径
    bonus_attr: Optional[str] = None #活动增幅属性
    unit: Optional[str] = None #活动增幅组合
    banner_cid: Optional[int] = None #横幅角色ID

    @field_validator('start_time', 'end_time', mode='before')
    @classmethod
    def parse_timestamp(cls, v):
        if isinstance(v, (int, str)):
            try:
                timestamp = int(v)
                return datetime.fromtimestamp(timestamp / 1000)
            except (ValueError, TypeError):
                raise ValueError(f"无效的时间戳: {v}")
        return v

class GachaInfo(BaseModel):
    gacha_id: int #招募ID
    gacha_name: str #招募名
    start_time: Union[datetime, int, str]  #开始时间
    end_time: Union[datetime, int, str]  #结束时间
    gacha_banner_path: str #招募横幅图片路径

    @field_validator('start_time', 'end_time', mode='before')
    @classmethod
    def parse_timestamp(cls, v):
        if isinstance(v, (int, str)):
            try:
                timestamp = int(v)
                return datetime.fromtimestamp(timestamp / 1000)
            except (ValueError, TypeError):
                raise ValueError(f"无效的时间戳: {v}")
        return v

class CardBasicInfo(BaseModel):
    id: int #卡片ID
    character_id: int #角色ID
    character_name: str #角色名
    unit: str #所属组合
    release_at: int #发布时间
    supply_type: str  # 类型
    card_rarity_type: str  # 稀有度
    attr: str  # 属性
    prefix: str  #卡名
    asset_bundle_name: str #资源名
    skill: SkillInfo
    thumbnail_info: List[CardFullThumbnailRequest]
    after_training: Optional[bool] = False #是否特训后

class CardDetailRequest(BaseModel):
    card_info: CardBasicInfo
    region: str #服务器地区
    power_info: CardPowerInfo
    skill_info: SkillInfo
    special_skill_info: Optional[SkillInfo] = None
    event_info: Optional[EventInfo] = None
    gacha_info: Optional[GachaInfo] = None
    card_images_path: List[str]  # 卡面图片路径
    costume_images_path: List[str]  # 服装图片路径
    character_icon_path: str #角色图标路径
    unit_logo_path: str #团队图标路径
    background_image_path: Optional[str] = None  # 背景图片路径
    event_attr_icon_path: Optional[str] = None  # 活动增幅属性图标路径
    event_unit_icon_path: Optional[str] = None  # 活动增幅组合图标路径
    event_chara_icon_path: Optional[str] = None  # 活动横幅角色图标路径

class CardListRequest(BaseModel):
    cards: List[CardBasicInfo]
    region: str
    user_info: Optional[DetailedProfileCardRequest] = None
    background_image_path: Optional[str] = None  # 背景图片路径

class UserCardInfo(BaseModel):
    card: CardBasicInfo
    has_card: bool

class CardBoxRequest(BaseModel):
    cards: List[UserCardInfo]
    region: str
    user_info: Optional[DetailedProfileCardRequest] = None
    show_id: bool = False
    show_box: bool = False
    background_image_path: Optional[str] = None  # 背景图片路径
    character_icon_paths: dict[int, str]  # 角色ID到图标路径的映射
    term_limited_icon_path: Optional[str] = None  # 期间限定图标路径
    fes_limited_icon_path: Optional[str] = None  # FES限定图标路径

# ========== 主要函数 ==========

async def compose_card_detail_image(rqd: CardDetailRequest, title: str = None, title_style: TextStyle = None, title_shadow: bool = False):
    """
    合成卡牌详情图片
    """
    card_info = rqd.card_info
    region = rqd.region
    power_info = rqd.power_info
    skill_info = rqd.skill_info
    # 获取图片

    card_images = [await get_img_from_path(ASSETS_BASE_DIR, path) for path in rqd.card_images_path]
    costume_images = [await get_img_from_path(ASSETS_BASE_DIR, path) for path in rqd.costume_images_path]


    # 构建完整缩略图（带框体、属性、星级）- 使用card_utils中的函数
    thumbnail_images = []
    for thumbnail in rqd.card_info.thumbnail_info:
        thumb_img = await get_card_full_thumbnail(thumbnail)
        thumbnail_images.append(thumb_img)

    character_icon = await get_img_from_path(ASSETS_BASE_DIR, rqd.character_icon_path)
    unit_logo = await get_img_from_path(ASSETS_BASE_DIR, rqd.unit_logo_path)

    skill_type_icon = await get_img_from_path(ASSETS_BASE_DIR, skill_info.skill_type_icon_path)
    if rqd.special_skill_info:
        sp_skill_type_icon = await get_img_from_path(ASSETS_BASE_DIR, rqd.special_skill_info.skill_type_icon_path)

    # 处理事件横幅
    event_detail = None
    if rqd.event_info:
        event_detail = rqd.event_info

    # 处理卡池横幅
    gacha_detail = None
    if rqd.gacha_info:
        gacha_detail = rqd.gacha_info

    # 时间格式化
    release_time = datetime.fromtimestamp(card_info.release_at / 1000)

    # 样式定义
    title_style_def = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(0, 0, 0))
    label_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(50, 50, 50))
    text_style = TextStyle(font=DEFAULT_FONT, size=24, color=(70, 70, 70))
    small_style = TextStyle(font=DEFAULT_FONT, size=18, color=(70, 70, 70))
    tip_style = TextStyle(font=DEFAULT_FONT, size=18, color=(0, 0, 0))

    # 使用传入的背景图片，如果没有则使用默认蓝色背景
    if rqd.background_image_path:
        try:
            bg_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.background_image_path)
            bg = ImageBg(bg_img)
        except FileNotFoundError:
            bg = SEKAI_BLUE_BG
    else:
        bg = SEKAI_BLUE_BG

    with Canvas(bg=bg).set_padding(BG_PADDING) as canvas:
        with HSplit().set_sep(16).set_content_align('lt').set_item_align('lt'):
            # 左侧: 卡面+关联活动+关联卡池+提示
            with VSplit().set_padding(0).set_sep(16).set_content_align('lt').set_item_align('lt').set_item_bg(roundrect_bg(alpha=80)):
                # 卡面
                with VSplit().set_padding(16).set_sep(8).set_content_align('lt').set_item_align('lt'):
                    for img in card_images:
                        ImageBox(img, size=(500, None))

                # 关联活动
                if event_detail:
                    with VSplit().set_padding(16).set_sep(12).set_content_align('lt').set_item_align('lt'):
                        with HSplit().set_padding(0).set_sep(8).set_content_align('l').set_item_align('l'):
                            TextBox("当期活动", label_style)
                            TextBox(f"【{event_detail.event_id}】{event_detail.event_name}", small_style).set_w(360)
                        with HSplit().set_padding(0).set_sep(8).set_content_align('lt').set_item_align('lt'):
                            ImageBox(await get_img_from_path(ASSETS_BASE_DIR, event_detail.event_banner_path), size=(250, None))
                            with VSplit().set_content_align('c').set_item_align('c').set_sep(6):
                                TextBox(f"开始时间: {event_detail.start_time.strftime('%Y-%m-%d %H:%M')}", small_style)
                                TextBox(f"结束时间: {event_detail.end_time.strftime('%Y-%m-%d %H:%M')}", small_style)
                                Spacer(h=4)
                                with HSplit().set_padding(0).set_sep(8).set_content_align('l').set_item_align('l'):
                                    # 属性、团队、角色图标
                                    if event_detail.bonus_attr and rqd.event_attr_icon_path:
                                        ImageBox(await get_img_from_path(ASSETS_BASE_DIR, rqd.event_attr_icon_path), size=(32, None))
                                    if event_detail.unit and rqd.event_unit_icon_path:
                                        ImageBox(await get_img_from_path(ASSETS_BASE_DIR, rqd.event_unit_icon_path), size=(32, None))
                                    if event_detail.banner_cid and rqd.event_chara_icon_path:
                                        ImageBox(await get_img_from_path(ASSETS_BASE_DIR, rqd.event_chara_icon_path), size=(32, None))

                # 关联卡池
                if gacha_detail:
                    with VSplit().set_padding(16).set_sep(12).set_content_align('lt').set_item_align('lt'):
                        with HSplit().set_padding(0).set_sep(8).set_content_align('l').set_item_align('l'):
                            TextBox("当期卡池", label_style)
                            TextBox(f"【{gacha_detail.gacha_id}】{gacha_detail.gacha_name}", small_style).set_w(360)
                        with HSplit().set_padding(0).set_sep(8).set_content_align('lt').set_item_align('lt'):
                            ImageBox(await get_img_from_path(ASSETS_BASE_DIR, gacha_detail.gacha_banner_path), size=(250, None))
                            with VSplit().set_content_align('c').set_item_align('c').set_sep(6):
                                TextBox(f"开始时间: {gacha_detail.start_time.strftime('%Y-%m-%d %H:%M')}", small_style)
                                TextBox(f"结束时间: {gacha_detail.end_time.strftime('%Y-%m-%d %H:%M')}", small_style)

            # 右侧: 标题+限定类型+综合力+技能+发布时间+缩略图+衣装
            w = 600
            with VSplit().set_padding(0).set_sep(16).set_content_align('lt').set_item_align('lt').set_item_bg(roundrect_bg(alpha=80)):
                # 标题
                with HSplit().set_padding(16).set_sep(32).set_content_align('c').set_item_align('c').set_w(w):
                    ImageBox(unit_logo, size=(None, 64))
                    with VSplit().set_content_align('c').set_item_align('c').set_sep(12):
                        TextBox(card_info.prefix, title_style_def).set_w(w - 260).set_content_align('c')
                        with HSplit().set_content_align('c').set_item_align('c').set_sep(8):
                            ImageBox(character_icon, size=(None, 32))
                            TextBox(card_info.character_name, title_style_def)

                with VSplit().set_padding(16).set_sep(8).set_item_bg(roundrect_bg(alpha=80)).set_content_align('l').set_item_align('l'):
                    # 卡牌ID 限定类型
                    with HSplit().set_padding(16).set_sep(8).set_content_align('l').set_item_align('l'):
                        TextBox("ID", label_style)
                        TextBox(f"{card_info.id} ({region.upper()})", text_style)
                        Spacer(w=32)
                        TextBox("限定类型", label_style)
                        TextBox(card_info.supply_type, text_style)

                    # 综合力
                    with HSplit().set_padding(16).set_sep(8).set_content_align('lb').set_item_align('lb'):
                        TextBox("综合力", label_style)
                        TextBox(f"{power_info.power_total} ({power_info.power1}/{power_info.power2}/{power_info.power3}) (满级0破无剧情)", text_style)

                    # 技能
                    with VSplit().set_padding(16).set_sep(8).set_content_align('l').set_item_align('l'):
                        with HSplit().set_padding(0).set_sep(8).set_content_align('l').set_item_align('l'):
                            TextBox("技能", label_style)
                            if skill_type_icon:
                                ImageBox(skill_type_icon, size=(32, 32))
                            TextBox(skill_info.skill_name, text_style).set_w(w - 24 * 2 - 32 - 16)
                        TextBox(skill_info.skill_detail, text_style, use_real_line_count=True).set_w(w)
                        if skill_info.skill_detail_cn:
                            TextBox(skill_info.skill_detail_cn.removesuffix("。"), text_style, use_real_line_count=True).set_w(w)

                    # 特训技能
                    if rqd.special_skill_info:
                        with VSplit().set_padding(16).set_sep(8).set_content_align('l').set_item_align('l'):
                            with HSplit().set_padding(0).set_sep(8).set_content_align('l').set_item_align('l'):
                                TextBox("特训后技能", label_style)
                                if sp_skill_type_icon:
                                    ImageBox(sp_skill_type_icon, size=(32, 32))
                                TextBox(rqd.special_skill_info.skill_name, text_style).set_w(w - 24 * 5 - 32 - 16)
                            TextBox(rqd.special_skill_info.skill_detail, text_style, use_real_line_count=True).set_w(w)
                            if rqd.special_skill_info.skill_detail_cn:
                                TextBox(rqd.special_skill_info.skill_detail_cn.removesuffix("。"), text_style, use_real_line_count=True).set_w(w)

                    # 发布时间
                    with HSplit().set_padding(16).set_sep(8).set_content_align('lb').set_item_align('lb'):
                        TextBox("发布时间", label_style)
                        TextBox(release_time.strftime("%Y-%m-%d %H:%M:%S"), text_style)

                    # 缩略图
                    with HSplit().set_padding(16).set_sep(16).set_content_align('l').set_item_align('l'):
                        TextBox("缩略图", label_style)
                        for img in thumbnail_images:
                            ImageBox(img, size=(100, None))

                    # 衣装
                    if len(costume_images) > 0:
                        with HSplit().set_padding(16).set_sep(16).set_content_align('l').set_item_align('l'):
                            TextBox("衣装", label_style)
                            with Grid(col_count=5).set_sep(8, 8):
                                for img in costume_images:
                                    ImageBox(img, size=(80, None))

                    # 提示
                    with VSplit().set_padding(12).set_sep(6).set_content_align('l').set_item_align('l'):
                        TextBox(f"发送\"/查卡面 {card_info.id}\"获取卡面原图, 发送\"/卡面剧情 {card_info.id}\"获取AI剧情总结", tip_style)

    add_watermark(canvas)
    return await canvas.get_img()


async def compose_card_list_image(rqd: CardListRequest, title: str = None, title_style: TextStyle = None, title_shadow: bool = False):
    """
    合成卡牌列表图片
    """
    cards = rqd.cards
    region = rqd.region
    user_info = rqd.user_info
    # 如果只有一张卡，调用详情函数

    # 创建用户卡牌ID到卡牌信息的映射
    user_card_map = {}
    if user_info and user_info.user_cards:
        for user_card in user_info.user_cards:
            if isinstance(user_card, dict) and 'cardId' in user_card:
                user_card_map[user_card['cardId']] = user_card

    thumbs = []
    for card in rqd.cards:
        if card.after_training:
            thumb_img = await get_card_full_thumbnail(card.thumbnail_info[1])
        else:
            thumb_img = await get_card_full_thumbnail(card.thumbnail_info[0])
        thumbs.append(thumb_img)

    # 并行获取所有缩略图
    card_and_thumbs = [(card, thumb) for card, thumb in zip(cards, thumbs) if thumb is not None]

    # 按发布时间和ID排序
    card_and_thumbs.sort(key=lambda x: (x[0].release_at, x[0].id), reverse=True)

    # 样式定义
    name_style = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(0, 0, 0))
    id_style = TextStyle(font=DEFAULT_FONT, size=20, color=(0, 0, 0))
    leak_style = TextStyle(font=DEFAULT_BOLD_FONT, size=20, color=(200, 0, 0))

    # 使用传入的背景图片，如果没有则使用默认背景
    if rqd.background_image_path:
        try:
            bg_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.background_image_path)
            bg = ImageBg(bg_img)
        except FileNotFoundError:
            bg = SEKAI_BLUE_BG
    else:
        bg = SEKAI_BLUE_BG

    with Canvas(bg=bg).set_padding(BG_PADDING) as canvas:
        with VSplit().set_sep(16).set_content_align('lt').set_item_align('lt'):
            # 卡牌网格
            with Grid(col_count=3).set_bg(roundrect_bg(alpha=80)).set_padding(16):
                for i, (card, thumb) in enumerate(card_and_thumbs):
                    # 背景设置 - 确保毛玻璃效果启用
                    if card.supply_type not in ["非限定", "normal"]:
                        # 限定卡牌：使用淡黄色背景，确保有足够的透明度
                        bg = roundrect_bg(fill=(255, 250, 220, 200), blur_glass=True)
                    else:
                        # 普通卡牌：使用默认的半透明白色背景
                        bg = roundrect_bg(alpha=80)  # 默认已经是半透明+毛玻璃效果

                    with Frame().set_content_align('lb').set_bg(bg):
                        # 检查是否为未来卡牌
                        release_time = datetime.fromtimestamp(card.release_at / 1000)
                        if release_time > datetime.now():
                            TextBox("LEAK", leak_style).set_offset((4, -4))

                        # 技能图标区域
                        with Frame().set_content_align('rb'):
                            # 根据skill_type自动匹配技能图标
                            if card.skill.skill_type:
                                skill_icon_path = card.skill.skill_type_icon_path
                                try:
                                    skill_img = await get_img_from_path(ASSETS_BASE_DIR, skill_icon_path)
                                    ImageBox(skill_img, image_size_mode='fit').set_w(32).set_margin(8)
                                except FileNotFoundError:
                                    # 如果找不到对应的技能图标，静默跳过
                                    pass

                            # 卡牌信息区域
                            with VSplit().set_content_align('c').set_item_align('c').set_sep(5).set_padding(8):
                                GW = 300
                                with HSplit().set_content_align('c').set_w(GW).set_padding(8).set_sep(16):
                                        ImageBox(thumb, size=(100, 100), image_size_mode='fill')

                                # 卡牌名称
                                name_text = card.prefix
                                TextBox(name_text, name_style).set_w(GW).set_content_align('c')

                                # ID和限定类型
                                id_text = f"ID:{card.id}"
                                if card.supply_type not in ["非限定", "normal"]:
                                    id_text += f"【{card.supply_type}】"
                                TextBox(id_text, id_style).set_w(GW).set_content_align('c')

    add_watermark(canvas)
    return await canvas.get_img()

async def compose_box_image(rqd: CardBoxRequest, title: str = None, title_style: TextStyle = None, title_shadow: bool = False):
    """
    合成卡牌一览图片（按角色分类的卡牌收集册）
    """
    cards = rqd.cards
    region = rqd.region
    user_info = rqd.user_info
    show_id = rqd.show_id
    show_box = rqd.show_box

    thumbs = []
    for card in cards:
        # 根据use_after_training决定使用哪张缩略图
        if card.card.after_training:
            thumbs.append(await get_card_full_thumbnail(card.card.thumbnail_info[1]))
        else:
            thumbs.append(await get_card_full_thumbnail(card.card.thumbnail_info[0]))

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
            'img': img,
            'has': card.has_card  # 恢复拥有状态判断
        }

        # 如果只显示拥有卡牌且用户没有此卡，跳过
        if show_box and not card_data['has']:
            continue

        chara_cards[chara_id].append(card_data)

    # 按角色ID和稀有度排序
    chara_cards = list(chara_cards.items())
    chara_cards.sort(key=lambda x: x[0])
    for i in range(len(chara_cards)):
        chara_cards[i][1].sort(key=lambda x: (x['card']['card_rarity_type'], x['card']['release_at'], x['card']['id']))

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
        for chara_id, path in rqd.character_icon_paths.items():
            chara_icons[chara_id] = await get_img_from_path(ASSETS_BASE_DIR, path)
    # 绘制单张卡
    sz = 48
    def draw_card(card_data):
        with Frame().set_content_align('rt'):
            ImageBox(card_data['img'], size=(sz, sz))

            # 限定类型图标
            supply_name = card_data['card'].get('supply_type', '')
            if supply_name in ['期间限定', 'WL限定', '联动限定']:
                if term_img:
                    ImageBox(term_img, size=(int(sz*0.75), None))
            elif supply_name in ['Fes限定', 'BFes限定']:
                if fes_img:
                    ImageBox(fes_img, size=(int(sz*0.75), None))

            # 如果用户没有此卡牌，添加遮罩
            if not card_data['has'] and user_info:
                Spacer(w=sz, h=sz).set_bg(RoundRectBg(fill=(0,0,0,120), radius=2))

        if show_id:
            TextBox(f"{card_data['card']['id']}", TextStyle(font=DEFAULT_FONT, size=12, color=(0, 0, 0))).set_w(sz)

    # 使用传入的背景图片，如果没有则使用默认背景
    if rqd.background_image_path:
        try:
            bg_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.background_image_path)
            bg = ImageBg(bg_img)
        except FileNotFoundError:
            bg = SEKAI_BLUE_BG
    else:
        bg = SEKAI_BLUE_BG

    with Canvas(bg=bg).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(16):
            if user_info:
                user_profile = await get_detailed_profile_card(user_info)

            # 卡牌网格
            with HSplit().set_bg(roundrect_bg(alpha=80)).set_content_align('lt').set_item_align('lt').set_padding(16).set_sep(4):
                for chara_id, cards in chara_cards:
                    with VSplit().set_content_align('t').set_item_align('t').set_sep(4):
                        # 角色图标
                        chara_icon = chara_icons.get(chara_id)
                        ImageBox(chara_icon, size=(sz, sz))
                        chara_color = color_code_to_rgb(CHARACTER_COLOR_CODE[chara_id])
                        col_num = max(1, len(range(0, len(cards), best_height)))
                        Spacer(w=sz * col_num + 4 * (col_num - 1), h=4).set_bg(FillBg(chara_color))
                        # 卡牌列表
                        with HSplit().set_content_align('lt').set_item_align('lt').set_sep(4):
                            for i in range(0, len(cards), best_height):
                                with VSplit().set_content_align('lt').set_item_align('lt').set_sep(4):
                                    for card_data in cards[i:i + best_height]:
                                        draw_card(card_data)

    add_watermark(canvas)
    return await canvas.get_img()


