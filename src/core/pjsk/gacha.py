from fastapi import APIRouter, HTTPException

from src.core.utils import image_to_response
from src.sekai.gacha.drawer import (
    compose_gacha_detail_image,
    compose_gacha_list_image,
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
        image = await compose_gacha_list_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/detail", summary="Generate gacha detail image")
async def gacha_detail(request: GachaDetailRequest):
    """
    Generate a gacha detail image.

    Shows gacha information, rates, and pickup cards.
    """
    try:
        image = await compose_gacha_detail_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
