import logging
import time

from fastapi import APIRouter, HTTPException

from src.core.utils import image_to_response
from src.sekai.mysekai.drawer import (
    compose_mysekai_door_upgrade_image,
    compose_mysekai_fixture_detail_image,
    compose_mysekai_fixture_list_image,
    compose_mysekai_msr_map_image,
    compose_mysekai_musicrecord_image,
    compose_mysekai_resource_image,
    compose_mysekai_talk_list_image,
)
from src.sekai.mysekai.model import (
    MysekaiDoorUpgradeRequest,
    MysekaiFixtureDetailRequest,
    MysekaiFixtureListRequest,
    MysekaiMsrMapRequest,
    MysekaiMusicrecordRequest,
    MysekaiResourceRequest,
    MysekaiTalkListRequest,
)

router = APIRouter(tags=["MySekai"])
_perf_logger = logging.getLogger("mysekai.endpoint.perf")


@router.post("/resource", summary="Generate MySekai resource image")
async def mysekai_resource(request: MysekaiResourceRequest):
    """Generate MySekai resource list image."""
    try:
        image = await compose_mysekai_resource_image(request)
        return await image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/map", summary="Generate MySekai MSR map image")
async def mysekai_msr_map(request: MysekaiMsrMapRequest):
    """Generate MySekai MSR harvest map image."""
    _t0 = time.perf_counter()
    try:
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
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/fixture-list", summary="Generate MySekai fixture list image")
async def mysekai_fixture_list(request: MysekaiFixtureListRequest):
    """Generate MySekai fixture collection list image."""
    try:
        _t0 = time.perf_counter()
        image = await compose_mysekai_fixture_list_image(request)
        _t1 = time.perf_counter()
        resp = await image_to_response(image)
        _perf_logger.info(
            "/fixture-list total: %.3fs (draw=%.3fs, encode=%.3fs, image=%dx%d)",
            time.perf_counter() - _t0, _t1 - _t0, time.perf_counter() - _t1,
            image.width, image.height,
        )
        return resp
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/fixture-detail", summary="Generate MySekai fixture detail image")
async def mysekai_fixture_detail(request: list[MysekaiFixtureDetailRequest]):
    """Generate MySekai fixture detail cards image."""
    try:
        image = await compose_mysekai_fixture_detail_image(request)
        return await image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/door-upgrade", summary="Generate MySekai door upgrade image")
async def mysekai_door_upgrade(request: MysekaiDoorUpgradeRequest):
    """Generate MySekai gate upgrade materials image."""
    try:
        image = await compose_mysekai_door_upgrade_image(request)
        return await image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/music-record", summary="Generate MySekai music record image")
async def mysekai_music_record(request: MysekaiMusicrecordRequest):
    """Generate MySekai music record collection list image."""
    try:
        _t0 = time.perf_counter()
        image = await compose_mysekai_musicrecord_image(request)
        _t1 = time.perf_counter()
        resp = await image_to_response(image)
        _perf_logger.info(
            "/music-record total: %.3fs (draw=%.3fs, encode=%.3fs, image=%dx%d)",
            time.perf_counter() - _t0, _t1 - _t0, time.perf_counter() - _t1,
            image.width, image.height,
        )
        return resp
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/talk-list", summary="Generate MySekai talk list image")
async def mysekai_talk_list(request: MysekaiTalkListRequest):
    """Generate MySekai character talk collection list image."""
    try:
        _t0 = time.perf_counter()
        image = await compose_mysekai_talk_list_image(request)
        _t1 = time.perf_counter()
        resp = await image_to_response(image)
        _perf_logger.info(
            "/talk-list total: %.3fs (draw=%.3fs, encode=%.3fs, image=%dx%d)",
            time.perf_counter() - _t0, _t1 - _t0, time.perf_counter() - _t1,
            image.width, image.height,
        )
        return resp
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
