from PIL import Image
from pydantic import BaseModel
from typing import Optional, List

from src.base.configs import DEFAULT_BOLD_FONT, DEFAULT_FONT, ASSETS_BASE_DIR
from src.base.painter import WHITE
from src.base.utils import get_img_from_path
from src.base.draw import (
    TextBox,
    Canvas,
    SEKAI_BLUE_BG,
    BG_PADDING,
    roundrect_bg,
    add_watermark,
    DIFF_COLORS
)
from src.base.plot import (
    VSplit,
    HSplit,
    Frame,
    Spacer,
    ImageBox,
    FillBg,
    RoundRectBg,
    TextStyle,
    Grid,
)


# =========================== 从.model导入数据类型 =========================== #

from .model import *

async def compose_stamp_list_image(
    stamps: List[StampData]
) -> Image.Image:
    r"""compose_stamp_list_image\
    
    合成表情列表图片

    TODO:
    -----
        提示性文字还未确定，
        id文字颜色也暂定

    Args
    ----
    stamps : List[ StampData ]
        表情id和表情路径的列表

    Returns
    -------
    PIL.Image.Image
    """
    Text1 = "测试测试"
    Text2 = "1234556"
    
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_sep(8).set_item_align('l').set_bg(roundrect_bg(alpha=80)).set_padding(8):
            TextBox(
                f"发送\"{Text1} 序号\"获取单张表情\n"
                f"发送\"{Text2} 序号 文本\"制作表情\n"
                f"序号为绿色的表情有人工抠图处理的底图\n"
                f"序号为蓝色的表情有AI抠图处理的底图",
                style=TextStyle(font=DEFAULT_FONT, size=24, color=(0, 0, 0, 255)), use_real_line_count=True) \
                .set_padding(16).set_bg(roundrect_bg(alpha=80))
            with Grid(col_count=5).set_sep(4, 4).set_item_bg(roundrect_bg(alpha=80)):
                for stamp in stamps:
                    img = await get_img_from_path(ASSETS_BASE_DIR, stamp.image_path)
                    with VSplit().set_padding(4).set_sep(4):
                        ImageBox(img, size=(None, 100), use_alpha_blend=True, shadow=True)
                        TextBox(str(stamp.stamp_id), style=TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=stamp.text_color))
    add_watermark(canvas)
    return await canvas.get_img()