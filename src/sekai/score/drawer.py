from PIL import Image
from src.base.configs import DEFAULT_BOLD_FONT, DEFAULT_FONT, ASSETS_BASE_DIR
from src.base.painter import BLACK
from src.base.utils import get_img_from_path
from src.base.draw import (
    TextBox,
    Canvas,
    SEKAI_BLUE_BG,
    BG_PADDING,
    roundrect_bg,
    add_watermark,
)
from src.base.plot import (
    VSplit,
    HSplit,
    ImageBox,
    FillBg,
    TextStyle,
)


# =========================== 从.model导入数据类型 =========================== #

from .model import *

# 合成控分图片
async def compose_score_control_image(
    rqd: ScoreControlRequest,
) -> Image.Image:
    r"""compose_score_control_image

    合成控分图片

    Args
    ----
    rqd : ScoreControlRequest
        绘制控分图片所必须的数据
    
    Returns
    -------
    PIL.Image.Image
    """
    SHOW_SEG_LEN = 50

    def get_score_str(score: int) -> str:
        score_str = str(score)
        score_str = score_str[::-1]
        score_str = ','.join([score_str[i:i + 4] for i in range(0, len(score_str), 4)])
        return score_str[::-1]
    
    style1 = TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=BLACK)
    style2 = TextStyle(font=DEFAULT_FONT,      size=16, color=(50, 50, 50))
    style3 = TextStyle(font=DEFAULT_BOLD_FONT, size=16, color=(255, 50, 50))
    
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_item_bg(roundrect_bg(alpha=80)):
            # 标题
            with VSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_padding(8):
                with HSplit().set_content_align('lb').set_item_align('lb').set_sep(4):
                    music_cover = await get_img_from_path(ASSETS_BASE_DIR, rqd.music_cover_path)
                    ImageBox(music_cover, size=(20, 20), use_alpha_blend=False)
                    TextBox(f"【{rqd.music_id}】{rqd.music_title} (任意难度)", style1)
                with HSplit().set_content_align('lb').set_item_align('lb').set_sep(4):
                    TextBox(f"歌曲基础分 {rqd.music_basic_point}   目标PT: ", style1)
                    TextBox(f" {rqd.target_point}", style3)
                if rqd.music_basic_point != 100 and rqd.target_point > 1000:
                    TextBox(f"基础分非100有误差风险，不推荐控较大PT", style3)
                if rqd.target_point > 3000:
                    TextBox(f"目标PT过大可能存在误差，推荐以多次控分", style3)
                TextBox(f"控分教程：选取表中一个活动加成和体力", style1)
                TextBox(f"游玩歌曲到对应分数范围内放置", style1)
                TextBox(f"友情提醒：控分前请核对加成和体力设置", style3)
                TextBox(f"特别注意核对加成是否多了0.5", style3)
            
            # 数据
            with HSplit().set_content_align('lt').set_item_align('lt').set_sep(8).set_omit_parent_bg(True).set_item_bg(roundrect_bg(alpha=80)):
                for i in range(0, len(rqd.valid_scores), SHOW_SEG_LEN):
                    scores = rqd.valid_scores[i:i + SHOW_SEG_LEN]
                    gh, gw1, gw2, gw3, gw4 = 20, 54, 48, 90, 90
                    bg1 = FillBg((255, 255, 255, 200))
                    bg2 = FillBg((255, 255, 255, 100))
                    with VSplit().set_content_align('lt').set_item_align('lt').set_sep(4).set_padding(8):
                        with HSplit().set_content_align('lt').set_item_align('lt').set_sep(4):
                            TextBox("加成",  style1).set_bg(bg1).set_size((gw1, gh)).set_content_align('c')
                            TextBox("火",    style1).set_bg(bg1).set_size((gw2, gh)).set_content_align('c')
                            TextBox("分数下限",  style1).set_bg(bg1).set_size((gw3, gh)).set_content_align('c')
                            TextBox("分数上限",  style1).set_bg(bg1).set_size((gw4, gh)).set_content_align('c')
                        for i, item in enumerate(scores):
                            bg = bg2 if i % 2 == 0 else bg1
                            score_min = get_score_str(item.score_min)
                            if score_min == '0': score_min = '0 (放置)'
                            score_max = get_score_str(item.score_max)
                            with HSplit().set_content_align('lt').set_item_align('lt').set_sep(4):
                                TextBox(f"{item.event_bonus}", style2).set_bg(bg).set_size((gw1, gh)).set_content_align('r')
                                TextBox(f"{item.boost}",       style2).set_bg(bg).set_size((gw2, gh)).set_content_align('r')
                                TextBox(f"{score_min}",         style2).set_bg(bg).set_size((gw3, gh)).set_content_align('r')
                                TextBox(f"{score_max}",         style2).set_bg(bg).set_size((gw4, gh)).set_content_align('r')

    add_watermark(canvas)
    return await canvas.get_img()
    