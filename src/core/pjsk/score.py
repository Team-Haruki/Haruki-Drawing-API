from fastapi import APIRouter, HTTPException

from src.core.utils import image_to_response
from src.sekai.score.drawer import (
    compose_custom_room_score_control_image,
    compose_music_board_image,
    compose_music_meta_image,
    compose_score_control_image,
)
from src.sekai.score.model import (
    CustomRoomScoreRequest,
    MusicBoardRequest,
    MusicMetaRequest,
    ScoreControlRequest,
)

router = APIRouter(tags=["Score"])


@router.post("/control", summary="Generate score control image")
async def score_control(request: ScoreControlRequest):
    """
    Generate a score control guide image.

    Shows optimal score ranges for event point control.
    """
    try:
        image = await compose_score_control_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/custom-room", summary="Generate custom room score control image")
async def custom_room_score_control(request: CustomRoomScoreRequest):
    """
    Generate a custom room score control image.

    Shows valid event bonus and song combinations for small PT targets.
    """
    try:
        image = await compose_custom_room_score_control_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/music-meta", summary="Generate music meta image")
async def music_meta(request: list[MusicMetaRequest]):
    """
    Generate a music meta info image.

    Shows detailed stats (diff, time, efficiency) for one or more songs.
    """
    try:
        image = await compose_music_meta_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/music-board", summary="Generate music board image")
async def music_board(request: MusicBoardRequest):
    """
    Generate a music leaderboard image.

    Shows ranking of songs based on score, efficiency, time etc.
    """
    try:
        image = await compose_music_board_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
