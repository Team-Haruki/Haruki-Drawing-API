import io

from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask


def image_to_response(image) -> StreamingResponse:
    """Convert PIL Image to StreamingResponse."""
    buffer = io.BytesIO()
    try:
        image.save(buffer, format="PNG")
    finally:
        close = getattr(image, "close", None)
        if callable(close):
            close()
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="image/png",
        headers={"Content-Disposition": "inline; filename=image.png"},
        background=BackgroundTask(buffer.close),
    )
