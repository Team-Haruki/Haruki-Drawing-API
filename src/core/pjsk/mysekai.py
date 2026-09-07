import logging

from fastapi import APIRouter, HTTPException

from src.core.image_payload import require_native_payload
from src.core.utils import encoded_image_payload_to_response
from src.sekai.mysekai.housing_drawer import (
    try_render_mysekai_housing_competition_payload,
)
from src.sekai.mysekai.model import (
    MysekaiDoorUpgradeRequest,
    MysekaiFixtureDetailRequest,
    MysekaiFixtureListRequest,
    MysekaiHousingCompetitionRequest,
    MysekaiMsrMapRequest,
    MysekaiMusicrecordRequest,
    MysekaiResourceRequest,
    MysekaiTalkListRequest,
)

router = APIRouter(tags=["MySekai"])
_logger = logging.getLogger(__name__)


@router.post("/resource", summary="Generate MySekai resource image")
async def mysekai_resource(request: MysekaiResourceRequest):
    """Generate MySekai resource list image."""
    try:
        from src.sekai.mysekai.drawer import (
            try_render_mysekai_resource_payload,
        )

        payload = await try_render_mysekai_resource_payload(request)
        payload = require_native_payload(payload)
        return encoded_image_payload_to_response(payload)
    except Exception as e:
        _logger.exception("mysekai_resource render failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/map", summary="Generate MySekai MSR map image")
async def mysekai_msr_map(request: MysekaiMsrMapRequest):
    """Generate MySekai MSR harvest map image."""
    try:
        from src.sekai.mysekai.drawer import (
            try_render_mysekai_msr_map_payload,
        )

        payload = await try_render_mysekai_msr_map_payload(request)
        payload = require_native_payload(payload)
        return encoded_image_payload_to_response(payload)
    except Exception as e:
        _logger.exception("mysekai_msr_map render failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/fixture-list", summary="Generate MySekai fixture list image")
async def mysekai_fixture_list(request: MysekaiFixtureListRequest):
    """Generate MySekai fixture collection list image."""
    try:
        from src.sekai.mysekai.drawer import (
            try_render_mysekai_fixture_list_payload,
        )

        payload = await try_render_mysekai_fixture_list_payload(request)
        payload = require_native_payload(payload)
        return encoded_image_payload_to_response(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/fixture-detail", summary="Generate MySekai fixture detail image")
async def mysekai_fixture_detail(request: list[MysekaiFixtureDetailRequest]):
    """Generate MySekai fixture detail cards image."""
    try:
        from src.sekai.mysekai.drawer import (
            try_render_mysekai_fixture_detail_payload,
        )

        payload = await try_render_mysekai_fixture_detail_payload(request)
        payload = require_native_payload(payload)
        return encoded_image_payload_to_response(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/door-upgrade", summary="Generate MySekai door upgrade image")
async def mysekai_door_upgrade(request: MysekaiDoorUpgradeRequest):
    """Generate MySekai gate upgrade materials image."""
    try:
        from src.sekai.mysekai.drawer import (
            try_render_mysekai_door_upgrade_payload,
        )

        payload = await try_render_mysekai_door_upgrade_payload(request)
        payload = require_native_payload(payload)
        return encoded_image_payload_to_response(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/music-record", summary="Generate MySekai music record image")
async def mysekai_music_record(request: MysekaiMusicrecordRequest):
    """Generate MySekai music record collection list image."""
    try:
        from src.sekai.mysekai.drawer import (
            try_render_mysekai_musicrecord_payload,
        )

        payload = await try_render_mysekai_musicrecord_payload(request)
        payload = require_native_payload(payload)
        return encoded_image_payload_to_response(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/talk-list", summary="Generate MySekai talk list image")
async def mysekai_talk_list(request: MysekaiTalkListRequest):
    """Generate MySekai character talk collection list image."""
    try:
        from src.sekai.mysekai.drawer import (
            try_render_mysekai_talk_list_payload,
        )

        payload = await try_render_mysekai_talk_list_payload(request)
        payload = require_native_payload(payload)
        return encoded_image_payload_to_response(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/housing-competition", summary="Generate MySekai housing competition image")
async def mysekai_housing_competition(request: MysekaiHousingCompetitionRequest):
    """Generate MySekai housing competition ranking cards."""
    try:
        payload = await try_render_mysekai_housing_competition_payload(request)
        payload = require_native_payload(payload)
        return encoded_image_payload_to_response(payload)
    except Exception as e:
        _logger.exception("mysekai_housing_competition render failed")
        raise HTTPException(status_code=500, detail=str(e))
