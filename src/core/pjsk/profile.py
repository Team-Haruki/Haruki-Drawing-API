from fastapi import APIRouter, HTTPException

from src.core.utils import image_to_response
from src.sekai.profile.drawer import compose_profile_image
from src.sekai.profile.model import ProfileRequest

router = APIRouter(tags=["Profile"])


@router.post("/", summary="Generate profile image")
async def profile(request: ProfileRequest):
    """
    Generate a player profile image.

    Shows player info, rank, honors, cards, and play statistics.
    """
    try:
        image = await compose_profile_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
