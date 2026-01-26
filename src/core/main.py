"""
Haruki Drawing API - FastAPI Core Application

This module provides RESTful API endpoints for generating various Sekai images.
All endpoints accept JSON request bodies and return PNG images.

Run with: uvicorn src.core.main:app --reload
Swagger UI: http://localhost:8000/docs
ReDoc: http://localhost:8000/redoc
"""

import io
from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from contextlib import asynccontextmanager

# Card module
from src.sekai.card.drawer import (
    compose_card_detail_image,
    compose_card_list_image,
    compose_box_image,
)
from src.sekai.card.model import (
    CardDetailRequest,
    CardListRequest,
    CardBoxRequest,
)

# Music module
from src.sekai.music.drawer import (
    compose_music_detail_image,
    compose_music_brief_list_image,
    compose_music_list_image,
    compose_play_progress_image,
    compose_detail_music_rewards_image,
    compose_basic_music_rewards_image,
)
from src.sekai.music.model import (
    MusicDetailRequest,
    MusicBriefListRequest,
    MusicListRequest,
    PlayProgressRequest,
    DetailMusicRewardsRequest,
    BasicMusicRewardsRequest,
)

# Profile module
from src.sekai.profile.drawer import compose_profile_image
from src.sekai.profile.model import ProfileRequest

# Event module
from src.sekai.event.drawer import (
    compose_event_detail_image,
    compose_event_record_image,
    compose_event_list_image,
)
from src.sekai.event.model import (
    EventDetailRequest,
    EventRecordRequest,
    EventListRequest,
)

# Gacha module
from src.sekai.gacha.drawer import (
    compose_gacha_list_image,
    compose_gacha_detail_image,
)
from src.sekai.gacha.model import (
    GachaListRequest,
    GachaDetailRequest,
)

# Honor module
from src.sekai.honor.drawer import compose_full_honor_image
from src.sekai.honor.model import HonorRequest

# Score module
from src.sekai.score.drawer import (
    compose_score_control_image,
    compose_custom_room_score_control_image,
    compose_music_meta_image,
    compose_music_board_image,
)
from src.sekai.score.model import (
    ScoreControlRequest,
    CustomRoomScoreRequest,
    MusicMetaRequest,
    MusicBoardRequest,
)

# Stamp module
from src.sekai.stamp.drawer import compose_stamp_list_image
from src.sekai.stamp.model import StampListRequest

# Misc module
from src.sekai.misc.drawer import compose_chara_birthday_image
from src.sekai.misc.model import CharaBirthdayRequest


# Education module
from src.sekai.education.drawer import (
    compose_challenge_live_detail_image,
    compose_power_bonus_detail_image,
    compose_area_item_upgrade_materials_image,
    compose_bonds_image,
    compose_leader_count_image,
)
from src.sekai.education.model import (
    ChallengeLiveDetailsRequest,
    PowerBonusDetailRequest,
    AreaItemUpgradeMaterialsRequest,
    BondsRequest,
    LeaderCountRequest,
)

# Deck module
from src.sekai.deck.drawer import compose_deck_recommend_image
from src.sekai.deck.model import DeckRequest

# MySekai module
from src.sekai.mysekai.drawer import (
    compose_mysekai_resource_image,
    compose_mysekai_fixture_list_image,
    compose_mysekai_fixture_detail_image,
    compose_mysekai_door_upgrade_image,
    compose_mysekai_musicrecord_image,
    compose_mysekai_talk_list_image,
)
from src.sekai.mysekai.model import (
    MysekaiResourceRequest,
    MysekaiFixtureListRequest,
    MysekaiFixtureDetailRequest,
    MysekaiDoorUpgradeRequest,
    MysekaiMusicrecordRequest,
    MysekaiTalkListRequest,
)

# SK (Ranking) module
from src.sekai.sk.drawer import (
    compose_skl_image,
    compose_sk_image,
    compose_cf_image,
    compose_sks_image,
    compose_player_trace_image,
    compose_rank_trace_image,
    compose_winrate_predict_image,
)
from src.sekai.sk.drawer import (
    SklRequest,
    SKRequest,
    CFRequest,
    SpeedRequest,
    PlayerTraceRequest,
    RankTraceRequest,
    WinRateRequest,
)


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown events."""
    # Startup
    print("🎹 Haruki Drawing API is starting...")
    yield
    # Shutdown
    print("🎹 Haruki Drawing API is shutting down...")


app = FastAPI(
    title="Haruki Drawing API",
    description="""
## 🎨 Haruki Drawing API

This API provides endpoints for generating various Project Sekai images.

### Available Modules:
- **Card**: Generate card detail, list, and box images
- **Music**: Generate music detail, list, progress, and rewards images
- **Profile**: Generate player profile images
- **Event**: Generate event detail, record, and list images
- **Gacha**: Generate gacha list and detail images
- **Honor**: Generate honor/badge images
- **Score**: Generate score control images
- **Stamp**: Generate stamp list images
- **Education**: Generate challenge live, power bonus, area items, bonds, and leader count images
- **Deck**: Generate deck recommendation images
- **MySekai**: Generate resource, fixture, gate, music record, and talk list images
- **SK**: Generate ranking lines, history, speed, and prediction images


### Response Format:
All endpoints return PNG images as binary stream.
    """,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)


def image_to_response(image) -> StreamingResponse:
    """Convert PIL Image to StreamingResponse."""
    buffer = io.BytesIO()
    image.save(buffer, format="PNG")
    buffer.seek(0)
    return StreamingResponse(
        buffer,
        media_type="image/png",
        headers={"Content-Disposition": "inline; filename=image.png"}
    )


# ======================= Health Check =======================

@app.get("/", tags=["Health"])
async def root():
    """API root endpoint - health check."""
    return {
        "status": "healthy",
        "message": "Welcome to Haruki Drawing API",
        "docs": "/docs",
        "redoc": "/redoc",
    }


@app.get("/health", tags=["Health"])
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


# ======================= Card Endpoints =======================

@app.post("/api/card/detail", tags=["Card"], summary="Generate card detail image")
async def card_detail(request: CardDetailRequest):
    """
    Generate a detailed card image.
    
    The image includes card information, power stats, skills, and related event/gacha info.
    """
    try:
        image = await compose_card_detail_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/card/list", tags=["Card"], summary="Generate card list image")
async def card_list(request: CardListRequest):
    """
    Generate a card list image.
    
    Shows multiple cards in a list format with optional user info.
    """
    try:
        image = await compose_card_list_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/card/box", tags=["Card"], summary="Generate card box image")
async def card_box(request: CardBoxRequest):
    """
    Generate a card box image.
    
    Shows cards organized by character with ownership status.
    """
    try:
        image = await compose_box_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ======================= Music Endpoints =======================

@app.post("/api/music/detail", tags=["Music"], summary="Generate music detail image")
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


@app.post("/api/music/brief-list", tags=["Music"], summary="Generate music brief list image")
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


@app.post("/api/music/list", tags=["Music"], summary="Generate music list image")
async def music_list(request: MusicListRequest, show_id: bool = False, show_leak: bool = False):
    """
    Generate a music list image with user play results.
    
    Shows songs with user's play status and results.
    """
    try:
        image = await compose_music_list_image(request, show_id, show_leak)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/music/progress", tags=["Music"], summary="Generate play progress image")
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


@app.post("/api/music/rewards/detail", tags=["Music"], summary="Generate detailed music rewards image")
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


@app.post("/api/music/rewards/basic", tags=["Music"], summary="Generate basic music rewards image")
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


# ======================= Profile Endpoints =======================

@app.post("/api/profile", tags=["Profile"], summary="Generate profile image")
async def profile(request: ProfileRequest):
    """
    Generate a player profile image.
    
    Shows player info, rank, honors, cards, and play statistics.
    """
    try:
        image = await compose_profile_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ======================= Event Endpoints =======================

@app.post("/api/event/detail", tags=["Event"], summary="Generate event detail image")
async def event_detail(request: EventDetailRequest):
    """
    Generate an event detail image.
    
    Shows event information, banner, and featured cards.
    """
    try:
        image = await compose_event_detail_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/event/record", tags=["Event"], summary="Generate event record image")
async def event_record(request: EventRecordRequest):
    """
    Generate an event participation record image.
    
    Shows user's event history and rankings.
    """
    try:
        image = await compose_event_record_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/event/list", tags=["Event"], summary="Generate event list image")
async def event_list(request: EventListRequest):
    """
    Generate an event list image.
    
    Shows multiple events in a list format.
    """
    try:
        image = await compose_event_list_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ======================= Gacha Endpoints =======================

@app.post("/api/gacha/list", tags=["Gacha"], summary="Generate gacha list image")
async def gacha_list(request: GachaListRequest):
    """
    Generate a gacha list image.
    
    Shows multiple gacha banners.
    """
    try:
        image = await compose_gacha_list_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/gacha/detail", tags=["Gacha"], summary="Generate gacha detail image")
async def gacha_detail(request: GachaDetailRequest):
    """
    Generate a gacha detail image.
    
    Shows gacha information, rates, and pickup cards.
    """
    try:
        image = await compose_gacha_detail_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ======================= Honor Endpoints =======================

@app.post("/api/honor", tags=["Honor"], summary="Generate honor image")
async def honor(request: HonorRequest):
    """
    Generate an honor/badge image.
    
    Supports normal, bonds, and event ranking honors.
    """
    try:
        image = await compose_full_honor_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ======================= Score Endpoints =======================

@app.post("/api/score/control", tags=["Score"], summary="Generate score control image")
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


@app.post("/api/score/custom-room", tags=["Score"], summary="Generate custom room score control image")
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


@app.post("/api/score/music-meta", tags=["Score"], summary="Generate music meta image")
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


@app.post("/api/score/music-board", tags=["Score"], summary="Generate music board image")
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


# ======================= Stamp Endpoints =======================

@app.post("/api/stamp/list", tags=["Stamp"], summary="Generate stamp list image")
async def stamp_list(request: StampListRequest):
    """
    Generate a stamp list image.
    
    Shows available stamps in a grid layout.
    """
    try:
        image = await compose_stamp_list_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ======================= Misc Endpoints =======================

@app.post("/api/misc/chara-birthday", tags=["Misc"], summary="Generate character birthday image")
async def chara_birthday(request: CharaBirthdayRequest):
    """
    Generate a character birthday info image.
    
    Shows character birthday info, upcoming dates, and birthday cards.
    """
    try:
        image = await compose_chara_birthday_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



@app.post("/api/education/challenge-live", tags=["Education"], summary="Generate challenge live detail image")
async def challenge_live_detail(request: ChallengeLiveDetailsRequest):
    """
    Generate a challenge live detail image.
    
    Shows challenge live progress for all characters.
    """
    try:
        image = await compose_challenge_live_detail_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/education/power-bonus", tags=["Education"], summary="Generate power bonus detail image")
async def power_bonus_detail(request: PowerBonusDetailRequest):
    """
    Generate a power bonus detail image.
    
    Shows character, unit, and attribute bonus details.
    """
    try:
        image = await compose_power_bonus_detail_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/education/area-item", tags=["Education"], summary="Generate area item upgrade materials image")
async def area_item_materials(request: AreaItemUpgradeMaterialsRequest):
    """
    Generate an area item upgrade materials image.
    
    Shows required materials for upgrading area items.
    """
    try:
        image = await compose_area_item_upgrade_materials_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/education/bonds", tags=["Education"], summary="Generate bonds level image")
async def bonds_level(request: BondsRequest):
    """
    Generate a bonds level image.
    
    Shows character bonds levels and progress.
    """
    try:
        image = await compose_bonds_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/education/leader-count", tags=["Education"], summary="Generate leader count image")
async def leader_count(request: LeaderCountRequest):
    """
    Generate a leader count image.
    
    Shows character leader play counts and EX levels.
    """
    try:
        image = await compose_leader_count_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

# ======================= Deck Endpoints =======================

@app.post("/api/deck/recommend", tags=["Deck"], summary="Generate deck recommendation image")
async def deck_recommend(request: DeckRequest):
    """
    Generate a deck recommendation image.
    
    Provides card recommendations for specific events or songs based on optimization targets.
    """
    try:
        image = await compose_deck_recommend_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ======================= MySekai Endpoints =======================

@app.post("/api/mysekai/resource", tags=["MySekai"], summary="Generate MySekai resource image")
async def mysekai_resource(request: MysekaiResourceRequest):
    """Generate MySekai resource list image."""
    try:
        image = await compose_mysekai_resource_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/mysekai/fixture-list", tags=["MySekai"], summary="Generate MySekai fixture list image")
async def mysekai_fixture_list(request: MysekaiFixtureListRequest):
    """Generate MySekai fixture collection list image."""
    try:
        image = await compose_mysekai_fixture_list_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/mysekai/fixture-detail", tags=["MySekai"], summary="Generate MySekai fixture detail image")
async def mysekai_fixture_detail(request: list[MysekaiFixtureDetailRequest]):
    """Generate MySekai fixture detail cards image."""
    try:
        image = await compose_mysekai_fixture_detail_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/mysekai/door-upgrade", tags=["MySekai"], summary="Generate MySekai door upgrade image")
async def mysekai_door_upgrade(request: MysekaiDoorUpgradeRequest):
    """Generate MySekai gate upgrade materials image."""
    try:
        image = await compose_mysekai_door_upgrade_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/mysekai/music-record", tags=["MySekai"], summary="Generate MySekai music record image")
async def mysekai_music_record(request: MysekaiMusicrecordRequest):
    """Generate MySekai music record collection list image."""
    try:
        image = await compose_mysekai_musicrecord_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/mysekai/talk-list", tags=["MySekai"], summary="Generate MySekai talk list image")
async def mysekai_talk_list(request: MysekaiTalkListRequest):
    """Generate MySekai character talk collection list image."""
    try:
        image = await compose_mysekai_talk_list_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


# ======================= SK (Ranking) Endpoints =======================

@app.post("/api/sk/line", tags=["SK"], summary="Generate ranking line image")
async def sk_line(request: SklRequest, full: bool = False):
    """Generate event ranking line list image."""
    try:
        image = await compose_skl_image(request, full)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sk/query", tags=["SK"], summary="Generate sk image")
async def sk_query(request: SKRequest):
    """Generate sk image."""
    try:
        image = await compose_sk_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sk/check-room", tags=["SK"], summary="Generate check room image")
async def sk_check_room(request: CFRequest):
    """Generate 'Check Room' participation record image."""
    try:
        image = await compose_cf_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sk/speed", tags=["SK"], summary="Generate ranking speed image")
async def sk_speed(request: SpeedRequest):
    """Generate event ranking speed list image."""
    try:
        image = await compose_sks_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sk/player-trace", tags=["SK"], summary="Generate player trace image")
async def sk_player_trace(request: PlayerTraceRequest):
    """Generate player point trace chart image."""
    try:
        image = await compose_player_trace_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sk/rank-trace", tags=["SK"], summary="Generate rank trace image")
async def sk_rank_trace(request: RankTraceRequest):
    """Generate ranking line trace and prediction chart image."""
    try:
        image = await compose_rank_trace_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))


@app.post("/api/sk/winrate", tags=["SK"], summary="Generate winrate prediction image")
async def sk_winrate(request: WinRateRequest):
    """Generate Cheerful Live team winrate prediction image."""
    try:
        image = await compose_winrate_predict_image(request)
        return image_to_response(image)
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))



if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
