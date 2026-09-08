from fastapi import APIRouter, HTTPException

from src.core.http_responses import INTERNAL_SERVER_ERROR_RESPONSES
from src.core.image_payload import require_native_payload
from src.core.utils import encoded_image_payload_to_response
from src.sekai.gacha.drawer import (
    try_render_gacha_detail_payload,
    try_render_gacha_list_payload,
)
from src.sekai.gacha.model import (
    GachaDetailRequest,
    GachaListRequest,
)

router = APIRouter(tags=["Gacha"], responses=INTERNAL_SERVER_ERROR_RESPONSES)


@router.post(
    "/list",
    summary="Generate gacha list image",
    responses=INTERNAL_SERVER_ERROR_RESPONSES,
)
async def gacha_list(request: GachaListRequest):
    """
    Generate a gacha list image.

    Shows multiple gacha banners.
    """
    try:
        payload = await try_render_gacha_list_payload(request)
        payload = require_native_payload(payload)
        return encoded_image_payload_to_response(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/detail",
    summary="Generate gacha detail image",
    responses=INTERNAL_SERVER_ERROR_RESPONSES,
)
async def gacha_detail(request: GachaDetailRequest):
    """
    Generate a gacha detail image.

    Shows gacha information, rates, and pickup cards.
    """
    try:
        payload = await try_render_gacha_detail_payload(request)
        payload = require_native_payload(payload)
        return encoded_image_payload_to_response(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
