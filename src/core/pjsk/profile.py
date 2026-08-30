import asyncio
import logging

from fastapi import APIRouter, HTTPException

from src.core.debug import set_request_stage
from src.core.http_responses import CUSTOM_PROFILE_ERROR_RESPONSES, INTERNAL_SERVER_ERROR_RESPONSES
from src.core.utils import encoded_image_payload_to_response, image_to_response
from src.sekai.profile.custom_profile.drawer import compose_custom_profile_card_image
from src.sekai.profile.custom_profile.limits import validate_custom_profile_card
from src.sekai.profile.custom_profile.skia import try_render_custom_profile_card_attempt
from src.sekai.profile.drawer import compose_profile_image, try_render_profile_payload
from src.sekai.profile.model import CustomProfileCardRenderRequest, ProfileRequest
from src.settings import (
    CUSTOM_PROFILE_MAX_CONCURRENT_REQUESTS,
    CUSTOM_PROFILE_MAX_ELEMENTS,
    CUSTOM_PROFILE_MAX_SCALE,
    CUSTOM_PROFILE_MAX_TEXT_LENGTH,
    CUSTOM_PROFILE_MAX_TEXT_SIZE,
)

router = APIRouter(tags=["Profile"], responses=CUSTOM_PROFILE_ERROR_RESPONSES)
logger = logging.getLogger(__name__)
_custom_profile_render_slots = asyncio.Semaphore(CUSTOM_PROFILE_MAX_CONCURRENT_REQUESTS)


@router.post(
    "",
    summary="Generate profile image",
    responses=INTERNAL_SERVER_ERROR_RESPONSES,
)
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
        if payload is not None:
            set_request_stage("profile:image_to_response")
            return encoded_image_payload_to_response(payload)
        image = await compose_profile_image(request)
        set_request_stage("profile:image_to_response")
        return await image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post(
    "/custom-profile-card",
    summary="Generate custom profile card image",
    responses=CUSTOM_PROFILE_ERROR_RESPONSES,
)
async def custom_profile_card(request: CustomProfileCardRenderRequest):
    attempt = None
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
            # Defer the aggregate backend outcome until the final HTTP result is known. A
            # canonical ValueError -> 400 is a rejected request, not production render traffic;
            # a Skia failure recovered by Pillow still commits ``error`` below.
            attempt = await try_render_custom_profile_card_attempt(request)
            attempt.tag_backend()
            payload = attempt.payload
            if payload is None:
                image = await compose_custom_profile_card_image(request)
            set_request_stage("custom_profile_card:image_to_response")
            if payload is not None:
                response = encoded_image_payload_to_response(payload)
            else:
                response = await image_to_response(image, export_format="png")
            attempt.record(response.status_code)
            return response
    except ValueError as e:
        if attempt is not None:
            attempt.reject()
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        if attempt is not None:
            attempt.record(500)
        raise HTTPException(status_code=500, detail=str(e))
