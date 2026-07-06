from fastapi import APIRouter, HTTPException

from src.core.debug import set_request_stage
from src.core.utils import image_to_response
from src.sekai.misc.drawer import compose_command_help_image
from src.sekai.misc.model import CommandHelpRenderRequest

router = APIRouter(tags=["Help"])


@router.post("/render", summary="Generate command help image")
async def command_help(request: CommandHelpRenderRequest):
    try:
        set_request_stage("help:compose_image")
        image = await compose_command_help_image(request)
        set_request_stage("help:image_to_response")
        return await image_to_response(image, export_format="png")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
