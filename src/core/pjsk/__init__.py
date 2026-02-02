from fastapi import APIRouter

from . import (
    card,
    chart,
    deck,
    education,
    event,
    gacha,
    honor,
    misc,
    music,
    mysekai,
    profile,
    score,
    sk,
    stamp,
)

router = APIRouter(prefix="/api")

router.include_router(card.router, prefix="/card")
router.include_router(music.router, prefix="/music")
router.include_router(profile.router, prefix="/profile")
router.include_router(event.router, prefix="/event")
router.include_router(gacha.router, prefix="/gacha")
router.include_router(honor.router, prefix="/honor")
router.include_router(score.router, prefix="/score")
router.include_router(stamp.router, prefix="/stamp")
router.include_router(misc.router, prefix="/misc")
router.include_router(education.router, prefix="/education")
router.include_router(deck.router, prefix="/deck")
router.include_router(mysekai.router, prefix="/mysekai")
router.include_router(sk.router, prefix="/sk")
router.include_router(chart.router, prefix="/chart")
