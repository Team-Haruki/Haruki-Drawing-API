import io

from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from src.sekai.base.utils import run_in_pool
from src.settings import EXPORT_IMAGE_FORMAT, JPG_QUALITY


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
    buffer, media_type, filename = await run_in_pool(_encode_image, image, EXPORT_IMAGE_FORMAT, JPG_QUALITY)
    return StreamingResponse(
        buffer,
        media_type=media_type,
        headers={"Content-Disposition": f"inline; filename={filename}"},
        background=BackgroundTask(buffer.close),
    )
