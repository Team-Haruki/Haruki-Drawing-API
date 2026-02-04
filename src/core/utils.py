import io

from fastapi.responses import StreamingResponse


def image_to_response(image) -> StreamingResponse:
    """Convert PIL Image to StreamingResponse."""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return StreamingResponse(
        buffer, media_type="image/png", headers={"Content-Disposition": "inline; filename=image.png"}
    )
