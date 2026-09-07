from fastapi import APIRouter, HTTPException

from src.core.image_payload import require_native_payload
from src.core.utils import encoded_image_payload_to_response
from src.sekai.stamp.drawer import try_render_stamp_payload
from src.sekai.stamp.model import StampListRequest

router = APIRouter(tags=["Stamp"])


@router.post("/list", summary="Generate stamp list image")
async def stamp_list(request: StampListRequest):
    """
    Generate a stamp list image.

    Shows available stamps in a grid layout.
    """
    try:
        payload = await try_render_stamp_payload(request)
        payload = require_native_payload(payload)
        return encoded_image_payload_to_response(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
