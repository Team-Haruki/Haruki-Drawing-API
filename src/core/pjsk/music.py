from fastapi import APIRouter, HTTPException

from src.core.utils import image_to_response
from src.sekai.music.drawer import (
    compose_basic_music_rewards_image,
    compose_detail_music_rewards_image,
    compose_music_brief_list_image,
    compose_music_detail_image,
    compose_music_list_image,
    compose_play_progress_image,
)
from src.sekai.music.model import (
    BasicMusicRewardsRequest,
    DetailMusicRewardsRequest,
    MusicBriefListRequest,
    MusicDetailRequest,
    MusicListRequest,
    PlayProgressRequest,
)

router = APIRouter(tags=["Music"])


@router.post("/detail", summary="Generate music detail image")
async def music_detail(request: MusicDetailRequest):
    """
    Generate a detailed music image.

    Shows song information, difficulty levels, vocal info, and related event.
    """
    try:
        image = await compose_music_detail_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/brief-list", summary="Generate music brief list image")
async def music_brief_list(request: MusicBriefListRequest):
    """
    Generate a brief music list image.

    Shows multiple songs in a compact list format.
    """
    try:
        image = await compose_music_brief_list_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/list", summary="Generate music list image")
async def music_list(request: MusicListRequest):
    """
    Generate a music list image with user play results.

    Shows songs with user's play status and results.
    """
    try:
        image = await compose_music_list_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/progress", summary="Generate play progress image")
async def music_progress(request: PlayProgressRequest):
    """
    Generate a play progress image.

    Shows player's progress across different difficulty levels.
    """
    try:
        image = await compose_play_progress_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rewards/detail", summary="Generate detailed music rewards image")
async def music_rewards_detail(request: DetailMusicRewardsRequest):
    """
    Generate a detailed music rewards image.

    Shows remaining rewards with detailed breakdown.
    """
    try:
        image = await compose_detail_music_rewards_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/rewards/basic", summary="Generate basic music rewards image")
async def music_rewards_basic(request: BasicMusicRewardsRequest):
    """
    Generate a basic music rewards image.

    Shows remaining rewards in simplified format.
    """
    try:
        image = await compose_basic_music_rewards_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
