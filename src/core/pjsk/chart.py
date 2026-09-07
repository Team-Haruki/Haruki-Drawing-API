from fastapi import APIRouter, HTTPException

from src.core.image_payload import require_native_payload
from src.core.utils import encoded_image_payload_to_response
from src.sekai.chart.model import GenerateMusicChartRequest

router = APIRouter(tags=["Chart"])


@router.post("", summary="Generate music chart image")
async def music_chart(request: GenerateMusicChartRequest):
    try:
        from src.sekai.chart.drawer import try_render_music_chart_payload

        payload = await try_render_music_chart_payload(request)
        payload = require_native_payload(payload)
        return encoded_image_payload_to_response(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
