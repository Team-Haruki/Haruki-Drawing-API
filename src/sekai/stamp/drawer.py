from PIL import Image

from src.sekai.base.draw import (
    BG_PADDING,
    SEKAI_BLUE_BG,
    Canvas,
    TextBox,
    add_watermark,
    roundrect_bg,
)
from src.sekai.base.plot import (
    Grid,
    ImageBox,
    TextStyle,
    VSplit,
)
from src.sekai.base.utils import get_img_from_path
from src.settings import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT, DEFAULT_FONT

# =========================== 从.model导入数据类型 =========================== #
from .model import StampListRequest


async def compose_stamp_list_image(rqd: StampListRequest) -> Image.Image:
    r"""compose_stamp_list_image\

    合成表情列表图片

    Args
    ----
    rqd : StampListRequest
        绘制表情列表图片所必须的数据

    Returns
    -------
    PIL.Image.Image
    """
    stamps = rqd.stamps
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_sep(8).set_item_align("l").set_bg(roundrect_bg(alpha=80)).set_padding(8):
            TextBox(
                rqd.prompt_message,
                style=TextStyle(font=DEFAULT_FONT, size=24, color=(0, 0, 0, 255)),
                use_real_line_count=True,
            ).set_padding(16).set_bg(roundrect_bg(alpha=80))
            with Grid(col_count=5).set_sep(4, 4).set_item_bg(roundrect_bg(alpha=80)):
                for stamp in stamps:
                    img = await get_img_from_path(ASSETS_BASE_DIR, stamp.image_path)
                    with VSplit().set_padding(4).set_sep(4):
                        ImageBox(img, size=(None, 100), use_alpha_blend=True, shadow=True)
                        TextBox(
                            str(stamp.id),
                            style=TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=tuple(stamp.text_color)),
                        )
    add_watermark(canvas)
    return await canvas.get_img()
