import traceback

from fastapi import APIRouter, HTTPException

from src.core.utils import image_to_response
from src.sekai.vlive.drawer import compose_vlive_list_image
from src.sekai.vlive.model import VLiveListRequest

router = APIRouter(tags=["VLive"])


@router.post("/list", summary="Generate virtual live list image")
async def vlive_list(request: VLiveListRequest):
    """
    Generate a virtual live list image.

    Shows recent and upcoming virtual lives in a reminder-style list.
    """
    try:
        image = await compose_vlive_list_image(request)
        return await image_to_response(image)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))
