from fastapi import APIRouter

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
