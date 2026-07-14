import logging
import time

from fastapi import APIRouter, HTTPException

from src.core.utils import encoded_image_payload_to_response, image_to_response
from src.sekai.mysekai.drawer import (
    compose_mysekai_door_upgrade_image,
    compose_mysekai_fixture_detail_image,
    compose_mysekai_fixture_list_image,
    compose_mysekai_msr_map_image,
    compose_mysekai_musicrecord_image,
    compose_mysekai_resource_image,
    compose_mysekai_talk_list_image,
    try_render_mysekai_door_upgrade_payload,
    try_render_mysekai_fixture_detail_payload,
    try_render_mysekai_fixture_list_payload,
    try_render_mysekai_msr_map_payload,
    try_render_mysekai_musicrecord_payload,
    try_render_mysekai_resource_payload,
    try_render_mysekai_talk_list_payload,
)
from src.sekai.mysekai.housing_drawer import (
    compose_mysekai_housing_competition_image,
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
_perf_logger = logging.getLogger("mysekai.endpoint.perf")
_logger = logging.getLogger(__name__)


@router.post("/resource", summary="Generate MySekai resource image")
async def mysekai_resource(request: MysekaiResourceRequest):
    """Generate MySekai resource list image."""
    try:
        payload = await try_render_mysekai_resource_payload(request)
        if payload is not None:
            return encoded_image_payload_to_response(payload)
        image = await compose_mysekai_resource_image(request)
        return await image_to_response(image)
    except Exception as e:
        _logger.exception("mysekai_resource render failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/map", summary="Generate MySekai MSR map image")
async def mysekai_msr_map(request: MysekaiMsrMapRequest):
    """Generate MySekai MSR harvest map image."""
    _t0 = time.perf_counter()
    try:
        payload = await try_render_mysekai_msr_map_payload(request)
        if payload is not None:
            return encoded_image_payload_to_response(payload)
        image = await compose_mysekai_msr_map_image(request)
        _t1 = time.perf_counter()
        resp = await image_to_response(image)
        _perf_logger.info(
            "/map total: %.3fs (draw=%.3fs, encode=%.3fs, image=%dx%d)",
            time.perf_counter() - _t0,
            _t1 - _t0,
            time.perf_counter() - _t1,
            image.width,
            image.height,
        )
        return resp
    except Exception as e:
        _logger.exception("mysekai_msr_map render failed")
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/fixture-list", summary="Generate MySekai fixture list image")
async def mysekai_fixture_list(request: MysekaiFixtureListRequest):
    """Generate MySekai fixture collection list image."""
    try:
        payload = await try_render_mysekai_fixture_list_payload(request)
        if payload is not None:
            return encoded_image_payload_to_response(payload)
        _t0 = time.perf_counter()
        image = await compose_mysekai_fixture_list_image(request)
        _t1 = time.perf_counter()
        resp = await image_to_response(image)
        _perf_logger.info(
            "/fixture-list total: %.3fs (draw=%.3fs, encode=%.3fs, image=%dx%d)",
            time.perf_counter() - _t0,
            _t1 - _t0,
            time.perf_counter() - _t1,
            image.width,
            image.height,
        )
        return resp
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/fixture-detail", summary="Generate MySekai fixture detail image")
async def mysekai_fixture_detail(request: list[MysekaiFixtureDetailRequest]):
    """Generate MySekai fixture detail cards image."""
    try:
        payload = await try_render_mysekai_fixture_detail_payload(request)
        if payload is not None:
            return encoded_image_payload_to_response(payload)
        image = await compose_mysekai_fixture_detail_image(request)
        return await image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/door-upgrade", summary="Generate MySekai door upgrade image")
async def mysekai_door_upgrade(request: MysekaiDoorUpgradeRequest):
    """Generate MySekai gate upgrade materials image."""
    try:
        payload = await try_render_mysekai_door_upgrade_payload(request)
        if payload is not None:
            return encoded_image_payload_to_response(payload)
        image = await compose_mysekai_door_upgrade_image(request)
        return await image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/music-record", summary="Generate MySekai music record image")
async def mysekai_music_record(request: MysekaiMusicrecordRequest):
    """Generate MySekai music record collection list image."""
    try:
        payload = await try_render_mysekai_musicrecord_payload(request)
        if payload is not None:
            return encoded_image_payload_to_response(payload)
        _t0 = time.perf_counter()
        image = await compose_mysekai_musicrecord_image(request)
        _t1 = time.perf_counter()
        resp = await image_to_response(image)
        _perf_logger.info(
            "/music-record total: %.3fs (draw=%.3fs, encode=%.3fs, image=%dx%d)",
            time.perf_counter() - _t0,
            _t1 - _t0,
            time.perf_counter() - _t1,
            image.width,
            image.height,
        )
        return resp
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/talk-list", summary="Generate MySekai talk list image")
async def mysekai_talk_list(request: MysekaiTalkListRequest):
    """Generate MySekai character talk collection list image."""
    try:
        payload = await try_render_mysekai_talk_list_payload(request)
        if payload is not None:
            return encoded_image_payload_to_response(payload)
        _t0 = time.perf_counter()
        image = await compose_mysekai_talk_list_image(request)
        _t1 = time.perf_counter()
        resp = await image_to_response(image)
        _perf_logger.info(
            "/talk-list total: %.3fs (draw=%.3fs, encode=%.3fs, image=%dx%d)",
            time.perf_counter() - _t0,
            _t1 - _t0,
            time.perf_counter() - _t1,
            image.width,
            image.height,
        )
        return resp
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/housing-competition", summary="Generate MySekai housing competition image")
async def mysekai_housing_competition(request: MysekaiHousingCompetitionRequest):
    """Generate MySekai housing competition ranking cards."""
    try:
        payload = await try_render_mysekai_housing_competition_payload(request)
        if payload is not None:
            return encoded_image_payload_to_response(payload)
        _t0 = time.perf_counter()
        image = await compose_mysekai_housing_competition_image(request)
        _t1 = time.perf_counter()
        resp = await image_to_response(image)
        _perf_logger.info(
            "/housing-competition total: %.3fs (draw=%.3fs, encode=%.3fs, image=%dx%d)",
            time.perf_counter() - _t0,
            _t1 - _t0,
            time.perf_counter() - _t1,
            image.width,
            image.height,
        )
        return resp
    except Exception as e:
        _logger.exception("mysekai_housing_competition render failed")
        raise HTTPException(status_code=500, detail=str(e))
