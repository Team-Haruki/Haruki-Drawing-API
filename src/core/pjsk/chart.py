from fastapi import APIRouter, HTTPException

from src.core.utils import image_to_response
from src.sekai.base.draw import add_request_watermark_to_image
from src.sekai.chart.model import GenerateMusicChartRequest
from src.settings import JPG_QUALITY

router = APIRouter(tags=["Chart"])


@router.post("", summary="Generate music chart image")
async def music_chart(request: GenerateMusicChartRequest):
    try:
        from src.sekai.chart.drawer import generate_music_chart

        image = await generate_music_chart(request)
        image = await add_request_watermark_to_image(image, request)
        return await image_to_response(image, export_format="jpg", jpg_quality=max(JPG_QUALITY, 95), jpeg_subsampling=0)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
