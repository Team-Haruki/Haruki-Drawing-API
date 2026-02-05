from fastapi import APIRouter, HTTPException

from src.core.utils import image_to_response
from src.sekai.sk.drawer import (
    CFRequest,
    PlayerTraceRequest,
    RankTraceRequest,
    SklRequest,
    SKRequest,
    SpeedRequest,
    WinRateRequest,
    compose_cf_image,
    compose_player_trace_image,
    compose_rank_trace_image,
    compose_sk_image,
    compose_skl_image,
    compose_sks_image,
    compose_winrate_predict_image,
)

router = APIRouter(tags=["SK"])


@router.post("/line", summary="Generate ranking line image")
async def sk_line(request: SklRequest):
    """Generate event ranking line list image."""
    try:
        image = await compose_skl_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/query", summary="Generate sk image")
async def sk_query(request: SKRequest):
    """Generate sk image."""
    try:
        image = await compose_sk_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/check-room", summary="Generate check room image")
async def sk_check_room(request: CFRequest):
    """Generate 'Check Room' participation record image."""
    try:
        image = await compose_cf_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/speed", summary="Generate ranking speed image")
async def sk_speed(request: SpeedRequest):
    """Generate event ranking speed list image."""
    try:
        image = await compose_sks_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/player-trace", summary="Generate player trace image")
async def sk_player_trace(request: PlayerTraceRequest):
    """Generate player point trace chart image."""
    try:
        image = await compose_player_trace_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rank-trace", summary="Generate rank trace image")
async def sk_rank_trace(request: RankTraceRequest):
    """Generate ranking line trace and prediction chart image."""
    try:
        image = await compose_rank_trace_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/winrate", summary="Generate winrate prediction image")
async def sk_winrate(request: WinRateRequest):
    """Generate Cheerful Live team winrate prediction image."""
    try:
        image = await compose_winrate_predict_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
