from fastapi import APIRouter, HTTPException

from src.core.utils import image_to_response
from src.sekai.education.drawer import (
    compose_area_item_upgrade_materials_image,
    compose_bonds_image,
    compose_challenge_live_detail_image,
    compose_leader_count_image,
    compose_power_bonus_detail_image,
)
from src.sekai.education.model import (
    AreaItemUpgradeMaterialsRequest,
    BondsRequest,
    ChallengeLiveDetailsRequest,
    LeaderCountRequest,
    PowerBonusDetailRequest,
)

router = APIRouter(tags=["Education"])


@router.post("/challenge-live", summary="Generate challenge live detail image")
async def challenge_live_detail(request: ChallengeLiveDetailsRequest):
    """
    Generate a challenge live detail image.

    Shows challenge live progress for all characters.
    """
    try:
        image = await compose_challenge_live_detail_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/power-bonus", summary="Generate power bonus detail image")
async def power_bonus_detail(request: PowerBonusDetailRequest):
    """
    Generate a power bonus detail image.

    Shows character, unit, and attribute bonus details.
    """
    try:
        image = await compose_power_bonus_detail_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/area-item", summary="Generate area item upgrade materials image")
async def area_item_materials(request: AreaItemUpgradeMaterialsRequest):
    """
    Generate an area item upgrade materials image.

    Shows required materials for upgrading area items.
    """
    try:
        image = await compose_area_item_upgrade_materials_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/bonds", summary="Generate bonds level image")
async def bonds_level(request: BondsRequest):
    """
    Generate a bonds level image.

    Shows character bonds levels and progress.
    """
    try:
        image = await compose_bonds_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/leader-count", summary="Generate leader count image")
async def leader_count(request: LeaderCountRequest):
    """
    Generate a leader count image.

    Shows character leader play counts and EX levels.
    """
    try:
        image = await compose_leader_count_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
