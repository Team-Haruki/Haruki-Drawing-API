"""
Haruki Drawing API - FastAPI Core Application

This module provides RESTful API endpoints for generating various Sekai images.
All endpoints accept JSON request bodies and return PNG images.

Run with: uvicorn src.core.main:app --reload
Swagger UI: http://localhost:8000/docs
ReDoc: http://localhost:8000/redoc
"""

from contextlib import asynccontextmanager
import logging

import coloredlogs
from fastapi import FastAPI
import uvicorn

from src.core import health
from src.core.pjsk import router as pjsk_router
from src.settings import (
    FIELD_STYLE,
    LOG_FORMAT,
    SERVER_HOST,
    SERVER_PORT,
)

logger = logging.getLogger(__name__)
_description = """
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
    """


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan handler for startup/shutdown events."""
    # Configure coloredlogs
    coloredlogs.install(level="INFO", fmt=LOG_FORMAT, field_styles=FIELD_STYLE)

    # Startup
    logger.info("Haruki Drawing API is starting...")
    yield
    # Shutdown
    logger.info("Haruki Drawing API is shutting down...")


app = FastAPI(
    title="Haruki Drawing API",
    description=_description,
    version="1.0.0",
    lifespan=lifespan,
    docs_url="/docs",
    redoc_url="/redoc",
)


# ======================= Include Routers =======================

app.include_router(health.router)
app.include_router(pjsk_router)


if __name__ == "__main__":
    uvicorn.run(app, host=SERVER_HOST, port=SERVER_PORT)
