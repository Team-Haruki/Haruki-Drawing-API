import logging

from fastapi import APIRouter, HTTPException

from src.core.debug import set_request_stage
from src.core.utils import encoded_image_payload_to_response, image_to_response
from src.sekai.profile.custom_profile.drawer import compose_custom_profile_card_image
from src.sekai.profile.custom_profile.skia import try_render_custom_profile_card_payload
from src.sekai.profile.drawer import compose_profile_image, try_render_profile_payload
from src.sekai.profile.model import CustomProfileCardRenderRequest, ProfileRequest

router = APIRouter(tags=["Profile"])
logger = logging.getLogger(__name__)


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
        if payload is not None:
            set_request_stage("profile:image_to_response")
            return encoded_image_payload_to_response(payload)
        image = await compose_profile_image(request)
        set_request_stage("profile:image_to_response")
        return await image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/custom-profile-card", summary="Generate custom profile card image")
async def custom_profile_card(request: CustomProfileCardRenderRequest):
    try:
        set_request_stage("custom_profile_card:compose_image")
        # Skia-first: try_render never raises (fail-open records one outcome and returns None),
        # so an unrenderable card still reaches the Pillow compose and raises the canonical
        # ValueError -> 400 below.
        payload = await try_render_custom_profile_card_payload(request)
        if payload is not None:
            set_request_stage("custom_profile_card:image_to_response")
            return encoded_image_payload_to_response(payload)
        image = await compose_custom_profile_card_image(request)
        set_request_stage("custom_profile_card:image_to_response")
        return await image_to_response(image, export_format="png")
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
