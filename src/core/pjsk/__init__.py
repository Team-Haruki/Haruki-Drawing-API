from fastapi import APIRouter

from src.core.http_responses import ARTIFACT_RESPONSES

from . import (
    card,
    chart,
    command_help,
    costume,
    deck,
    education,
    event,
    gacha,
    honor,
    inventory,
    misc,
    music,
    mysekai,
    profile,
    score,
    sk,
    stamp,
    vlive,
)

# Sub-routers are collected here and merged ONCE into the public router below, so the artifact response
# metadata is declared in exactly one place (never per route) and every path keeps one POST operation.
_routes = APIRouter()

_routes.include_router(card.router, prefix="/card")
_routes.include_router(costume.router, prefix="/costume")
_routes.include_router(music.router, prefix="/music")
_routes.include_router(profile.router, prefix="/profile")
_routes.include_router(event.router, prefix="/event")
_routes.include_router(gacha.router, prefix="/gacha")
_routes.include_router(honor.router, prefix="/honor")
_routes.include_router(score.router, prefix="/score")
_routes.include_router(stamp.router, prefix="/stamp")
_routes.include_router(misc.router, prefix="/misc")
_routes.include_router(education.router, prefix="/education")
_routes.include_router(inventory.router, prefix="/inventory")
_routes.include_router(deck.router, prefix="/deck")
_routes.include_router(mysekai.router, prefix="/mysekai")
_routes.include_router(sk.router, prefix="/sk")
_routes.include_router(chart.router, prefix="/chart")
_routes.include_router(vlive.router, prefix="/vlive")
_routes.include_router(command_help.router, prefix="/help")

router = APIRouter(prefix="/api/pjsk")
router.include_router(_routes, responses=ARTIFACT_RESPONSES)
