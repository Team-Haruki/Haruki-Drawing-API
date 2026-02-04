from fastapi import APIRouter, HTTPException

from src.core.utils import image_to_response
from src.sekai.misc.drawer import compose_chara_birthday_image
from src.sekai.misc.model import CharaBirthdayRequest

router = APIRouter(tags=["Misc"])


@router.post("/chara-birthday", summary="Generate character birthday image")
async def chara_birthday(request: CharaBirthdayRequest):
    """
    Generate a character birthday info image.

    Shows character birthday info, upcoming dates, and birthday cards.
    """
    try:
        image = await compose_chara_birthday_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
