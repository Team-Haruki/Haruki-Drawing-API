from fastapi import APIRouter, HTTPException

from src.core.utils import image_to_response
from src.sekai.costume.drawer import compose_costume_detail_image, compose_costume_list_image
from src.sekai.costume.model import CostumeDetailRequest, CostumeListRequest

router = APIRouter(tags=["Costume"])


@router.post("/list", summary="Generate costume list image")
async def costume_list(request: CostumeListRequest):
    try:
        image = await compose_costume_list_image(request)
        return await image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/detail", summary="Generate costume detail image")
async def costume_detail(request: CostumeDetailRequest):
    try:
        image = await compose_costume_detail_image(request)
        return await image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
