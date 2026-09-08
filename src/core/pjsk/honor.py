from fastapi import APIRouter, HTTPException

from src.core.http_responses import INTERNAL_SERVER_ERROR_RESPONSES
from src.core.image_payload import require_native_payload
from src.core.utils import encoded_image_payload_to_response
from src.sekai.honor.drawer import try_render_full_honor_payload
from src.sekai.honor.model import HonorRequest

router = APIRouter(tags=["Honor"], responses=INTERNAL_SERVER_ERROR_RESPONSES)


@router.post(
    "",
    summary="Generate honor image",
    responses=INTERNAL_SERVER_ERROR_RESPONSES,
)
async def honor(request: HonorRequest):
    """
    Generate an honor/badge image.

    Supports normal, bonds, and event ranking honors.
    """
    try:
        payload = await try_render_full_honor_payload(request)
        payload = require_native_payload(payload)
        return encoded_image_payload_to_response(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
