from fastapi import APIRouter, HTTPException

from src.core.image_payload import require_native_payload
from src.core.utils import encoded_image_payload_to_response
from src.sekai.inventory.drawer import try_render_inventory_list_payload
from src.sekai.inventory.model import InventoryListRequest

router = APIRouter(tags=["Inventory"])


@router.post("/list", summary="Generate inventory list image")
async def inventory_list(request: InventoryListRequest):
    try:
        payload = await try_render_inventory_list_payload(request)
        payload = require_native_payload(payload)
        return encoded_image_payload_to_response(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
