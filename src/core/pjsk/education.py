import traceback

from fastapi import APIRouter, HTTPException

from src.core.utils import encoded_image_payload_to_response, image_to_response
from src.sekai.education.drawer import (
    compose_area_item_upgrade_materials_image,
    compose_bonds_image,
    compose_challenge_live_detail_image,
    compose_character_mission_all_image,
    compose_character_mission_overview_image,
    compose_leader_count_image,
    compose_power_bonus_detail_image,
    try_render_area_item_upgrade_materials_payload,
    try_render_bonds_payload,
    try_render_challenge_live_detail_payload,
    try_render_character_mission_all_payload,
    try_render_character_mission_overview_payload,
    try_render_leader_count_payload,
    try_render_power_bonus_detail_payload,
)
from src.sekai.education.model import (
    AreaItemUpgradeMaterialsRequest,
    BondsRequest,
    ChallengeLiveDetailsRequest,
    CharacterMissionAllRequest,
    CharacterMissionOverviewRequest,
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
        payload = await try_render_challenge_live_detail_payload(request)
        if payload is not None:
            return encoded_image_payload_to_response(payload)
        image = await compose_challenge_live_detail_image(request)
        return await image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/power-bonus", summary="Generate power bonus detail image")
async def power_bonus_detail(request: PowerBonusDetailRequest):
    """
    Generate a power bonus detail image.

    Shows character, unit, and attribute bonus details.
    """
    try:
        payload = await try_render_power_bonus_detail_payload(request)
        if payload is not None:
            return encoded_image_payload_to_response(payload)
        image = await compose_power_bonus_detail_image(request)
        return await image_to_response(image)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/area-item", summary="Generate area item upgrade materials image")
async def area_item_materials(request: AreaItemUpgradeMaterialsRequest):
    """
    Generate an area item upgrade materials image.

    Shows required materials for upgrading area items.
    """
    try:
        payload = await try_render_area_item_upgrade_materials_payload(request)
        if payload is not None:
            return encoded_image_payload_to_response(payload)
        image = await compose_area_item_upgrade_materials_image(request)
        return await image_to_response(image)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/bonds", summary="Generate bonds level image")
async def bonds_level(request: BondsRequest):
    """
    Generate a bonds level image.

    Shows character bonds levels and progress.
    """
    try:
        payload = await try_render_bonds_payload(request)
        if payload is not None:
            return encoded_image_payload_to_response(payload)
        image = await compose_bonds_image(request)
        return await image_to_response(image)
    except Exception as e:
        traceback.print_exc()
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/leader-count", summary="Generate leader count image")
async def leader_count(request: LeaderCountRequest):
    """
    Generate a leader count image.

    Shows character leader play counts and EX levels.
    """
    try:
        payload = await try_render_leader_count_payload(request)
        if payload is not None:
            return encoded_image_payload_to_response(payload)
        image = await compose_leader_count_image(request)
        return await image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/character-mission-overview", summary="Generate character mission overview image")
async def character_mission_overview(request: CharacterMissionOverviewRequest):
    try:
        payload = await try_render_character_mission_overview_payload(request)
        if payload is not None:
            return encoded_image_payload_to_response(payload)
        image = await compose_character_mission_overview_image(request)
        return await image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/character-mission-all", summary="Generate character mission full table image")
async def character_mission_all(request: CharacterMissionAllRequest):
    try:
        payload = await try_render_character_mission_all_payload(request)
        if payload is not None:
            return encoded_image_payload_to_response(payload)
        image = await compose_character_mission_all_image(request)
        return await image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
