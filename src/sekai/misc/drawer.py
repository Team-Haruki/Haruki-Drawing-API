from PIL import Image

from src.sekai.base.configs import DEFAULT_BOLD_FONT, DEFAULT_FONT, DEFAULT_HEAVY_FONT, ASSETS_BASE_DIR
from src.sekai.base.utils import get_img_from_path
from src.sekai.base.painter import color_code_to_rgb, ADAPTIVE_WB
from src.sekai.base.draw import (
    TextBox,
    Canvas,
    BG_PADDING,
    roundrect_bg,
    add_watermark,
)
from src.sekai.base.plot import (
    VSplit,
    HSplit,
    Grid,
    ImageBox,
    TextStyle,
    Spacer,
    ImageBg,
    RoundRectBg,
)

# =========================== 从.model导入数据类型 =========================== #

from .model import *

# =========================== 颜色常量 =========================== #

BLACK = (0, 0, 0, 255)


async def compose_chara_birthday_image(
    rqd: CharaBirthdayRequest
) -> Image.Image:
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

    # 加载图片
    card_image = await get_img_from_path(ASSETS_BASE_DIR, rqd.card_image_path)
    sd_image = await get_img_from_path(ASSETS_BASE_DIR, rqd.sd_image_path)
    title_image = await get_img_from_path(ASSETS_BASE_DIR, rqd.title_image_path)
    card_thumbs = [await get_img_from_path(ASSETS_BASE_DIR, card.thumbnail_path) for card in cards]

    # 绘制时间范围的辅助函数
    def draw_time_range(label: str, tr: BirthdayEventTime):
        with HSplit().set_sep(8).set_content_align('l').set_item_align('l'):
            TextBox(f"{label} ", style1)
            TextBox(f"{tr.start_text} ~ {tr.end_text}", style2)

    with Canvas(bg=ImageBg(card_image)).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('c').set_item_align('c').set_padding(16).set_sep(8) \
            .set_item_bg(roundrect_bg()).set_bg(roundrect_bg()):
        
            # 角色信息头部
            with HSplit().set_sep(16).set_padding(16).set_content_align('c').set_item_align('c'):
                ImageBox(sd_image, size=(None, 80), shadow=True)
                ImageBox(title_image, size=(None, 60))
                TextBox(f"{month}月{day}日", 
                        TextStyle(DEFAULT_HEAVY_FONT, 32, (100, 100, 100), 
                                  use_shadow=True, shadow_offset=2, shadow_color=tuple(color_code_to_rgb(color_code))))

            # 基本信息
            with VSplit().set_sep(4).set_padding(16).set_content_align('l').set_item_align('l'):
                with HSplit().set_sep(8).set_padding(0).set_content_align('l').set_item_align('l'):
                    TextBox(f"({region_name}) 距离下次生日还有{days_until_birthday}天", style1)
                    Spacer(w=16)
                    TextBox(f"应援色", style1)
                    TextBox(color_code, TextStyle(DEFAULT_FONT, 20, ADAPTIVE_WB)) \
                        .set_bg(RoundRectBg(tuple(color_code_to_rgb(color_code)), radius=4)).set_padding(8)

                # 时间范围 - 固定绘制
                draw_time_range("🎰卡池开放时间", rqd.gacha_time)
                draw_time_range("🎤虚拟LIVE时间", rqd.live_time)

            # 五周年特殊时间范围
            if is_fifth_anniv:
                with VSplit().set_sep(4).set_padding(16).set_content_align('l').set_item_align('l'):
                    if rqd.drop_time:
                        draw_time_range("💧露滴掉落时间", rqd.drop_time)
                    if rqd.flower_time:
                        draw_time_range("🌱浇水开放时间", rqd.flower_time)
                    if rqd.party_time:
                        draw_time_range("🎂派对开放时间", rqd.party_time)

            # 卡牌列表
            with HSplit().set_sep(4).set_padding(16).set_content_align('l').set_item_align('l'):
                TextBox(f"卡牌", style1)
                Spacer(w=8)
                with Grid(col_count=6).set_sep(4, 4):
                    for i, thumb in enumerate(card_thumbs):
                        with VSplit().set_sep(2).set_content_align('c').set_item_align('c'):
                            ImageBox(thumb, size=(80, 80), shadow=True)
                            TextBox(f"{cards[i].id}", TextStyle(DEFAULT_FONT, 16, (50, 50, 50)))
                
            # 底部角色生日日历
            with Grid(col_count=13).set_sep(2, 2).set_padding(16).set_content_align('c').set_item_align('c'):
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
                    
                    with VSplit().set_sep(0).set_content_align('c').set_item_align('c'):
                        # 使用model中传入的icon_path
                        chara_icon = await get_img_from_path(ASSETS_BASE_DIR, chara.icon_path)
                            
                        b = ImageBox(chara_icon, size=(40, 40)).set_padding(4)
                        if chara.cid == cid:
                            b.set_bg(roundrect_bg(radius=8))
                        TextBox(f"{chara.month}/{chara.day}", TextStyle(DEFAULT_FONT, 14, (50, 50, 80)))

    add_watermark(canvas)
    return await canvas.get_img()