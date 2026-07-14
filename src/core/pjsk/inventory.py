from fastapi import APIRouter, HTTPException

from src.core.utils import encoded_image_payload_to_response, image_to_response
from src.sekai.inventory.drawer import compose_inventory_list_image, try_render_inventory_list_payload
from src.sekai.inventory.model import InventoryListRequest

router = APIRouter(tags=["Inventory"])


@router.post("/list", summary="Generate inventory list image")
async def inventory_list(request: InventoryListRequest):
    try:
        payload = await try_render_inventory_list_payload(request)
        if payload is not None:
            return encoded_image_payload_to_response(payload)
        image = await compose_inventory_list_image(request)
        return await image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
