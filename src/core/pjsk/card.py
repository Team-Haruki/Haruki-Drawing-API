import logging
import time

from fastapi import APIRouter, HTTPException

from src.core.http_responses import INTERNAL_SERVER_ERROR_RESPONSES
from src.core.image_payload import require_native_payload
from src.core.utils import encoded_image_payload_to_response
from src.sekai.card.drawer import (
    try_render_box_payload,
    try_render_card_detail_payload,
    try_render_card_list_payload,
)
from src.sekai.card.model import (
    CardBoxRequest,
    CardDetailRequest,
    CardListRequest,
)

router = APIRouter(tags=["Card"], responses=INTERNAL_SERVER_ERROR_RESPONSES)
_perf_logger = logging.getLogger("card.endpoint.perf")


@router.post(
    "/detail",
    summary="Generate card detail image",
    responses=INTERNAL_SERVER_ERROR_RESPONSES,
)
async def card_detail(request: CardDetailRequest):
    """
    Generate a detailed card image.

    The image includes card information, power stats, skills, and related event/gacha info.
    """
    try:
        payload = await try_render_card_detail_payload(request)
        payload = require_native_payload(payload)
        return encoded_image_payload_to_response(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/list",
    summary="Generate card list image",
    responses=INTERNAL_SERVER_ERROR_RESPONSES,
)
async def card_list(request: CardListRequest):
    """
    Generate a card list image.

    Shows multiple cards in a list format with optional user info.
    """
    try:
        _t0 = time.perf_counter()
        payload = await try_render_card_list_payload(request)
        payload = require_native_payload(payload)
        resp = encoded_image_payload_to_response(payload)
        _perf_logger.info(
            "/list total: %.3fs (backend=skia, encode=%.3fs, image=%dx%d, cards=%d)",
            time.perf_counter() - _t0,
            payload.encode_elapsed,
            payload.image_width,
            payload.image_height,
            len(request.cards),
        )
        return resp
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/box",
    summary="Generate card box image",
    responses=INTERNAL_SERVER_ERROR_RESPONSES,
)
async def card_box(request: CardBoxRequest):
    """
    Generate a card box image.

    Shows cards organized by character with ownership status.
    """
    try:
        _t0 = time.perf_counter()
        payload = await try_render_box_payload(request)
        payload = require_native_payload(payload)
        resp = encoded_image_payload_to_response(payload)
        _perf_logger.info(
            "/box total: %.3fs (backend=skia, encode=%.3fs, image=%dx%d, cards=%d)",
            time.perf_counter() - _t0,
            payload.encode_elapsed,
            payload.image_width,
            payload.image_height,
            len(request.cards),
        )
        return resp
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
