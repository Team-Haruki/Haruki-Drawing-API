import logging
import time

from fastapi import APIRouter, HTTPException

from src.core.utils import encoded_image_payload_to_response, image_to_response
from src.sekai.card.drawer import (
    compose_box_image,
    compose_card_detail_image,
    compose_card_list_image,
    try_render_box_payload,
    try_render_card_detail_payload,
    try_render_card_list_payload,
)
from src.sekai.card.model import (
    CardBoxRequest,
    CardDetailRequest,
    CardListRequest,
)

router = APIRouter(tags=["Card"])
_perf_logger = logging.getLogger("card.endpoint.perf")


@router.post("/detail", summary="Generate card detail image")
async def card_detail(request: CardDetailRequest):
    """
    Generate a detailed card image.

    The image includes card information, power stats, skills, and related event/gacha info.
    """
    try:
        payload = await try_render_card_detail_payload(request)
        if payload is not None:
            return encoded_image_payload_to_response(payload)
        image = await compose_card_detail_image(request)
        return await image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/list", summary="Generate card list image")
async def card_list(request: CardListRequest):
    """
    Generate a card list image.

    Shows multiple cards in a list format with optional user info.
    """
    try:
        _t0 = time.perf_counter()
        payload = await try_render_card_list_payload(request)
        if payload is not None:
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

        image = await compose_card_list_image(request)
        _t1 = time.perf_counter()
        width, height = image.width, image.height
        resp = await image_to_response(image)
        _perf_logger.info(
            "/list total: %.3fs (backend=pillow, draw=%.3fs, encode=%.3fs, image=%dx%d, cards=%d)",
            time.perf_counter() - _t0,
            _t1 - _t0,
            time.perf_counter() - _t1,
            width,
            height,
            len(request.cards),
        )
        return resp
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/box", summary="Generate card box image")
async def card_box(request: CardBoxRequest):
    """
    Generate a card box image.

    Shows cards organized by character with ownership status.
    """
    try:
        _t0 = time.perf_counter()
        payload = await try_render_box_payload(request)
        if payload is not None:
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

        image = await compose_box_image(request)
        _t1 = time.perf_counter()
        width, height = image.width, image.height
        resp = await image_to_response(image)
        _perf_logger.info(
            "/box total: %.3fs (backend=pillow, draw=%.3fs, encode=%.3fs, image=%dx%d, cards=%d)",
            time.perf_counter() - _t0,
            _t1 - _t0,
            time.perf_counter() - _t1,
            width,
            height,
            len(request.cards),
        )
        return resp
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
