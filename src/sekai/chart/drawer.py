from io import BytesIO
import json
import logging
import struct
import time

from PIL import Image
from pjsekai_scores_rs import Drawing, Score

from src.core.debug import set_render_backend
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
from src.sekai.skia_renderer.render_stats import (
    OUTCOME_DISABLED,
    OUTCOME_ERROR,
    OUTCOME_FALLBACK,
    OUTCOME_SKIA,
    backend_for_outcome,
    record_native_metrics,
    record_render,
)
from src.settings import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT, DEFAULT_FONT, FONT_DIR, JPG_QUALITY

from .model import GenerateMusicChartRequest

logger = logging.getLogger("chart.draw.perf")

# /render-stats + the ``backend=`` log field key on this name. The chart path hand-builds its IR
# (it feeds the crate's raster in as a mem image), so it never goes through render_canvas_payload
# and has to record its own outcome — one per render attempt, exactly like the canvas helper.
CHART_ENDPOINT = "chart"

# The /chart route pins PNG on BOTH backends (see src/core/pjsk/chart.py: the Pillow fallback
# calls image_to_response(..., export_format="png")), so EXPORT_IMAGE_FORMAT does not reach the
# pixels here.
CHART_EXPORT_FORMAT = "png"

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


def _prepare_chart_render(rqd: GenerateMusicChartRequest) -> tuple[Drawing, Score]:
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
    return drawing, score


def render_chart_png_bytes(rqd: GenerateMusicChartRequest) -> bytes:
    """Render the chart via pjsekai_scores_rs and return encoded PNG bytes."""
    drawing, score = _prepare_chart_render(rqd)
    return drawing.png(score)


def render_chart_mem_image(
    rqd: GenerateMusicChartRequest,
    *,
    allow_raster: bool = True,
) -> tuple[object, int, int, str]:
    """Return a render_scene mem image, preferring pjsekai_scores_rs' zero-copy raster transport."""
    drawing, score = _prepare_chart_render(rqd)
    raster_render = getattr(drawing, "raster", None)
    if allow_raster and raster_render is not None:
        raster = raster_render(score)
        mem_image = (
            raster.width,
            raster.height,
            raster.row_bytes,
            raster.color_type,
            raster.alpha_type,
            raster,
        )
        return mem_image, raster.width, raster.height, "raw-n32"
    png = drawing.png(score)
    width, height = _png_size(png)
    return png, width, height, "png"


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


def _record(outcome: str, payload: EncodedImagePayload | None = None) -> None:
    """Record one render attempt for /render-stats and tag the request context.

    Mirrors ``src.sekai.skia_renderer.canvas._record``; the chart path cannot reuse the canvas
    helper (it hand-builds its IR around a mem image), so it records through the same public
    primitives instead.
    """
    record_render(CHART_ENDPOINT, outcome)
    backend = backend_for_outcome(outcome)
    set_render_backend(backend)
    if payload is not None:
        payload.backend = backend
        record_native_metrics(payload.native_metrics)


async def try_render_music_chart_payload(rqd: GenerateMusicChartRequest) -> EncodedImagePayload | None:
    """Skia 路径:谱面优先以只读 N32 buffer 零拷贝进入最终场景,只编码一次。

    旧版 pjsekai_scores_rs 没有 ``Drawing.raster`` 时自动退回中间 PNG transport;
    整条 Skia 路径失败时仍由调用方回退 Pillow。
    """
    if not skia_plot_enabled():
        _record(OUTCOME_DISABLED)
        return None
    try:
        native = load_native_renderer()
    except ImportError as exc:
        logger.error("haruki_skia_renderer not importable (%s); falling back to Pillow", exc)
        _record(OUTCOME_FALLBACK)
        return None
    allow_raster = getattr(native, "RAW_BUFFER_CAPABILITY", 0) >= 1

    # 没有整页 payload 缓存,这是有意的:调用方 (cloud) 已按 payload 去重,同一个 payload 不会来第二次,
    # 页面级缓存永远不可能命中,而每次 miss 仍会 insert 挤占共享 LRU。
    watermark_text = build_request_watermark_text(rqd)

    def _render():
        chart_image, w, h, transport = render_chart_mem_image(rqd, allow_raster=allow_raster)
        font_size, lines, text_w, text_h = get_watermark_render_spec(watermark_text, w - WATERMARK_RIGHT_OFFSET, 12)
        footer_h = WATERMARK_TOP_OFFSET + text_h + WATERMARK_BOTTOM_OFFSET + WATERMARK_SHADOW_OFFSET
        b = IRBuilder(
            w,
            h + footer_h,
            assets_base_dir=str(ASSETS_BASE_DIR),
            font_dir=str(FONT_DIR),
            default_font=DEFAULT_FONT,
            bold_font=DEFAULT_BOLD_FONT,
            export_format=CHART_EXPORT_FORMAT,  # the /chart route pins PNG
            jpg_quality=JPG_QUALITY,
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
        return native.render_scene(ir_json, {"chart": chart_image}), transport

    started = time.perf_counter()
    try:
        result, transport = await run_in_pool(_render)
        payload = payload_from_native(result)
    except Exception:
        logger.exception("chart backend=skia failed; falling back to Pillow")
        _record(OUTCOME_ERROR)
        return None
    _record(OUTCOME_SKIA, payload)
    logger.info(
        "chart backend=skia transport=%s total=%.3fs bytes=%d image=%sx%s",
        transport,
        time.perf_counter() - started,
        len(payload.image_bytes),
        payload.image_width,
        payload.image_height,
    )
    return payload
