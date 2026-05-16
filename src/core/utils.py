import io
import logging
import time

from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from src.core.debug import current_request_context, set_request_stage, snapshot_process_metrics
from src.core.heavy_render_pool import EncodedImagePayload
from src.sekai.base.utils import run_in_pool
from src.settings import EXPORT_IMAGE_FORMAT, JPG_QUALITY

logger = logging.getLogger(__name__)


def _encode_image(image, export_format: str, jpg_quality: int) -> tuple[io.BytesIO, str, str]:
    buffer = io.BytesIO()
    try:
        if export_format == "jpg":
            # JPEG 不支持 alpha 通道，需要转换为 RGB
            if image.mode in ("RGBA", "LA", "PA"):
                rgb = image.convert("RGB")
                image.close()
                image = rgb
            image.save(buffer, format="JPEG", quality=jpg_quality)
            media_type = "image/jpeg"
            filename = "image.jpg"
        else:
            image.save(buffer, format="PNG")
            media_type = "image/png"
            filename = "image.png"
    finally:
        close = getattr(image, "close", None)
        if callable(close):
            close()
    buffer.seek(0)
    return buffer, media_type, filename


async def image_to_response(image) -> StreamingResponse:
    """Convert PIL Image to StreamingResponse without blocking the event loop."""
    request_ctx = current_request_context()
    image_width = getattr(image, "width", None)
    image_height = getattr(image, "height", None)
    image_mode = getattr(image, "mode", None)
    set_request_stage("encode_image")
    started = time.perf_counter()
    buffer, media_type, filename = await run_in_pool(_encode_image, image, EXPORT_IMAGE_FORMAT, JPG_QUALITY)
    elapsed = time.perf_counter() - started
    byte_len = buffer.getbuffer().nbytes
    logger.info(
        "image.response id=%s path=%s method=%s size=%sx%s mode=%s media=%s bytes=%d elapsed=%.3fs metrics=%s",
        request_ctx["request_id"],
        request_ctx["path"],
        request_ctx["method"],
        image_width,
        image_height,
        image_mode,
        media_type,
        byte_len,
        elapsed,
        snapshot_process_metrics(include_asyncio=False),
    )
    set_request_stage("stream_response")
    return StreamingResponse(
        buffer,
        media_type=media_type,
        headers={"Content-Disposition": f"inline; filename={filename}"},
        background=BackgroundTask(buffer.close),
    )


def encoded_image_payload_to_response(payload: EncodedImagePayload) -> StreamingResponse:
    request_ctx = current_request_context()
    byte_len = len(payload.image_bytes)
    logger.info(
        "image.response id=%s path=%s method=%s size=%sx%s mode=%s media=%s bytes=%d elapsed=%.3fs metrics=%s",
        request_ctx["request_id"],
        request_ctx["path"],
        request_ctx["method"],
        payload.image_width,
        payload.image_height,
        payload.image_mode,
        payload.media_type,
        byte_len,
        payload.encode_elapsed,
        snapshot_process_metrics(include_asyncio=False),
    )
    set_request_stage("stream_response")
    buffer = io.BytesIO(payload.image_bytes)
    return StreamingResponse(
        buffer,
        media_type=payload.media_type,
        headers={"Content-Disposition": f"inline; filename={payload.filename}"},
        background=BackgroundTask(buffer.close),
    )
