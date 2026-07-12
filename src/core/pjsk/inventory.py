from fastapi import APIRouter, HTTPException

from src.core.utils import image_to_response
from src.sekai.inventory.drawer import compose_inventory_list_image
from src.sekai.inventory.model import InventoryListRequest

router = APIRouter(tags=["Inventory"])


@router.post("/list", summary="Generate inventory list image")
async def inventory_list(request: InventoryListRequest):
    try:
        image = await compose_inventory_list_image(request)
        return await image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
