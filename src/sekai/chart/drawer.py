from io import BytesIO
import json
import logging
import struct
import time

from PIL import Image
from pjsekai_scores_rs import Drawing, Score

from src.core.heavy_render_pool import EncodedImagePayload
from src.sekai.base.draw import (
    WATERMARK_BOTTOM_OFFSET,
    WATERMARK_LINE_SEP,
    WATERMARK_RIGHT_OFFSET,
    WATERMARK_SHADOW_OFFSET,
    WATERMARK_TOP_OFFSET,
    build_request_watermark_text,
    get_watermark_render_spec,
)
from src.sekai.base.painter import get_font, get_text_size
from src.sekai.base.utils import run_in_pool
from src.sekai.skia_renderer.canvas import load_native_renderer, payload_from_native, skia_plot_enabled
from src.sekai.skia_renderer.ir_builder import IRBuilder
from src.settings import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT, DEFAULT_FONT, FONT_DIR, JPG_QUALITY

from .model import GenerateMusicChartRequest

logger = logging.getLogger("chart.draw.perf")

CHART_FONT_FILENAMES = (
    "SourceHanSansSC-Regular.otf",
    "SourceHanSansSC-Bold.otf",
    "SourceHanSansSC-Heavy.otf",
    "TwitterColorEmoji-SVGinOT.ttf",
)


def chart_font_kwargs() -> dict[str, list[str]]:
    font_paths = [str(FONT_DIR / filename) for filename in CHART_FONT_FILENAMES if (FONT_DIR / filename).is_file()]
    if font_paths:
        return {"font_paths": font_paths}
    return {"font_dirs": [str(FONT_DIR)]}


def load_score(rqd: GenerateMusicChartRequest) -> Score:
    if rqd.chart_json is not None:
        if isinstance(rqd.chart_json, str):
            return Score.from_json(rqd.chart_json)
        return Score.from_json(json.dumps(rqd.chart_json, ensure_ascii=False))
    if not rqd.sus_path:
        raise ValueError("either chart_json or sus_path is required")
    return Score.open(str(ASSETS_BASE_DIR / rqd.sus_path))


def render_chart_png_bytes(rqd: GenerateMusicChartRequest) -> bytes:
    """Render the chart via the pjsekai_scores_rs crate and return the encoded PNG bytes
    (sync; run inside the worker pool). Both backends start from these bytes."""
    style_sheet = ""
    if rqd.style_path:
        style_sheet = (ASSETS_BASE_DIR / rqd.style_path).read_text(encoding="utf-8")
    score = load_score(rqd)
    score.set_meta(
        title=rqd.title,
        artist=rqd.artist,
        difficulty=rqd.difficulty,
        playlevel=str(rqd.play_level),
        jacket=str(ASSETS_BASE_DIR / rqd.jacket_path),
        songid=str(rqd.music_id),
    )
    drawing = Drawing(
        note_host=str(ASSETS_BASE_DIR / rqd.note_host),
        style_sheet=style_sheet,
        skill=rqd.skill,
        music_meta=rqd.music_meta,
        target_segment_seconds=rqd.target_segment_seconds,
        **chart_font_kwargs(),
    )
    return drawing.png(score)


async def generate_music_chart(rqd: GenerateMusicChartRequest) -> Image.Image:
    r"""generate_music_chart

    生成谱面图片(Pillow 对象;回退路径使用)

    Args
    ----
    rqd : GenerateMusicChartRequest
        生成谱面图片所必需的数据

    Returns
    -------
    PIL.Image.Image
    """

    def render_png() -> Image.Image:
        image = Image.open(BytesIO(render_chart_png_bytes(rqd)))
        image.load()
        return image

    return await run_in_pool(render_png)


async def compose_music_chart_image(rqd: GenerateMusicChartRequest) -> Image.Image:
    """Pillow 路径:crate PNG 解码 + 光栅水印页脚(回退与对拍基线)。"""
    from src.sekai.base.draw import add_request_watermark_to_image

    image = await generate_music_chart(rqd)
    return await add_request_watermark_to_image(image, rqd)


def _png_size(png: bytes) -> tuple[int, int]:
    """Width/height straight from the PNG IHDR (no pixel decode)."""
    if len(png) < 24 or png[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("crate did not return a PNG")
    w, h = struct.unpack(">II", png[16:24])
    return int(w), int(h)


async def try_render_music_chart_payload(rqd: GenerateMusicChartRequest) -> EncodedImagePayload | None:
    """Skia 路径,一进一出:crate 出的 PNG bytes 作 encoded mem 图进 ``render_scene``
    (场景 = 谱面图 + 底部条采样页脚 + 右对齐白字灰影水印,复刻 ``add_watermark_to_image``
    的光栅页脚),编码字节直接出——Python 全程不解码像素。失败回退 Pillow 路径。"""
    if not skia_plot_enabled():
        return None
    try:
        native = load_native_renderer()
    except ImportError as exc:
        logger.error("haruki_skia_renderer not importable (%s); falling back to Pillow", exc)
        return None

    def _render():
        png = render_chart_png_bytes(rqd)
        w, h = _png_size(png)
        text = build_request_watermark_text(rqd)
        font_size, lines, text_w, text_h = get_watermark_render_spec(text, w - WATERMARK_RIGHT_OFFSET, 12)
        footer_h = WATERMARK_TOP_OFFSET + text_h + WATERMARK_BOTTOM_OFFSET + WATERMARK_SHADOW_OFFSET
        b = IRBuilder(
            w, h + footer_h,
            assets_base_dir=str(ASSETS_BASE_DIR), font_dir=str(FONT_DIR),
            default_font=DEFAULT_FONT, bold_font=DEFAULT_BOLD_FONT,
            export_format="png", jpg_quality=JPG_QUALITY,  # the /chart route pins PNG
        )
        b.image("mem:chart", (0, 0), (w, h), fit="stretch")
        # Footer background: the bottom footer_h strip of the chart, stretched (add_watermark_to_image).
        sample_h = max(1, min(h, footer_h))
        b.image("mem:chart", (0, h), (w, footer_h), fit="stretch", source_rect=(0, h - sample_h, w, h))
        font = get_font(DEFAULT_FONT, font_size)
        x = max(0, w - text_w - WATERMARK_RIGHT_OFFSET)
        y = h + WATERMARK_TOP_OFFSET
        for idx, line in enumerate(lines):
            line_w = get_text_size(font, line)[0]
            lx = x + max(0, text_w - line_w)
            ly = y + idx * (font_size + WATERMARK_LINE_SEP)
            # PIL ImageDraw.text default anchor is left/top-of-ascent -> IR "ascender" baseline.
            b.text(line, (lx + 1, ly + 1), "default", font_size, baseline="ascender", fill=(75, 75, 75, 255))
            b.text(line, (lx, ly), "default", font_size, baseline="ascender", fill=(255, 255, 255, 255))
        ir_json = json.dumps(b.build(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        return native.render_scene(ir_json, {"chart": png})

    started = time.perf_counter()
    try:
        result = await run_in_pool(_render)
        payload = payload_from_native(result)
    except Exception:
        logger.exception("chart backend=skia failed; falling back to Pillow")
        return None
    logger.info(
        "chart backend=skia total=%.3fs bytes=%d image=%sx%s",
        time.perf_counter() - started, len(payload.image_bytes), payload.image_width, payload.image_height,
    )
    return payload
