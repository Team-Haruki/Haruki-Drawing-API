from fastapi import APIRouter
from fastapi.responses import JSONResponse

from src.core.debug import evaluate_runtime_readiness, runtime_readiness_thresholds
from src.sekai.base.utils import get_runtime_cache_stats

router = APIRouter(tags=["Health"])


@router.get("/")
async def root():
    """API root endpoint - health check."""
    return {
        "status": "healthy",
        "message": "Welcome to Haruki Drawing API",
        "docs": "/docs",
        "redoc": "/redoc",
    }


@router.get("/health")
async def health_check():
    """Health check endpoint."""
    return {"status": "healthy"}


@router.get("/ready")
async def readiness_check():
    """Readiness endpoint with runtime overload awareness."""
    ready, reasons, metrics = evaluate_runtime_readiness()
    payload = {
        "status": "ready" if ready else "not_ready",
        "reasons": reasons,
        "metrics": metrics,
        "thresholds": runtime_readiness_thresholds(),
    }
    if ready:
        return payload
    return JSONResponse(status_code=503, content=payload)


@router.get("/cache/stats")
async def cache_stats():
    """Runtime cache stats endpoint."""
    return {
        "status": "healthy",
        "caches": get_runtime_cache_stats(),
    }
