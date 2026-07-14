from fastapi import APIRouter, HTTPException

from src.core.utils import encoded_image_payload_to_response, image_to_response
from src.sekai.costume.drawer import (
    compose_costume_detail_image,
    compose_costume_list_image,
    try_render_costume_detail_payload,
    try_render_costume_list_payload,
)
from src.sekai.costume.model import CostumeDetailRequest, CostumeListRequest

router = APIRouter(tags=["Costume"])


@router.post("/list", summary="Generate costume list image")
async def costume_list(request: CostumeListRequest):
    try:
        payload = await try_render_costume_list_payload(request)
        if payload is not None:
            return encoded_image_payload_to_response(payload)
        image = await compose_costume_list_image(request)
        return await image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/detail", summary="Generate costume detail image")
async def costume_detail(request: CostumeDetailRequest):
    try:
        payload = await try_render_costume_detail_payload(request)
        if payload is not None:
            return encoded_image_payload_to_response(payload)
        image = await compose_costume_detail_image(request)
        return await image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
