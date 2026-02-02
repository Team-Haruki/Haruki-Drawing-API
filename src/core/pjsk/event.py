from fastapi import APIRouter, HTTPException

from src.core.utils import image_to_response
from src.sekai.event.drawer import (
    compose_event_detail_image,
    compose_event_list_image,
    compose_event_record_image,
)
from src.sekai.event.model import (
    EventDetailRequest,
    EventListRequest,
    EventRecordRequest,
)

router = APIRouter(tags=["Event"])


@router.post("/detail", summary="Generate event detail image")
async def event_detail(request: EventDetailRequest):
    """
    Generate an event detail image.

    Shows event information, banner, and featured cards.
    """
    try:
        image = await compose_event_detail_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/record", summary="Generate event record image")
async def event_record(request: EventRecordRequest):
    """
    Generate an event participation record image.

    Shows user's event history and rankings.
    """
    try:
        image = await compose_event_record_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/list", summary="Generate event list image")
async def event_list(request: EventListRequest):
    """
    Generate an event list image.

    Shows multiple events in a list format.
    """
    try:
        image = await compose_event_list_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
