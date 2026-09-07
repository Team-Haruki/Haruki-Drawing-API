from fastapi import APIRouter, HTTPException

from src.core.debug import set_request_stage
from src.core.image_payload import require_native_payload
from src.core.utils import encoded_image_payload_to_response
from src.sekai.misc.drawer import try_render_command_help_payload
from src.sekai.misc.model import CommandHelpRenderRequest

router = APIRouter(tags=["Help"])


@router.post("/render", summary="Generate command help image")
async def command_help(request: CommandHelpRenderRequest):
    try:
        set_request_stage("help:try_render_payload")
        payload = await try_render_command_help_payload(request)
        payload = require_native_payload(payload)
        return encoded_image_payload_to_response(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
