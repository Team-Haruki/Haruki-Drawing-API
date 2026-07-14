from fastapi import APIRouter, HTTPException

from src.core.utils import encoded_image_payload_to_response, image_to_response
from src.sekai.gacha.drawer import (
    compose_gacha_detail_image,
    compose_gacha_list_image,
    try_render_gacha_detail_payload,
    try_render_gacha_list_payload,
)
from src.sekai.gacha.model import (
    GachaDetailRequest,
    GachaListRequest,
)

router = APIRouter(tags=["Gacha"])


@router.post("/list", summary="Generate gacha list image")
async def gacha_list(request: GachaListRequest):
    """
    Generate a gacha list image.

    Shows multiple gacha banners.
    """
    try:
        payload = await try_render_gacha_list_payload(request)
        if payload is not None:
            return encoded_image_payload_to_response(payload)
        image = await compose_gacha_list_image(request)
        return await image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/detail", summary="Generate gacha detail image")
async def gacha_detail(request: GachaDetailRequest):
    """
    Generate a gacha detail image.

    Shows gacha information, rates, and pickup cards.
    """
    try:
        payload = await try_render_gacha_detail_payload(request)
        if payload is not None:
            return encoded_image_payload_to_response(payload)
        image = await compose_gacha_detail_image(request)
        return await image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
