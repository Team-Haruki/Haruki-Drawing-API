from fastapi import APIRouter, HTTPException

from src.core.http_responses import INTERNAL_SERVER_ERROR_RESPONSES
from src.core.utils import encoded_image_payload_to_response, image_to_response
from src.sekai.stamp.drawer import compose_stamp_list_image, try_render_stamp_payload
from src.sekai.stamp.model import StampListRequest

router = APIRouter(tags=["Stamp"], responses=INTERNAL_SERVER_ERROR_RESPONSES)


@router.post(
    "/list",
    summary="Generate stamp list image",
    responses=INTERNAL_SERVER_ERROR_RESPONSES,
)
async def stamp_list(request: StampListRequest):
    """
    Generate a stamp list image.

    Shows available stamps in a grid layout.
    """
    try:
        payload = await try_render_stamp_payload(request)
        if payload is not None:
            return encoded_image_payload_to_response(payload)
        image = await compose_stamp_list_image(request)
        return await image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
