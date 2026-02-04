from fastapi import APIRouter, HTTPException

from src.core.utils import image_to_response
from src.sekai.chart.drawer import GenerateMusicChartRequest, generate_music_chart

router = APIRouter(tags=["Chart"])


@router.post("/", summary="Generate music chart image")
async def music_chart(request: GenerateMusicChartRequest):
    try:
        image = await generate_music_chart(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
