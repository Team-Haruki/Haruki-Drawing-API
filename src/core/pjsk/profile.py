import asyncio
import logging

from fastapi import APIRouter, HTTPException

from src.core.debug import set_request_stage
from src.core.image_payload import require_native_payload
from src.core.utils import encoded_image_payload_to_response
from src.sekai.profile.custom_profile.limits import validate_custom_profile_card
from src.sekai.profile.drawer import try_render_profile_payload
from src.sekai.profile.model import CustomProfileCardRenderRequest, ProfileRequest
from src.settings import (
    CUSTOM_PROFILE_MAX_CONCURRENT_REQUESTS,
    CUSTOM_PROFILE_MAX_ELEMENTS,
    CUSTOM_PROFILE_MAX_SCALE,
    CUSTOM_PROFILE_MAX_TEXT_LENGTH,
    CUSTOM_PROFILE_MAX_TEXT_SIZE,
)

router = APIRouter(tags=["Profile"])
logger = logging.getLogger(__name__)
_custom_profile_render_slots = asyncio.Semaphore(CUSTOM_PROFILE_MAX_CONCURRENT_REQUESTS)


async def try_render_custom_profile_card_payload(request: CustomProfileCardRenderRequest):
    # Keep the specialized renderer's imports out of ordinary profile requests and startup.
    from src.sekai.profile.custom_profile.skia import try_render_custom_profile_card_payload as render

    return await render(request, raise_errors=True)


@router.post("", summary="Generate profile image")
async def profile(request: ProfileRequest):
    """
    Generate a player profile image.

    Shows player info, rank, honors, cards, and play statistics.
    """
    try:
        set_request_stage("profile:log_request")
        logger.info(
            "profile request debug: id=%s region=%s honors=%d leader=%s honor_summary=%s",
            request.profile.id if request.profile else None,
            request.profile.region if request.profile else None,
            len(request.honors or []),
            request.profile.leader_image_path if request.profile else None,
            [
                {
                    "index": idx,
                    "honor_type": honor.honor_type,
                    "group_type": honor.group_type,
                    "honor_img_path": honor.honor_img_path,
                    "frame_img_path": honor.frame_img_path,
                    "frame_degree_level_img_path": honor.frame_degree_level_img_path,
                    "rank_img_path": honor.rank_img_path,
                }
                for idx, honor in enumerate(request.honors or [])
            ],
        )
        set_request_stage("profile:compose_image")
        payload = await try_render_profile_payload(request)
        payload = require_native_payload(payload)
        set_request_stage("profile:image_to_response")
        return encoded_image_payload_to_response(payload)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/custom-profile-card", summary="Generate custom profile card image")
async def custom_profile_card(request: CustomProfileCardRenderRequest):
    try:
        validate_custom_profile_card(
            dict(request.card),
            max_elements=CUSTOM_PROFILE_MAX_ELEMENTS,
            max_scale=CUSTOM_PROFILE_MAX_SCALE,
            max_text_size=CUSTOM_PROFILE_MAX_TEXT_SIZE,
            max_text_length=CUSTOM_PROFILE_MAX_TEXT_LENGTH,
        )
        async with _custom_profile_render_slots:
            set_request_stage("custom_profile_card:compose_image")
            payload = await try_render_custom_profile_card_payload(request)
            set_request_stage("custom_profile_card:image_to_response")
            payload = require_native_payload(payload)
            return encoded_image_payload_to_response(payload)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
