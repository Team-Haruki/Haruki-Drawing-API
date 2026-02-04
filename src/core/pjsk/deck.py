from fastapi import APIRouter, HTTPException

from src.core.utils import image_to_response
from src.sekai.deck.drawer import compose_deck_recommend_image
from src.sekai.deck.model import DeckRequest

router = APIRouter(tags=["Deck"])


@router.post("/recommend", summary="Generate deck recommendation image")
async def deck_recommend(request: DeckRequest):
    """
    Generate a deck recommendation image.

    Provides card recommendations for specific events or songs based on optimization targets.
    """
    try:
        image = await compose_deck_recommend_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
