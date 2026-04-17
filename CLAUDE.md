# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Haruki Drawing API is a FastAPI-based image generation service for Project Sekai (プロセカ). It accepts JSON payloads and returns rendered PNG/JPG images (player profiles, cards, events, music, gacha, scores, charts, etc.). It requires **CPython 3.14 free-threaded** (`-X gil=0`) and uses Granian as the ASGI server.

## Commands

```bash
# Run locally (requires Python 3.14t)
python -X gil=0 -m granian --interface asgi --host 0.0.0.0 --port 8000 src.core.main:app

# Run with uv
uv run granian --interface asgi --host 0.0.0.0 --port 8000 src.core.main:app

# Lint & format
uv run ruff check src/
uv run ruff format src/

# Docker
docker compose up --build

# Load test (example)
python scripts/concurrent_fetch_images.py --base-url http://127.0.0.1:8000 --endpoint /api/pjsk/sk/query --payload-file out/ci-sk-trend/sk_query_payload.json --requests 20 --concurrency 4 --output-dir out/ci-sk-load-query --save-errors
```

## Architecture

**Entrypoint**: `src.core.main:app` — FastAPI app with lifespan handler that enforces free-threaded runtime, sets up logging, and schedules periodic tmp cleanup.

**Three-layer structure**:
- `src/core/` — FastAPI routers and endpoint definitions. `src/core/pjsk/` contains one router module per feature (card, music, profile, event, sk, chart, etc.), all mounted under `/api/pjsk/`.
- `src/sekai/` — Domain logic and image drawing. Each subdirectory (card, music, profile, sk, etc.) contains models and drawer/rendering code. `src/sekai/base/` provides shared Pillow drawing utilities (`painter.py`, `draw.py`, `img_utils.py`, `plot.py`) and a thread/process pool executor (`utils.py`).
- `src/settings.py` — Pydantic-settings singleton loaded from `configs.yaml`. Access via `from src.settings import settings` or convenience exports like `ASSETS_BASE_DIR`, `DEFAULT_FONT`.

**Key design decisions**:
- Requires free-threaded Python (no-GIL). The lifespan handler calls `_ensure_nogil_runtime()` and will refuse to start with GIL enabled.
- CPU-intensive image rendering is offloaded to a thread pool (and optionally a process pool) managed in `src/sekai/base/`.
- Static assets (fonts, images, triangles) live in `data/` and are configured via `configs.yaml`. In Docker, this is mounted at `/pjskdata/Data`.
- A companion `screenshot-service` (separate repo) handles browser-based screenshot rendering; this API calls it via HTTP at `drawing.screenshot_api_path`.

## Configuration

`configs.yaml` at project root (or `configs.docker.yaml` in Docker). Environment variables with `HARUKI_` prefix and `__` nesting also work (e.g., `HARUKI_DRAWING__THREAD_POOL_SIZE=16`).

## CI

GitHub Actions workflow (`.github/workflows/free-threaded-smoke.yml`) runs on every push/PR: installs 3.14t, verifies no-GIL imports, compiles all source, runs concurrency smoke tests, and compares GIL vs no-GIL benchmark throughput.

## Code Style

Ruff with line-length 120. See `pyproject.toml [tool.ruff]` for the full rule set. Notable: isort via ruff, pyupgrade rules enabled, `RUF001-003` (ambiguous unicode) ignored since the codebase contains CJK text.
