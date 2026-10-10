import traceback

from fastapi import APIRouter

from src.core.http_responses import INTERNAL_SERVER_ERROR_RESPONSES
from src.core.image_payload import require_native_payload
from src.core.render_errors import render_http_exception
from src.core.utils import encoded_image_payload_to_response
from src.sekai.vlive.drawer import try_render_vlive_detail_payload, try_render_vlive_list_payload
from src.sekai.vlive.model import VLiveDetailRequest, VLiveListRequest

router = APIRouter(tags=["VLive"], responses=INTERNAL_SERVER_ERROR_RESPONSES)


@router.post(
    "/list",
    summary="Generate virtual live list image",
    responses=INTERNAL_SERVER_ERROR_RESPONSES,
)
async def vlive_list(request: VLiveListRequest):
    """
    Generate a virtual live list image.

    Shows recent and upcoming virtual lives in a reminder-style list.
    """
    try:
        payload = await try_render_vlive_list_payload(request)
        payload = require_native_payload(payload)
        return await encoded_image_payload_to_response(payload)
    except Exception as e:
        traceback.print_exc()
        raise render_http_exception(e)


@router.post(
    "/detail",
    summary="Generate virtual live detail image",
    responses=INTERNAL_SERVER_ERROR_RESPONSES,
)
async def vlive_detail(request: VLiveDetailRequest):
    """
    Generate a virtual live detail image.

    Shows one virtual live or one collapsed solo virtual live group: per-live schedules, total
    cheer-point reward thresholds, the surplus reward and the virtual-item override cost.
    """
    try:
        payload = await try_render_vlive_detail_payload(request)
        payload = require_native_payload(payload)
        return await encoded_image_payload_to_response(payload)
    except Exception as e:
        traceback.print_exc()
        raise render_http_exception(e)
