from fastapi import APIRouter, HTTPException

from src.core.utils import image_to_response
from src.sekai.card.drawer import (
    compose_box_image,
    compose_card_detail_image,
    compose_card_list_image,
)
from src.sekai.card.model import (
    CardBoxRequest,
    CardDetailRequest,
    CardListRequest,
)

router = APIRouter(tags=["Card"])


@router.post("/detail", summary="Generate card detail image")
async def card_detail(request: CardDetailRequest):
    """
    Generate a detailed card image.

    The image includes card information, power stats, skills, and related event/gacha info.
    """
    try:
        image = await compose_card_detail_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/list", summary="Generate card list image")
async def card_list(request: CardListRequest):
    """
    Generate a card list image.

    Shows multiple cards in a list format with optional user info.
    """
    try:
        image = await compose_card_list_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/box", summary="Generate card box image")
async def card_box(request: CardBoxRequest):
    """
    Generate a card box image.

    Shows cards organized by character with ownership status.
    """
    try:
        image = await compose_box_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
