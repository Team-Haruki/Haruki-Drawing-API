from functools import partial
import io
import logging
import time

from fastapi.responses import Response

from src.core.debug import (
    current_render_backend,
    current_request_context,
    set_request_stage,
    snapshot_process_metrics,
)
from src.core.heavy_render_pool import EncodedImagePayload
from src.sekai.base.utils import run_in_pool
from src.settings import EXPORT_IMAGE_FORMAT, JPG_QUALITY

logger = logging.getLogger(__name__)


def _encode_image(
    image,
    export_format: str,
    jpg_quality: int,
    *,
    jpeg_subsampling: int | str | None = None,
) -> tuple[io.BytesIO, str, str]:
    buffer = io.BytesIO()
    try:
        if export_format == "jpg":
            # JPEG 不支持 alpha 通道，需要转换为 RGB
            if image.mode in ("RGBA", "LA", "PA"):
                rgb = image.convert("RGB")
                image.close()
                image = rgb
            save_kwargs = {"quality": jpg_quality}
            if jpeg_subsampling is not None:
                save_kwargs["subsampling"] = jpeg_subsampling
            image.save(buffer, format="JPEG", **save_kwargs)
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


async def image_to_response(
    image,
    export_format: str | None = None,
    jpg_quality: int | None = None,
    *,
    jpeg_subsampling: int | str | None = None,
) -> Response:
    """Encode a PIL Image off the event loop and return it as a single-body response."""
    request_ctx = current_request_context()
    image_width = getattr(image, "width", None)
    image_height = getattr(image, "height", None)
    image_mode = getattr(image, "mode", None)
    set_request_stage("encode_image")
    started = time.perf_counter()
    encoder = partial(
        _encode_image,
        image,
        export_format if export_format is not None else EXPORT_IMAGE_FORMAT,
        jpg_quality if jpg_quality is not None else JPG_QUALITY,
        jpeg_subsampling=jpeg_subsampling,
    )
    buffer, media_type, filename = await run_in_pool(encoder)
    elapsed = time.perf_counter() - started
    byte_len = buffer.getbuffer().nbytes
    logger.info(
        "image.response id=%s path=%s method=%s size=%sx%s mode=%s media=%s bytes=%d elapsed=%.3fs "
        "backend=%s metrics=%s",
        request_ctx["request_id"],
        request_ctx["path"],
        request_ctx["method"],
        image_width,
        image_height,
        image_mode,
        media_type,
        byte_len,
        elapsed,
        # A request that never attempted Skia leaves the default "pillow"; one where Skia
        # declined/raised was tagged "skia_fallback" by the render helper.
        current_render_backend(),
        snapshot_process_metrics(include_asyncio=False),
    )
    set_request_stage("send_response")
    try:
        return _image_response(buffer.getvalue(), media_type, filename)
    finally:
        buffer.close()


def _image_response(image_bytes: bytes, media_type: str, filename: str) -> Response:
    """Send the encoded image as ONE body message.

    This used to be ``StreamingResponse(io.BytesIO(image_bytes))``, which streamed nothing useful:
    the bytes are already whole in memory, so there was no memory to save. What it did instead was
    hand Starlette a *sync* iterable, which Starlette drives through ``iterate_in_threadpool`` --
    and iterating a ``BytesIO`` yields **lines**. A PNG is binary, so it split on every 0x0A byte:
    ~384 bytes per chunk, i.e. ~2,300 thread-pool round-trips and ~2,300 ASGI body messages for a
    single 870 KB image. Under 8 concurrent requests that scheduling storm took a render the server
    finished in 0.12s and made the client wait ~10s for it, with the CPU 95% idle.
    """
    return Response(
        content=image_bytes,
        media_type=media_type,
        headers={"Content-Disposition": f"inline; filename={filename}"},
    )


def encoded_image_payload_to_response(payload: EncodedImagePayload) -> Response:
    request_ctx = current_request_context()
    byte_len = len(payload.image_bytes)
    logger.info(
        "image.response id=%s path=%s method=%s size=%sx%s mode=%s media=%s bytes=%d elapsed=%.3fs "
        "backend=%s metrics=%s",
        request_ctx["request_id"],
        request_ctx["path"],
        request_ctx["method"],
        payload.image_width,
        payload.image_height,
        payload.image_mode,
        payload.media_type,
        byte_len,
        payload.encode_elapsed,
        # The payload carries its own backend across the heavy-worker process boundary, where a
        # contextvar set in the child is invisible here; in-process renders set the contextvar.
        payload.backend or current_render_backend(),
        snapshot_process_metrics(include_asyncio=False),
    )
    set_request_stage("send_response")
    return _image_response(payload.image_bytes, payload.media_type, payload.filename)
