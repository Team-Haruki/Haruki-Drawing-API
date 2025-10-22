from datetime import datetime, timezone, timedelta
from typing import Any, List, Dict, Optional, Union
from dataclasses import dataclass, field
from PIL import Image
from pydantic import BaseModel, Field, ConfigDict

from src.base.configs import ASSETS_BASE_DIR
# from src.base.img_utils import UNKNOWN_IMG  # 使用本地创建的占位图片
from src.base.draw import (
    BG_PADDING,
    SEKAI_BLUE_BG,
    add_watermark,
    roundrect_bg,
)
from src.base.painter import (
    DEFAULT_BOLD_FONT,
    DEFAULT_HEAVY_FONT,
    DEFAULT_FONT,
    BLACK,
    WHITE,
)
from src.base.plot import Canvas, Grid, HSplit, ImageBox, ImageBg, Spacer, TextBox, TextStyle, VSplit
from src.base.utils import get_img_from_path, get_readable_timedelta
from src.profile.drawer import get_card_full_thumbnail, CardFullThumbnailRequest


# ======================= Utility Functions ======================= #

def to_beijing_time(dt: datetime) -> datetime:
    """
    将UTC时间转换为北京时间 (UTC+8)
    """
    if dt.tzinfo is None:
        # 如果没有时区信息，假设为UTC时间
        dt = dt.replace(tzinfo=timezone.utc)
    # 转换为北京时间 (UTC+8)
    beijing_tz = timezone(timedelta(hours=8))
    return dt.astimezone(beijing_tz)


# ======================= Data Models ======================= #

class GachaBehavior(BaseModel):
    type: str
    spin_count: int
    cost_type: Optional[str] = None
    cost_icon_path: Optional[str] = None  # 消耗资源图标路径
    cost_quantity: Optional[int] = None
    execute_limit: Optional[int] = None
    colorful_pass: bool = False

class GachaCard(BaseModel):
    id: int
    rarity: str                    # 直接包含稀有度信息，避免内部查询
    is_wish: bool = False
    is_pickup: bool = False

class GachaCardRarityRate(BaseModel):
    rarity: str
    rate: int
    lottery_type: str

class GachaInfo(BaseModel):
    id: int
    name: str
    gachaType: str  # 直接使用原版字段名
    summary: str = ""
    desc: str = ""
    startAt: datetime  # 直接使用原版字段名，datetime支持多种格式包括时间戳字符串
    endAt: datetime      # 直接使用原版字段名，datetime支持多种格式包括时间戳字符串
    asset_name: str
    ceilitem_img: Optional[str] = None   # 天井交换物品图片路径
    behaviors: List[GachaBehavior] = []

    # 直接传入的稀有度统计信息
    rarity_1_count: int = 0           # 1星卡数量
    rarity_2_count: int = 0           # 2星卡数量
    rarity_3_count: int = 0           # 3星卡数量
    rarity_4_count: int = 0           # 4星卡数量
    rarity_birthday_count: int = 0    # 生日卡数量
    pickup_count: int = 0             # UP卡数量

class GachaListInfo(BaseModel):
    id: int
    name: str
    gachaType: str
    startAt: datetime
    endAt: datetime
    asset_name: str  # 资源名称标识

class GachaFilter(BaseModel):
    page: Optional[int] = None
    year: Optional[int] = None
    card_id: Optional[int] = None
    is_rerelease: bool = False #复刻
    is_recall: bool = False #回响
    is_current: bool = False
    is_leak: bool = False #理科

class GachaCardThumbnailRequest(BaseModel):
    card_thumbnail_path: str      # 卡片基础图片路径
    rare: str                     # 稀有度
    frame_img_path: str           # 边框图片路径
    attr_img_path: str            # 属性图片路径
    rare_img_path: str            # 稀有度星星图片路径
    birthday_icon_path: Optional[str] = None  # 生日卡图标路径

class GachaCardWeightInfo(BaseModel):
    id: int
    rarity: str
    rate: float = 0.0                    # 直接传入的概率值 (0-1)
    thumbnail_request: GachaCardThumbnailRequest  # 缩略图生成参数 (必需)

class GachaWeightInfo(BaseModel):
    # 各稀有度最终显示概率 (必需，外部计算后的总概率)
    rarity_1_rate: float = 0.0                # 1星总概率 (0-1)
    rarity_2_rate: float = 0.0                # 2星总概率 (0-1)
    rarity_3_rate: float = 0.0                # 3星总概率 (0-1)
    rarity_4_rate: float = 0.0                # 4星总概率 (0-1)
    rarity_birthday_rate: float = 0.0         # 生日卡总概率 (0-1)

    # 保底系统相关 (外部传入)
    guaranteed_rates: Dict[str, float] = {}   # 各稀有度的保底概率

class GachaListRequest(BaseModel):
    gachas: List[GachaListInfo]   # 简化的卡池数据（仅列表显示所需）
    filter: GachaFilter = GachaFilter()  # 过滤条件
    page_size: int = 20            # 每页显示数量
    region: str = "jp"             # 服务器地区
    gacha_logos: Dict[int, str] = {} # 卡池ID对应的logo图片路径

class GachaDetailRequest(BaseModel):
    gacha: GachaInfo
    weight_info: GachaWeightInfo
    pickup_cards: List[GachaCardWeightInfo] = []
    logo_img: Optional[str] = None      # logo图片路径
    banner_img: Optional[str] = None   # banner图片路径
    bg_img: Optional[str] = None       # 背景图片路径
    region: str = "jp"



# ======================= Utility Functions ======================= #

def get_float_str(value: float, precision: int = 2) -> str:
    """格式化浮点数"""
    format_str = f"{{0:.{precision}f}}".format(value)
    if '.' in format_str:
        format_str = format_str.rstrip('0').rstrip('.')
    return format_str

async def run_in_pool(func, *args, **kwargs):
    """在线程池中运行函数的简化版本"""
    import asyncio
    import concurrent.futures

    loop = asyncio.get_event_loop()
    with concurrent.futures.ThreadPoolExecutor() as executor:
        return await loop.run_in_executor(executor, func, *args, **kwargs)

async def concat_images(images, direction='h'):
    """水平或垂直拼接图片"""
    if not images:
        return None

    # 过滤掉None值
    images = [img for img in images if img is not None]
    if not images:
        return None

    if direction == 'h':
        # 水平拼接
        total_width = sum(img.width for img in images)
        max_height = max(img.height for img in images)

        result = Image.new('RGBA', (total_width, max_height), (0, 0, 0, 0))
        x_offset = 0
        for img in images:
            result.paste(img, (x_offset, 0))
            x_offset += img.width
    else:
        # 垂直拼接
        max_width = max(img.width for img in images)
        total_height = sum(img.height for img in images)

        result = Image.new('RGBA', (max_width, total_height), (0, 0, 0, 0))
        y_offset = 0
        for img in images:
            result.paste(img, (0, y_offset))
            y_offset += img.height

    return result


async def get_rarity_img(rarity: str, static_imgs: Dict[str, Image.Image] = None) -> Optional[Image.Image]:
    """获取稀有度图片"""
    if static_imgs is None:
        static_imgs = {}

    if rarity == "rarity_birthday":
        rare_img = static_imgs.get(f"card/rare_birthday.png")
        if rare_img is None:
            rare_img = await get_img_from_path(ASSETS_BASE_DIR, "card/rare_birthday.png")
        rare_num = 1
    else:
        rare_img = static_imgs.get(f"card/rare_star_normal.png")
        if rare_img is None:
            rare_img = await get_img_from_path(ASSETS_BASE_DIR, "card/rare_star_normal.png")
        rare_num = int(rarity.split("_")[1])

    if rare_img:
        return await concat_images([rare_img] * rare_num, 'h')
    return None

# ======================= Constants ======================= #

GACHA_TYPE_NAMES = {
    'beginner': '新手',
    'normal': '一般',
    'ceil': '天井',
    'gift': '礼物',
}

# 保底行为类型映射 (基于lunabot实现)
GACHA_BEHAVIOR_NAMES = {
    'normal': '普通',
    'over_rarity_3_once': '保底3星',
    'over_rarity_4_once': '保底4星',
}

GACHA_RATE_RARITIES = ['rarity_1', 'rarity_2', 'rarity_3', 'rarity_4', 'rarity_birthday']

GACHA_RARE_NAMES = {
    'rarity_1': '1星',
    'rarity_2': '2星',
    'rarity_3': '3星',
    'rarity_4': '4星',
    'rarity_birthday': '生日',
    'pickup': '当期',
}

RERELEASE_KEYWORDS = ("[It's Back]", "[재등장]", "[复刻]", "[復刻]")
ECHO_KEYWORDS = ("[回响]",)


# ======================= Drawing Functions ======================= #

async def compose_gacha_list_image(rqd: GachaListRequest) -> Image.Image:
    """合成卡池一览图片"""
    gachas = []
    for gacha in rqd.gachas:
        g = gacha
        if rqd.filter.year and g.startAt.year != rqd.filter.year:
            continue
        if rqd.filter.is_rerelease and not g.name.startswith(RERELEASE_KEYWORDS):
            continue
        if rqd.filter.is_recall and not g.name.startswith(ECHO_KEYWORDS):
            continue
        if rqd.filter.is_current and not (g.startAt <= datetime.now(timezone.utc) <= g.endAt):
            continue
        if rqd.filter.is_leak:
            if g.startAt <= datetime.now(timezone.utc):
                continue
            else:
                if g.startAt > datetime.now(timezone.utc):
                    continue
        gachas.append(g)

    gachas.sort(key=lambda g: g.startAt)

    total_pages = 1
    page_size = rqd.page_size if rqd.page_size else 20
    if len(gachas) > 0:
        total_pages = (len(gachas) + page_size - 1) // page_size

    page = max(1, min(rqd.filter.page, total_pages)) if rqd.filter.page else total_pages
    start_index = (page - 1) * page_size
    gachas = gachas[start_index:start_index + page_size]

    row_count = (len(gachas) + 2) // 2  # 2 columns
    style1 = TextStyle(font=DEFAULT_HEAVY_FONT, size=10, color=(50, 50, 50))
    style2 = TextStyle(font=DEFAULT_FONT, size=10, color=(70, 70, 70))

    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_padding(0).set_sep(4).set_content_align('lt').set_item_align('lt'):
            TextBox(
                f"卡池按时间顺序排列，黄色为开放中卡池，当前为第 {page}/{total_pages} 页",
                TextStyle(font=DEFAULT_FONT, size=12, color=(0, 0, 100))
            ).set_bg(roundrect_bg(radius=4, alpha=80)).set_padding(4)
            with Grid(row_count=row_count, vertical=True).set_sep(8, 2).set_item_align('c').set_content_align('c'):
                for g in gachas:
                    now = datetime.now(timezone.utc)
                    bg_color = (255, 255, 255, 200)
                    if g.startAt <= now <= g.endAt:
                        bg_color = (255, 250, 220, 200)
                    elif now > g.endAt:
                        bg_color = (220, 220, 220, 200)
                    bg = roundrect_bg(bg_color, 5)
                    with HSplit().set_padding(4).set_sep(4).set_item_align('lt').set_content_align('lt').set_bg(bg):
                        with VSplit().set_padding(0).set_sep(2).set_item_align('lt').set_content_align('lt'):
                            # 处理logo图片
                            logo_data = rqd.gacha_logos.get(g.id)
                            if isinstance(logo_data, str):
                                logo_img = await get_img_from_path(ASSETS_BASE_DIR, logo_data)
                            elif isinstance(logo_data, Image.Image):
                                logo_img = logo_data
                            else:
                                raise ValueError(f"Logo image not found for gacha {g.id}")

                            ImageBox(logo_img, size=(None, 60))
                            TextBox(f"【{g.id}】{g.name}", style1, line_count=2, use_real_line_count=False).set_w(130)
                            TextBox(f"S {to_beijing_time(g.startAt).strftime('%Y-%m-%d %H:%M')}", style2)
                            TextBox(f"T {to_beijing_time(g.endAt).strftime('%Y-%m-%d %H:%M')}", style2)

    add_watermark(canvas)
    return await canvas.get_img()


async def compose_gacha_detail_image(rqd: GachaDetailRequest) -> Image.Image:
    """合成卡池详情图片"""
    # 绘图
    title_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK)
    label_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(50, 50, 50))
    text_style = TextStyle(font=DEFAULT_FONT, size=24, color=(70, 70, 70))
    small_style = TextStyle(font=DEFAULT_FONT, size=12, color=(70, 70, 70))
    tip_style = TextStyle(font=DEFAULT_FONT, size=18, color=(0, 0, 0))

    bg = SEKAI_BLUE_BG
    if rqd.bg_img:
        bg = await get_img_from_path(ASSETS_BASE_DIR, rqd.bg_img)

    with Canvas(bg=bg).set_padding(BG_PADDING) as canvas:
        with HSplit().set_sep(16).set_content_align('lt').set_item_align('lt'):
            w = 600
            with VSplit().set_padding(8).set_sep(8).set_content_align('c').set_item_align('c').set_item_bg(roundrect_bg(alpha=80)).set_bg(roundrect_bg(alpha=80)):
                # 标题
                with HSplit().set_padding(8).set_sep(32).set_content_align('c').set_item_align('c').set_omit_parent_bg(True):
                    if rqd.logo_img:
                        logo_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.logo_img)
                        ImageBox(logo_img, size=(None, 100))
                    if rqd.banner_img:
                        banner_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.banner_img)
                        ImageBox(banner_img, size=(None, 100))

                # 基本信息
                TextBox(rqd.gacha.name, title_style, use_real_line_count=True).set_w(w).set_padding(16).set_content_align('c')
                with HSplit().set_padding(16).set_sep(8).set_content_align('c').set_item_align('c'):
                    TextBox("ID", label_style)
                    TextBox(f"{rqd.gacha.id} ({rqd.region.upper()})", text_style)
                    Spacer(w=24)
                    TextBox("类型", label_style)
                    TextBox(GACHA_TYPE_NAMES.get(rqd.gacha.gachaType, rqd.gacha.gachaType), text_style)
                    if rqd.gacha.ceilitem_img:
                        Spacer(w=24)
                        TextBox("交换物品", label_style)
                        ceilitem_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.gacha.ceilitem_img)
                        ImageBox(ceilitem_img, size=(None, 30))

                with VSplit().set_padding(16).set_sep(8).set_content_align('c').set_item_align('c'):
                    with HSplit().set_padding(0).set_sep(8).set_content_align('c').set_item_align('c'):
                        TextBox("开始时间", label_style)
                        TextBox(to_beijing_time(rqd.gacha.startAt).strftime("%Y-%m-%d %H:%M"), text_style)
                    with HSplit().set_padding(0).set_sep(8).set_content_align('c').set_item_align('c'):
                        TextBox("结束时间", label_style)
                        TextBox(to_beijing_time(rqd.gacha.endAt).strftime("%Y-%m-%d %H:%M"), text_style)
                    with HSplit().set_padding(0).set_sep(8).set_content_align('c').set_item_align('c'):
                        if rqd.gacha.startAt >= datetime.now(timezone.utc):
                            TextBox("距离开始还有", label_style)
                            TextBox(get_readable_timedelta(rqd.gacha.startAt - datetime.now(timezone.utc)), text_style)
                        elif rqd.gacha.endAt >= datetime.now(timezone.utc):
                            TextBox("距离结束还有", label_style)
                            TextBox(get_readable_timedelta(rqd.gacha.endAt - datetime.now(timezone.utc)), text_style)
                        else:
                            TextBox("卡池已结束", label_style)

                # 抽卡消耗
                with VSplit().set_padding(16).set_sep(16).set_content_align('c').set_item_align('c'):
                    # 合并相同类型不同消耗
                    behaviors: Dict[str, List[GachaBehavior]] = {}
                    for behavior in rqd.gacha.behaviors:
                        text = GACHA_BEHAVIOR_NAMES.get(behavior.type, "未知")
                        match behavior.type:
                            case 'once_a_day': text = "每日"
                            case 'once_a_week': text = "每周"
                        if behavior.spin_count == 1:    text += "/单抽"
                        elif behavior.spin_count == 10: text += "/十连"
                        if behavior.colorful_pass:  text = "月卡" + text
                        if behavior.execute_limit:
                            text += f"(限{behavior.execute_limit}次)"
                        behaviors.setdefault(text, []).append(behavior)
                    with Grid(col_count=2).set_padding(0).set_sep(8, 8).set_content_align('l').set_item_align('l'):
                        for text, behavior_list in behaviors.items():
                            TextBox(text, label_style)
                            with HSplit().set_padding(0).set_sep(8).set_content_align('l').set_item_align('l'):
                                for i, behavior in enumerate(behavior_list):
                                    if i > 0:
                                        TextBox(" / ", text_style)
                                    if behavior.cost_type:
                                        if behavior.cost_icon_path:
                                            cost_icon = await get_img_from_path(ASSETS_BASE_DIR, behavior.cost_icon_path)
                                            ImageBox(cost_icon, size=(None, 48))
                                        if "paid" in behavior.cost_type:
                                            TextBox("(付费)", text_style)
                                        if behavior.cost_quantity and behavior.cost_quantity > 1:
                                            TextBox(f"x{behavior.cost_quantity}", text_style)
                                    else:
                                        TextBox("免费", text_style)

                # 当期卡牌
                if rqd.pickup_cards:
                    with HSplit().set_padding(16).set_sep(16).set_content_align('c').set_item_align('c'):
                        TextBox("当期卡片", label_style)
                        with Grid(col_count=min(5, len(rqd.pickup_cards))).set_padding(0).set_sep(8, 8).set_content_align('c').set_item_align('c'):
                            card_size = 80  
                            for card in rqd.pickup_cards:
                                with VSplit().set_padding(0).set_sep(1).set_content_align('c').set_item_align('c'):
                                    profile_request = CardFullThumbnailRequest(
                                        id=card.id,
                                        card_thumbnail_path=card.thumbnail_request.card_thumbnail_path,
                                        rare=card.thumbnail_request.rare,
                                        frame_img_path=card.thumbnail_request.frame_img_path,
                                        attr_img_path=card.thumbnail_request.attr_img_path,
                                        rare_img_path=card.thumbnail_request.rare_img_path,
                                        birthday_icon_path=card.thumbnail_request.birthday_icon_path,
                                        train_rank=None,
                                        train_rank_img_path=None,
                                        level=None,
                                        after_training=False,
                                        custom_text=None,
                                        card_level=None,
                                        is_pcard=False
                                    )
                                    full_thumb = await get_card_full_thumbnail(profile_request)

                                    ImageBox(full_thumb, size=(card_size, card_size))
                                    TextBox(f"{card.id} ({get_float_str(card.rate * 100, 4)}%)", small_style)

                # 抽卡概率
                with VSplit().set_padding(16).set_sep(8).set_content_align('c').set_item_align('c'):
                    with Grid(col_count=2).set_padding(0).set_sep(8, 8).set_content_align('l').set_item_align('l'):
                        if rqd.pickup_cards:
                            with HSplit().set_padding(0).set_sep(8).set_content_align('l').set_item_align('l'):
                                TextBox("当期", label_style)
                                TextBox(f"({len(rqd.pickup_cards)})", text_style)

                            # 计算并显示UP卡总概率 (包含保底概率)
                            pickup_total_rate = sum(card.rate for card in rqd.pickup_cards)
                            pickup_rate_text = f"{get_float_str(pickup_total_rate * 100, 4)}%"

                            # 检查4星是否有保底概率，如果有则计算UP卡的保底概率
                            guaranteed_4star_rate = rqd.weight_info.guaranteed_rates.get("rarity_4", 0.0)
                            if guaranteed_4star_rate > 0 and pickup_total_rate > 0:
                                # 按比例计算UP卡在保底中的概率
                                normal_4star_rate = rqd.weight_info.rarity_4_rate
                                if normal_4star_rate > 0:
                                    pickup_guaranteed_rate = guaranteed_4star_rate * (pickup_total_rate / normal_4star_rate)
                                    pickup_guaranteed_text = f"{get_float_str(pickup_guaranteed_rate * 100, 4)}%"
                                    pickup_rate_text = f"{pickup_rate_text} / {pickup_guaranteed_text} (保底)"

                            TextBox(pickup_rate_text, text_style)

                        # 显示各稀有度概率
                        for rarity in GACHA_RATE_RARITIES:
                            rate = getattr(rqd.weight_info, f"{rarity}_rate", 0.0)
                            if rate == 0.0: continue

                            # 获取该稀有度的卡牌数量
                            count = getattr(rqd.gacha, f"{rarity}_count", 0)
                            rarity_name = GACHA_RARE_NAMES.get(rarity, rarity.replace('rarity_', ''))

                            if count > 0:
                                # 显示稀有度名称和数量
                                with HSplit().set_padding(0).set_sep(8).set_content_align('l').set_item_align('l'):
                                    # 获取稀有度图片
                                    rarity_img = await get_rarity_img(rarity)
                                    if rarity_img:
                                        ImageBox(rarity_img, size=(None, 24))
                                    else:
                                        TextBox(rarity_name, label_style)

                                    TextBox(f"({count})", text_style)

                                # 显示概率
                                normal_rate_text = f"{get_float_str(rate * 100, 4)}%"

                                guaranteed_rate = rqd.weight_info.guaranteed_rates.get(rarity, 0.0)
                                if guaranteed_rate > 0:
                                    guaranteed_rate_text = f"{get_float_str(guaranteed_rate * 100, 4)}%"
                                    rate_text = f"{normal_rate_text} / {guaranteed_rate_text} (保底)"
                                else:
                                    rate_text = normal_rate_text

                                TextBox(rate_text, text_style)
                            else:
                                with HSplit().set_padding(0).set_sep(8).set_content_align('l').set_item_align('l'):
                                    rarity_img = await get_rarity_img(rarity)
                                    if rarity_img:
                                        ImageBox(rarity_img, size=(None, 24))
                                    else:
                                        TextBox(rarity_name, label_style)

                                # 显示概率 (包含保底概率)
                                normal_rate_text = f"{get_float_str(rate * 100, 4)}%"

                                # 检查是否有外部传入的保底概率
                                guaranteed_rate = rqd.weight_info.guaranteed_rates.get(rarity, 0.0)
                                if guaranteed_rate > 0:
                                    # 显示普通概率和保底概率
                                    guaranteed_rate_text = f"{get_float_str(guaranteed_rate * 100, 4)}%"
                                    rate_text = f"{normal_rate_text} / {guaranteed_rate_text} (保底)"
                                else:
                                    # 只显示普通概率
                                    rate_text = normal_rate_text

                                TextBox(rate_text, text_style)

    add_watermark(canvas)
    return await canvas.get_img()




