from fastapi import APIRouter, HTTPException

from src.core.utils import image_to_response
from src.sekai.mysekai.drawer import (
    compose_mysekai_door_upgrade_image,
    compose_mysekai_fixture_detail_image,
    compose_mysekai_fixture_list_image,
    compose_mysekai_musicrecord_image,
    compose_mysekai_resource_image,
    compose_mysekai_talk_list_image,
)
from src.sekai.mysekai.model import (
    MysekaiDoorUpgradeRequest,
    MysekaiFixtureDetailRequest,
    MysekaiFixtureListRequest,
    MysekaiMusicrecordRequest,
    MysekaiResourceRequest,
    MysekaiTalkListRequest,
)

router = APIRouter(tags=["MySekai"])


@router.post("/resource", summary="Generate MySekai resource image")
async def mysekai_resource(request: MysekaiResourceRequest):
    """Generate MySekai resource list image."""
    try:
        image = await compose_mysekai_resource_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/fixture-list", summary="Generate MySekai fixture list image")
async def mysekai_fixture_list(request: MysekaiFixtureListRequest):
    """Generate MySekai fixture collection list image."""
    try:
        image = await compose_mysekai_fixture_list_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/fixture-detail", summary="Generate MySekai fixture detail image")
async def mysekai_fixture_detail(request: list[MysekaiFixtureDetailRequest]):
    """Generate MySekai fixture detail cards image."""
    try:
        image = await compose_mysekai_fixture_detail_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/door-upgrade", summary="Generate MySekai door upgrade image")
async def mysekai_door_upgrade(request: MysekaiDoorUpgradeRequest):
    """Generate MySekai gate upgrade materials image."""
    try:
        image = await compose_mysekai_door_upgrade_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/music-record", summary="Generate MySekai music record image")
async def mysekai_music_record(request: MysekaiMusicrecordRequest):
    """Generate MySekai music record collection list image."""
    try:
        image = await compose_mysekai_musicrecord_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/talk-list", summary="Generate MySekai talk list image")
async def mysekai_talk_list(request: MysekaiTalkListRequest):
    """Generate MySekai character talk collection list image."""
    try:
        image = await compose_mysekai_talk_list_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
