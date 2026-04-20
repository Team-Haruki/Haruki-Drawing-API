from fastapi import APIRouter, HTTPException

from src.core.utils import image_to_response
from src.sekai.base.draw import add_request_watermark_to_image
from src.sekai.chart.drawer import GenerateMusicChartRequest, generate_music_chart

router = APIRouter(tags=["Chart"])


@router.post("", summary="Generate music chart image")
async def music_chart(request: GenerateMusicChartRequest):
    try:
        image = await generate_music_chart(request)
        image = await add_request_watermark_to_image(image, request)
        return await image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
