import traceback

from fastapi import APIRouter, HTTPException

from src.core.utils import encoded_image_payload_to_response, image_to_response
from src.sekai.event.drawer import (
    compose_event_detail_image,
    compose_event_list_image,
    compose_event_planner_image,
    compose_event_record_image,
    try_render_event_detail_payload,
    try_render_event_planner_payload,
    try_render_event_record_payload,
)
from src.sekai.event.model import (
    EventDetailRequest,
    EventListRequest,
    EventPlannerRequest,
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
        payload = await try_render_event_detail_payload(request)
        if payload is not None:
            return encoded_image_payload_to_response(payload)
        image = await compose_event_detail_image(request)
        return await image_to_response(image)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/record", summary="Generate event record image")
async def event_record(request: EventRecordRequest):
    """
    Generate an event participation record image.

    Shows user's event history and rankings.
    """
    try:
        payload = await try_render_event_record_payload(request)
        if payload is not None:
            return encoded_image_payload_to_response(payload)
        image = await compose_event_record_image(request)
        return await image_to_response(image)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/list", summary="Generate event list image")
async def event_list(request: EventListRequest):
    """
    Generate an event list image.

    Shows multiple events in a list format.
    """
    try:
        image = await compose_event_list_image(request)
        return await image_to_response(image)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/planner", summary="Generate event planner image")
async def event_planner(request: EventPlannerRequest):
    """
    Generate an event planning image.

    Shows target points, selected deck, and estimated plays/energy per song.
    """
    try:
        payload = await try_render_event_planner_payload(request)
        if payload is not None:
            return encoded_image_payload_to_response(payload)
        image = await compose_event_planner_image(request)
        return await image_to_response(image)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
