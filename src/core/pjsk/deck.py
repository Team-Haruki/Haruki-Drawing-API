import traceback

from fastapi import APIRouter, HTTPException

from src.core.debug import set_request_stage
from src.core.heavy_render_pool import HeavyRenderTaskExecutionError, HeavyRenderTaskTimeoutError, get_heavy_render_worker_pool
from src.core.utils import encoded_image_payload_to_response
from src.sekai.deck.model import DeckRequest

router = APIRouter(tags=["Deck"])


@router.post("/recommend", summary="Generate deck recommendation image")
async def deck_recommend(request: DeckRequest):
    """
    Generate a deck recommendation image.

    Provides card recommendations for specific events or songs based on optimization targets.
    """
    try:
        set_request_stage("deck:heavy_worker")
        payload = await get_heavy_render_worker_pool().render("deck_recommend", request.model_dump(mode="json"))
        set_request_stage("deck:stream_response")
        return encoded_image_payload_to_response(payload)
    except HeavyRenderTaskTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except HeavyRenderTaskExecutionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
