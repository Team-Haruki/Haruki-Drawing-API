from fastapi import APIRouter, HTTPException

from src.core.utils import encoded_image_payload_to_response, image_to_response
from src.sekai.chart.model import GenerateMusicChartRequest

router = APIRouter(tags=["Chart"])


@router.post("", summary="Generate music chart image")
async def music_chart(request: GenerateMusicChartRequest):
    try:
        from src.sekai.chart.drawer import compose_music_chart_image, try_render_music_chart_payload

        payload = await try_render_music_chart_payload(request)
        if payload is not None:
            return encoded_image_payload_to_response(payload)
        image = await compose_music_chart_image(request)
        return await image_to_response(image, export_format="png")
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e)) from e
