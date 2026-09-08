from fastapi import APIRouter, HTTPException

from src.core.http_responses import INTERNAL_SERVER_ERROR_RESPONSES
from src.core.image_payload import require_native_payload
from src.core.utils import encoded_image_payload_to_response
from src.sekai.costume.drawer import (
    try_render_costume_detail_payload,
    try_render_costume_list_payload,
)
from src.sekai.costume.model import CostumeDetailRequest, CostumeListRequest

router = APIRouter(tags=["Costume"], responses=INTERNAL_SERVER_ERROR_RESPONSES)


@router.post(
    "/list",
    summary="Generate costume list image",
    responses=INTERNAL_SERVER_ERROR_RESPONSES,
)
async def costume_list(request: CostumeListRequest):
    try:
        payload = await try_render_costume_list_payload(request)
        payload = require_native_payload(payload)
        return encoded_image_payload_to_response(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/detail",
    summary="Generate costume detail image",
    responses=INTERNAL_SERVER_ERROR_RESPONSES,
)
async def costume_detail(request: CostumeDetailRequest):
    try:
        payload = await try_render_costume_detail_payload(request)
        payload = require_native_payload(payload)
        return encoded_image_payload_to_response(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
