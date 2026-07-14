import asyncio
import logging
import time

from PIL import Image

from src.core.heavy_render_pool import EncodedImagePayload
from src.sekai.base.draw import (
    BG_PADDING,
    SEKAI_BLUE_BG,
    Canvas,
    TextBox,
    add_request_watermark,
    roundrect_bg,
)
from src.sekai.base.plot import (
    Grid,
    ImageBox,
    TextStyle,
    VSplit,
)
from src.sekai.base.utils import get_asset_image_ref
from src.sekai.skia_renderer.canvas import render_canvas_payload, skia_plot_enabled
from src.settings import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT, DEFAULT_FONT

# =========================== 从.model导入数据类型 =========================== #
from .model import StampListRequest

logger = logging.getLogger(__name__)


async def _build_stamp_canvas(rqd: StampListRequest) -> Canvas:
    """Build the stamp-list widget tree (shared by the Pillow and Skia render paths)."""
    stamps = rqd.stamps
    _t0 = time.perf_counter()
    stamp_imgs = await asyncio.gather(*[get_asset_image_ref(ASSETS_BASE_DIR, stamp.image_path) for stamp in stamps])
    logger.debug("[perf] compose_stamp_list_image preload %d images: %.3fs", len(stamps), time.perf_counter() - _t0)
    with Canvas(bg=SEKAI_BLUE_BG).set_padding(BG_PADDING) as canvas:
        with VSplit().set_sep(8).set_item_align("l").set_bg(roundrect_bg(alpha=80)).set_padding(8):
            TextBox(
                rqd.prompt_message,
                style=TextStyle(font=DEFAULT_FONT, size=24, color=(0, 0, 0, 255)),
                use_real_line_count=True,
            ).set_padding(16).set_bg(roundrect_bg(alpha=80))
            with Grid(col_count=5).set_sep(4, 4).set_item_bg(roundrect_bg(alpha=80)):
                for stamp, img in zip(stamps, stamp_imgs):
                    with VSplit().set_padding(4).set_sep(4):
                        ImageBox(img, size=(None, 100), use_alpha_blend=True, shadow=True)
                        TextBox(
                            str(stamp.id),
                            style=TextStyle(font=DEFAULT_BOLD_FONT, size=24, color=tuple(stamp.text_color)),
                        )
            if rqd.page_message:
                TextBox(
                    rqd.page_message,
                    style=TextStyle(font=DEFAULT_FONT, size=22, color=(40, 40, 40, 255)),
                ).set_padding((0, 8)).set_content_align("c").set_w(620)
    add_request_watermark(canvas, rqd)
    return canvas


async def compose_stamp_list_image(rqd: StampListRequest) -> Image.Image:
    r"""合成表情列表图片 (Pillow 路径)。"""
    canvas = await _build_stamp_canvas(rqd)
    return await canvas.get_img()


async def try_render_stamp_payload(rqd: StampListRequest) -> EncodedImagePayload | None:
    """Skia 路径：构建同一棵 widget 树并经 IRPainter 渲染；不可用时返回 None 回退 Pillow。"""
    if not skia_plot_enabled():
        return None
    canvas = await _build_stamp_canvas(rqd)
    return await render_canvas_payload(canvas, endpoint="stamp_list")
