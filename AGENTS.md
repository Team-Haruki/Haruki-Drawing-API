# Repository Guide for AI Coding Agents

This file provides guidance for AI coding assistants (Claude Code, GitHub Copilot, Codex, etc.) working in this repository. It is mirrored as `CLAUDE.md`, `AGENTS.md`, and `.github/copilot-instructions.md`.

## Project Overview

Haruki Drawing API is a FastAPI-based image generation service for Project Sekai (プロセカ). It accepts JSON payloads and returns rendered PNG/JPG images (player profiles, cards, events, music, gacha, scores, charts, MySekai, etc.). It requires **CPython 3.14 free-threaded** (`-X gil=0`, a.k.a. 3.14t) and uses Granian as the ASGI server.

## Commands

```bash
# Run locally (requires Python 3.14t)
python -X gil=0 -m granian --interface asgi --host 0.0.0.0 --port 8000 src.core.main:app

# Run with uv
uv run granian --interface asgi --host 0.0.0.0 --port 8000 src.core.main:app

# Lint & format (ruff is the only linter; line-length 120)
uv run ruff check src/
uv run ruff format src/

# Docker
docker compose up --build

# Load test (example)
python scripts/concurrent_fetch_images.py --base-url http://127.0.0.1:8000 \
  --endpoint /api/pjsk/sk/query --payload-file out/ci-sk-trend/sk_query_payload.json \
  --requests 20 --concurrency 4 --output-dir out/ci-sk-load-query --save-errors
```

## Architecture

**Entrypoint**: `src.core.main:app` — FastAPI app with a lifespan handler that enforces the free-threaded runtime, sets up logging, and schedules periodic tmp cleanup.

**Three-layer structure**:
- `src/core/` — FastAPI routers and endpoint definitions. `src/core/pjsk/` contains one router module per feature (card, music, profile, event, sk, chart, mysekai, etc.), all mounted under `/api/pjsk/`.
- `src/sekai/` — Domain logic and image drawing. Each subdirectory (card, music, profile, sk, mysekai, ...) contains models and drawer/rendering code. `src/sekai/base/` provides shared Pillow utilities (`painter.py`, `draw.py`, `img_utils.py`, `plot.py`) and the thread/process pool executor + image cache infrastructure (`utils.py`).
- `src/settings.py` — Pydantic-settings singleton loaded from `configs.yaml`. Access via `from src.settings import settings` or convenience module-level exports (e.g. `ASSETS_BASE_DIR`, `DEFAULT_FONT`, `EXPORT_IMAGE_FORMAT`, `JPG_QUALITY`).

**Key design decisions**:
- Requires free-threaded Python (no-GIL). The lifespan handler calls `_ensure_nogil_runtime()` and refuses to start when the GIL is enabled.
- CPU-intensive Pillow rendering is offloaded via `run_in_pool()` to a thread pool (and optionally a process pool gated by a pixel threshold) managed in `src/sekai/base/utils.py`.
- All multi-step image loading should use `asyncio.gather` to overlap I/O + decoding across threads. See `docs/optimizations.md` §3.
- Static assets (fonts, images, triangles) live in `data/` and are configured via `configs.yaml`. In Docker, the host data directory is mounted at `/pjskdata/Data`.
- A companion `screenshot-service` (separate repo) handles browser-based screenshot rendering; this API calls it via HTTP at `drawing.screenshot_api_path`.

## Image Cache Infrastructure (`src/sekai/base/utils.py`)

Three independent caches, all keyed by `(full_path, mtime_ns, file_size, target_w, target_h)`:

| Cache | Routing | Typical Use |
|---|---|---|
| `_image_cache` | general (default) | site backgrounds, card arts, large assets |
| `_thumb_cache` | paths containing `"thumbnail"` | small icons, high reuse, high count |
| `_composed_image_cache` | explicitly via composed-cache APIs | end-result PNG/JPG bundles, TTL-based |

Resize results are **also** cached in the same general/thumb pool — see `get_img_resized()` and `get_img_resized_long_edge()`. Always prefer these over per-request `dict` caches; the global pool persists across requests.

Sizes are configured in `configs.yaml` under `drawing.*` and exported as `IMAGE_CACHE_SIZE`, `THUMB_CACHE_SIZE`, `COMPOSED_IMAGE_CACHE_SIZE`, with their `*_MAX_BYTES` companions. Setting any size to `0` disables that pool.

## Configuration

`configs.yaml` at the project root (or `configs.docker.yaml` in Docker). Environment variables with the `HARUKI_` prefix and `__` nesting also work (e.g. `HARUKI_DRAWING__THREAD_POOL_SIZE=16`).

Notable `drawing.*` keys:
- `thread_pool_size` — default thread pool size (CPU-bound rendering).
- `use_process_pool`, `process_pool_workers`, `process_pool_threshold` — optional process-pool offload for very large images.
- `image_cache_size` / `image_cache_max_mb` — general image LRU.
- `thumbnail_cache_size` / `thumbnail_cache_max_mb` — dedicated thumbnail LRU (recommend 4096 / 256MB).
- `composed_image_cache_size` / `composed_image_cache_max_mb` / `composed_image_cache_ttl_seconds` — final-output cache.
- `export_image_format` — `"png"` or `"jpg"`.
- `jpg_quality` — JPEG quality (1–100), only applied when format is `"jpg"`.
- `screenshot_api_path` — URL of the companion screenshot service.

## Proprietary File: `src/sekai/mysekai/drawer.py`

This file is a **public placeholder stub** in the open-source repository. It exports the same async function signatures consumed by `src/core/pjsk/mysekai.py` but raises `NotImplementedError` at runtime.

For deployment:
- The real implementation lives locally as `src/sekai/mysekai/drawer.real.py` (gitignored).
- For bare-metal: rename `drawer.real.py` → `drawer.py` before launching.
- For Docker: bind-mount the real file over the stub (see commented-out volume in `docker-compose.yaml`).

**Do not delete or rewrite `drawer.real.py` if it exists locally** — it is the production implementation. Only modify `drawer.py` (the stub) when the public API surface needs to change.

## CI

GitHub Actions workflow `.github/workflows/free-threaded-smoke.yml` runs on every push/PR: installs 3.14t, verifies no-GIL imports, compiles all source, runs concurrency smoke tests, and compares GIL vs no-GIL benchmark throughput.

## Code Style

Ruff with `line-length = 120`. See `pyproject.toml [tool.ruff]` for the full ruleset. Notable: isort via ruff, pyupgrade rules enabled, `RUF001-003` (ambiguous unicode) ignored since the codebase contains CJK text.

## When Making Changes

- **Run `uv run ruff check src/`** before committing; only fix new violations you introduce, not pre-existing ones unrelated to your task.
- **Don't introduce per-request resize/load caches** — use the global pool in `src/sekai/base/utils.py`.
- **Use `asyncio.gather`** when loading multiple images; never serialize independent I/O in async contexts.
- **Performance-sensitive paths** should use the existing `*.perf` loggers (e.g. `mysekai.endpoint.perf`, `mysekai.map.perf`) — see `docs/optimizations.md` §4.
- **Refer to `docs/optimizations.md`** for the full history of memory, concurrency, and caching work — it documents the rationale behind current patterns.
