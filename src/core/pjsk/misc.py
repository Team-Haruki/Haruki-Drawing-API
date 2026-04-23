from fastapi import APIRouter, HTTPException

from src.core.utils import image_to_response
from src.sekai.misc.drawer import compose_alias_list_image, compose_chara_birthday_image
from src.sekai.misc.model import AliasListRequest, CharaBirthdayRequest

router = APIRouter(tags=["Misc"])


@router.post("/chara-birthday", summary="Generate character birthday image")
async def chara_birthday(request: CharaBirthdayRequest):
    """
    Generate a character birthday info image.

    Shows character birthday info, upcoming dates, and birthday cards.
    """
    try:
        image = await compose_chara_birthday_image(request)
        return await image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/alias-list", summary="Generate alias list image")
async def alias_list(request: AliasListRequest):
    """
    Generate a generic alias list image.

    Used by oversized music / character alias query responses.
    """
    try:
        image = await compose_alias_list_image(request)
        return await image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
