import io

from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from src.settings import EXPORT_IMAGE_FORMAT, JPG_QUALITY


def image_to_response(image) -> StreamingResponse:
    """Convert PIL Image to StreamingResponse."""
    buffer = io.BytesIO()
    try:
        if EXPORT_IMAGE_FORMAT == "jpg":
            # JPEG 不支持 alpha 通道，需要转换为 RGB
            if image.mode in ("RGBA", "LA", "PA"):
                rgb = image.convert("RGB")
                image.close()
                image = rgb
            image.save(buffer, format="JPEG", quality=JPG_QUALITY)
        else:
            image.save(buffer, format="PNG")
    finally:
        close = getattr(image, "close", None)
        if callable(close):
            close()
    buffer.seek(0)

    if EXPORT_IMAGE_FORMAT == "jpg":
        media_type = "image/jpeg"
        filename = "image.jpg"
    else:
        media_type = "image/png"
        filename = "image.png"

    return StreamingResponse(
        buffer,
        media_type=media_type,
        headers={"Content-Disposition": f"inline; filename={filename}"},
        background=BackgroundTask(buffer.close),
    )
