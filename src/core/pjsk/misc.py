from fastapi import APIRouter, HTTPException

from src.core.debug import set_request_stage
from src.core.heavy_render_pool import (
    HeavyRenderQueueFullError,
    HeavyRenderQueueTimeoutError,
    HeavyRenderTaskExecutionError,
    HeavyRenderTaskTimeoutError,
    get_heavy_render_worker_pool,
)
from src.core.utils import encoded_image_payload_to_response, image_to_response
from src.sekai.misc.drawer import compose_alias_list_image
from src.sekai.misc.model import AliasListRequest, CharaBirthdayRequest

router = APIRouter(tags=["Misc"])


@router.post("/chara-birthday", summary="Generate character birthday image")
async def chara_birthday(request: CharaBirthdayRequest):
    """
    Generate a character birthday info image.

    Shows character birthday info, upcoming dates, and birthday cards.
    """
    try:
        set_request_stage("misc:chara_birthday:heavy_worker")
        payload = await get_heavy_render_worker_pool().render("chara_birthday", request.model_dump(mode="json"))
        set_request_stage("misc:chara_birthday:stream_response")
        return encoded_image_payload_to_response(payload)
    except (HeavyRenderQueueFullError, HeavyRenderQueueTimeoutError) as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except HeavyRenderTaskTimeoutError as exc:
        raise HTTPException(status_code=504, detail=str(exc)) from exc
    except HeavyRenderTaskExecutionError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/alias-list", summary="Generate alias list image")
async def alias_list(request: AliasListRequest):
    """
    Generate a generic alias list image.

    Used by oversized music / character alias query responses.
    """
    try:
        set_request_stage("misc:alias_list:compose_image")
        image = await compose_alias_list_image(request)
        set_request_stage("misc:alias_list:image_to_response")
        return await image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
