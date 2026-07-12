from fastapi import APIRouter, HTTPException

from src.core.utils import encoded_image_payload_to_response, image_to_response
from src.sekai.base.draw import add_request_watermark_to_image
from src.sekai.honor.drawer import compose_full_honor_image, try_render_full_honor_payload
from src.sekai.honor.model import HonorRequest

router = APIRouter(tags=["Honor"])


@router.post("", summary="Generate honor image")
async def honor(request: HonorRequest):
    """
    Generate an honor/badge image.

    Supports normal, bonds, and event ranking honors.
    """
    try:
        payload = await try_render_full_honor_payload(request)
        if payload is not None:
            return encoded_image_payload_to_response(payload)
        image = await compose_full_honor_image(request)
        image = await add_request_watermark_to_image(image, request)
        return await image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
