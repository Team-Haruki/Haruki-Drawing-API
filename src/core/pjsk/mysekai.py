import logging

from fastapi import APIRouter

from src.core.http_responses import INTERNAL_SERVER_ERROR_RESPONSES
from src.core.image_payload import require_native_payload
from src.core.render_errors import render_http_exception
from src.core.utils import encoded_image_payload_to_response
from src.sekai.mysekai.content_drawer import (
    try_render_mysekai_blueprint_term_payload,
    try_render_mysekai_bulk_harvest_payload,
    try_render_mysekai_shop_payload,
)
from src.sekai.mysekai.housing_drawer import (
    try_render_mysekai_housing_competition_payload,
)
from src.sekai.mysekai.model import (
    MysekaiBlueprintTermRequest,
    MysekaiBulkHarvestRequest,
    MysekaiDoorUpgradeRequest,
    MysekaiFixtureDetailRequest,
    MysekaiFixtureListRequest,
    MysekaiHousingCompetitionRequest,
    MysekaiMsrMapRequest,
    MysekaiMusicrecordRequest,
    MysekaiResourceRequest,
    MysekaiShopRequest,
    MysekaiTalkListRequest,
)

router = APIRouter(tags=["MySekai"], responses=INTERNAL_SERVER_ERROR_RESPONSES)
_logger = logging.getLogger(__name__)


@router.post(
    "/resource",
    summary="Generate MySekai resource image",
    responses=INTERNAL_SERVER_ERROR_RESPONSES,
)
async def mysekai_resource(request: MysekaiResourceRequest):
    """Generate MySekai resource list image."""
    try:
        from src.sekai.mysekai.drawer import (
            try_render_mysekai_resource_payload,
        )

        payload = await try_render_mysekai_resource_payload(request)
        payload = require_native_payload(payload)
        return await encoded_image_payload_to_response(payload)
    except Exception as e:
        _logger.exception("mysekai_resource render failed")
        raise render_http_exception(e)


@router.post(
    "/map",
    summary="Generate MySekai MSR map image",
    responses=INTERNAL_SERVER_ERROR_RESPONSES,
)
async def mysekai_msr_map(request: MysekaiMsrMapRequest):
    """Generate MySekai MSR harvest map image."""
    try:
        from src.sekai.mysekai.drawer import (
            try_render_mysekai_msr_map_payload,
        )

        payload = await try_render_mysekai_msr_map_payload(request)
        payload = require_native_payload(payload)
        return await encoded_image_payload_to_response(payload)
    except Exception as e:
        _logger.exception("mysekai_msr_map render failed")
        raise render_http_exception(e)


@router.post(
    "/fixture-list",
    summary="Generate MySekai fixture list image",
    responses=INTERNAL_SERVER_ERROR_RESPONSES,
)
async def mysekai_fixture_list(request: MysekaiFixtureListRequest):
    """Generate MySekai fixture collection list image."""
    try:
        from src.sekai.mysekai.drawer import (
            try_render_mysekai_fixture_list_payload,
        )

        payload = await try_render_mysekai_fixture_list_payload(request)
        payload = require_native_payload(payload)
        return await encoded_image_payload_to_response(payload)
    except Exception as e:
        raise render_http_exception(e)


@router.post(
    "/fixture-detail",
    summary="Generate MySekai fixture detail image",
    responses=INTERNAL_SERVER_ERROR_RESPONSES,
)
async def mysekai_fixture_detail(request: list[MysekaiFixtureDetailRequest]):
    """Generate MySekai fixture detail cards image."""
    try:
        from src.sekai.mysekai.drawer import (
            try_render_mysekai_fixture_detail_payload,
        )

        payload = await try_render_mysekai_fixture_detail_payload(request)
        payload = require_native_payload(payload)
        return await encoded_image_payload_to_response(payload)
    except Exception as e:
        raise render_http_exception(e)


@router.post(
    "/door-upgrade",
    summary="Generate MySekai door upgrade image",
    responses=INTERNAL_SERVER_ERROR_RESPONSES,
)
async def mysekai_door_upgrade(request: MysekaiDoorUpgradeRequest):
    """Generate MySekai gate upgrade materials image."""
    try:
        from src.sekai.mysekai.drawer import (
            try_render_mysekai_door_upgrade_payload,
        )

        payload = await try_render_mysekai_door_upgrade_payload(request)
        payload = require_native_payload(payload)
        return await encoded_image_payload_to_response(payload)
    except Exception as e:
        raise render_http_exception(e)


@router.post(
    "/music-record",
    summary="Generate MySekai music record image",
    responses=INTERNAL_SERVER_ERROR_RESPONSES,
)
async def mysekai_music_record(request: MysekaiMusicrecordRequest):
    """Generate MySekai music record collection list image."""
    try:
        from src.sekai.mysekai.drawer import (
            try_render_mysekai_musicrecord_payload,
        )

        payload = await try_render_mysekai_musicrecord_payload(request)
        payload = require_native_payload(payload)
        return await encoded_image_payload_to_response(payload)
    except Exception as e:
        raise render_http_exception(e)


@router.post(
    "/talk-list",
    summary="Generate MySekai talk list image",
    responses=INTERNAL_SERVER_ERROR_RESPONSES,
)
async def mysekai_talk_list(request: MysekaiTalkListRequest):
    """Generate MySekai character talk collection list image."""
    try:
        from src.sekai.mysekai.drawer import (
            try_render_mysekai_talk_list_payload,
        )

        payload = await try_render_mysekai_talk_list_payload(request)
        payload = require_native_payload(payload)
        return await encoded_image_payload_to_response(payload)
    except Exception as e:
        raise render_http_exception(e)


@router.post(
    "/housing-competition",
    summary="Generate MySekai housing competition image",
    responses=INTERNAL_SERVER_ERROR_RESPONSES,
)
async def mysekai_housing_competition(request: MysekaiHousingCompetitionRequest):
    """Generate MySekai housing competition ranking cards."""
    try:
        payload = await try_render_mysekai_housing_competition_payload(request)
        payload = require_native_payload(payload)
        return await encoded_image_payload_to_response(payload)
    except Exception as e:
        _logger.exception("mysekai_housing_competition render failed")
        raise render_http_exception(e)


@router.post(
    "/shop",
    summary="Generate MySekai shop image",
    responses=INTERNAL_SERVER_ERROR_RESPONSES,
)
async def mysekai_shop(request: MysekaiShopRequest):
    """Generate the MySekai material/tool shop list (JP 7.0.0+)."""
    try:
        payload = await try_render_mysekai_shop_payload(request)
        payload = require_native_payload(payload)
        return await encoded_image_payload_to_response(payload)
    except Exception as e:
        _logger.exception("mysekai_shop render failed")
        raise render_http_exception(e)


@router.post(
    "/bulk-harvest",
    summary="Generate MySekai bulk harvest target image",
    responses=INTERNAL_SERVER_ERROR_RESPONSES,
)
async def mysekai_bulk_harvest(request: MysekaiBulkHarvestRequest):
    """Generate the per-site bulk harvest target groups (JP 7.0.0+)."""
    try:
        payload = await try_render_mysekai_bulk_harvest_payload(request)
        payload = require_native_payload(payload)
        return await encoded_image_payload_to_response(payload)
    except Exception as e:
        _logger.exception("mysekai_bulk_harvest render failed")
        raise render_http_exception(e)


@router.post(
    "/blueprint-term",
    summary="Generate MySekai blueprint term tab image",
    responses=INTERNAL_SERVER_ERROR_RESPONSES,
)
async def mysekai_blueprint_term(request: MysekaiBlueprintTermRequest):
    """Generate the limited-term / birthday-anniversary blueprint tabs (JP 7.0.0+)."""
    try:
        payload = await try_render_mysekai_blueprint_term_payload(request)
        payload = require_native_payload(payload)
        return await encoded_image_payload_to_response(payload)
    except Exception as e:
        _logger.exception("mysekai_blueprint_term render failed")
        raise render_http_exception(e)
