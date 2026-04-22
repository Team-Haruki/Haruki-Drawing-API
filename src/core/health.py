from fastapi import APIRouter

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


@router.get("/cache/stats")
async def cache_stats():
    """Runtime cache stats endpoint."""
    return {
        "status": "healthy",
        "caches": get_runtime_cache_stats(),
    }
