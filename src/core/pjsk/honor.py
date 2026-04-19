from fastapi import APIRouter, HTTPException

from src.core.utils import image_to_response
from src.sekai.base.draw import add_request_watermark_to_image
from src.sekai.honor.drawer import compose_full_honor_image
from src.sekai.honor.model import HonorRequest

router = APIRouter(tags=["Honor"])


@router.post("", summary="Generate honor image")
async def honor(request: HonorRequest):
    """
    Generate an honor/badge image.

    Supports normal, bonds, and event ranking honors.
    """
    try:
        image = await compose_full_honor_image(request)
        image = add_request_watermark_to_image(image, request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
