from datetime import datetime
from typing import List, Dict, Optional
from PIL import Image
from pydantic import BaseModel

from src.base.configs import ASSETS_BASE_DIR
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
)
from src.base.plot import Canvas, Grid, HSplit, ImageBox, Spacer, TextBox, TextStyle, VSplit
from src.base.utils import get_img_from_path, get_readable_timedelta, get_float_str, concat_images
from src.profile.drawer import get_card_full_thumbnail, CardFullThumbnailRequest

class GachaBehavior(BaseModel):
    type: str
    spin_count: int
    cost_type: Optional[str] = None
    cost_icon_path: Optional[str] = None  # 消耗资源图标路径
    cost_quantity: Optional[int] = None
    execute_limit: Optional[int] = None
    colorful_pass: bool = False

class GachaInfo(BaseModel):
    id: int
    name: str
    gachaType: str  # 直接使用原版字段名
    summary: str = ""
    desc: str = ""
    startAt: int
    endAt: int
    asset_name: str
    ceil_item_img_path: Optional[str] = None   # 天井交换物品图片路径
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

class GachaCardWeightInfo(BaseModel):
    id: int
    rarity: str
    rate: float = 0.0                    # 直接传入的概率值 (0-1)
    thumbnail_request: CardFullThumbnailRequest

class GachaWeightInfo(BaseModel):
    # 各稀有度最终显示概率 (必需，外部计算后的总概率)
    rarity_1_rate: Optional[float] = 0.0                # 1星总概率 (0-1)
    rarity_2_rate: Optional[float] = 0.0                # 2星总概率 (0-1)
    rarity_3_rate: Optional[float] = 0.0                # 3星总概率 (0-1)
    rarity_4_rate: Optional[float] = 0.0                # 4星总概率 (0-1)
    rarity_birthday_rate: Optional[float] = 0.0         # 生日卡总概率 (0-1)
    guaranteed_rates: Dict[str, float] = {}   # 各稀有度的保底概率

class GachaListRequest(BaseModel):
    gachas: List[GachaListInfo]   # 简化的卡池数据（仅列表显示所需）
    page_size: int = 20            # 每页显示数量
    region: str = "jp"             # 服务器地区
    gacha_logos: Dict[int, str] = {} # 卡池ID对应的logo图片路径

class GachaDetailRequest(BaseModel):
    gacha: GachaInfo
    weight_info: GachaWeightInfo
    pickup_cards: List[GachaCardWeightInfo] = []
    logo_img_path: Optional[str] = None      # logo图片路径
    banner_img_path: Optional[str] = None   # banner图片路径
    bg_img_path: Optional[str] = None       # 背景图片路径
    region: str = "jp"


async def get_rarity_img(rarity: str, rarity_img_path: str, birthday_img_path: Optional[str]) -> Optional[Image.Image]:
    """获取稀有度图片"""
    if rarity == "rarity_birthday":
        rare_img = await get_img_from_path(ASSETS_BASE_DIR, birthday_img_path)
        rare_num = 1
    else:
        rare_img = await get_img_from_path(ASSETS_BASE_DIR, rarity_img_path)
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

# 保底行为类型映射
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

# ======================= Drawing Functions ======================= #

async def compose_gacha_list_image(rqd: GachaListRequest) -> Image.Image:
    """合成卡池一览图片"""
    gachas = []

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
                    now = datetime.now()
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
                            TextBox(f"S {g.startAt.strftime('%Y-%m-%d %H:%M')}", style2)
                            TextBox(f"T {g.endAt.strftime('%Y-%m-%d %H:%M')}", style2)

    add_watermark(canvas)
    return await canvas.get_img()


async def compose_gacha_detail_image(rqd: GachaDetailRequest) -> Image.Image:
    """合成卡池详情图片"""
    # 绘图
    title_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=BLACK)
    label_style = TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=(50, 50, 50))
    text_style = TextStyle(font=DEFAULT_FONT, size=24, color=(70, 70, 70))
    small_style = TextStyle(font=DEFAULT_FONT, size=12, color=(70, 70, 70))
    start_time = datetime.fromtimestamp(rqd.gacha.startAt / 1000)
    end_time = datetime.fromtimestamp(rqd.gacha.endAt / 1000)

    bg = SEKAI_BLUE_BG
    if rqd.bg_img_path:
        bg = await get_img_from_path(ASSETS_BASE_DIR, rqd.bg_img_path)

    with Canvas(bg=bg).set_padding(BG_PADDING) as canvas:
        with HSplit().set_sep(16).set_content_align('lt').set_item_align('lt'):
            w = 600
            with VSplit().set_padding(8).set_sep(8).set_content_align('c').set_item_align('c').set_item_bg(roundrect_bg(alpha=80)).set_bg(roundrect_bg(alpha=80)):
                # 标题
                with HSplit().set_padding(8).set_sep(32).set_content_align('c').set_item_align('c').set_omit_parent_bg(True):
                    if rqd.logo_img_path:
                        logo_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.logo_img_path)
                        ImageBox(logo_img, size=(None, 100))
                    if rqd.banner_img_path:
                        banner_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.banner_img_path)
                        ImageBox(banner_img, size=(None, 100))

                # 基本信息
                TextBox(rqd.gacha.name, title_style, use_real_line_count=True).set_w(w).set_padding(16).set_content_align('c')
                with HSplit().set_padding(16).set_sep(8).set_content_align('c').set_item_align('c'):
                    TextBox("ID", label_style)
                    TextBox(f"{rqd.gacha.id} ({rqd.region.upper()})", text_style)
                    Spacer(w=24)
                    TextBox("类型", label_style)
                    TextBox(GACHA_TYPE_NAMES.get(rqd.gacha.gachaType, rqd.gacha.gachaType), text_style)
                    if rqd.gacha.ceil_item_img_path:
                        Spacer(w=24)
                        TextBox("交换物品", label_style)
                        ceilitem_img = await get_img_from_path(ASSETS_BASE_DIR, rqd.gacha.ceil_item_img_path)
                        ImageBox(ceilitem_img, size=(None, 30))

                with VSplit().set_padding(16).set_sep(8).set_content_align('c').set_item_align('c'):
                    with HSplit().set_padding(0).set_sep(8).set_content_align('c').set_item_align('c'):
                        TextBox("开始时间", label_style)
                        TextBox(start_time.strftime("%Y-%m-%d %H:%M"), text_style)
                    with HSplit().set_padding(0).set_sep(8).set_content_align('c').set_item_align('c'):
                        TextBox("结束时间", label_style)
                        TextBox(end_time.strftime("%Y-%m-%d %H:%M"), text_style)
                    with HSplit().set_padding(0).set_sep(8).set_content_align('c').set_item_align('c'):
                        if start_time >= datetime.now():
                            TextBox("距离开始还有", label_style)
                            TextBox(get_readable_timedelta(end_time - datetime.now()), text_style)
                        elif end_time >= datetime.now():
                            TextBox("距离结束还有", label_style)
                            TextBox(get_readable_timedelta(end_time - datetime.now()), text_style)
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
                                    full_thumb = await get_card_full_thumbnail(card.thumbnail_request)

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




