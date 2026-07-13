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

## Skia Backend (`rust/haruki_skia_renderer` + `src/sekai/skia_renderer/`)

Drawing endpoints render through a Rust + Skia extension (PyO3, built with maturin). Python builds a widget
tree, `IRPainter` lowers it to a JSON IR, and Rust rasterizes and encodes it. Pillow remains as the fallback.

**IR-first rule.** The widget tree (`src/sekai/base/plot.py`) is the *only* layout carrier for a drawing
endpoint. Both backends draw the same tree. If a primitive is missing, add it to `Painter` **and** to
`IRPainter` so both backends stay in step — do not special-case a backend inside a drawer with
`isinstance(p, IRPainter)`. A hand-written dedicated scene builder needs a specific performance justification
(Card List is the last remaining one).

**Fail-open.** A missing, stale, or broken extension must degrade to Pillow, never 500. `try_render_*_payload`
returns `None` to mean "Pillow, please". Never let a Skia error escape.

**Switches are env-only** (`HARUKI_` prefix, `__` nesting): `HARUKI_DRAWING__USE_SKIA_PLOT`,
`HARUKI_DRAWING__USE_SKIA_CARD_LIST`. Rollback = flip the env var and restart; the image itself is unchanged.
Renderer tunables: `HARUKI_SKIA_PNG_ENCODER`, `HARUKI_SKIA_RASTER_CACHE_MB`, `HARUKI_SKIA_TEXT_HINTING`,
`HARUKI_SKIA_TEXT_GAMMA`, `HARUKI_SKIA_PROFILE`.

**Capability handshake.** The extension exports `IR_CAPABILITY`; `src/sekai/skia_renderer/canvas.py` checks it
against `REQUIRED_NATIVE_IR_CAPABILITY`. A too-old extension raises `ImportError` and fails open. **When you add
an IR node, bump BOTH sides and the two CI smoke assertions** (`.github/workflows/quick-check.yml`,
`.github/workflows/skia-wheels.yml`).

```bash
# Rebuild the extension after ANY Rust change (otherwise you are testing the old .so)
uv run maturin develop --release --manifest-path rust/haruki_skia_renderer/Cargo.toml

# cargo test needs an explicit libpython link (pyo3 extension-module breaks a bare `cargo test`)
PYLIB=$(uv run python -c "import sysconfig; print(sysconfig.get_config_var('LIBDIR'))")
RUSTFLAGS="-L $PYLIB -C link-arg=-lpython3.14t" cargo test --release \
  --manifest-path rust/haruki_skia_renderer/Cargo.toml

# Parity sweep over 63 real payloads — the regression gate for ANY rendering change
uv run python -X gil=0 scripts/skia_parity_sweep.py            # baseline: {'ok': 63}, 0 failures
```

Wheels are built by `.github/workflows/skia-wheels.yml` (linux-x86_64 + macos-arm64 artifacts, not published to
an index) and installed conditionally by the Docker build. Wheels are Python-version-specific: **upgrading
Python means rebuilding wheels first**, otherwise the image silently falls back to Pillow.

**Traps that have already cost real debugging time:**

- **The parity sweep only compares Pillow ↔ Skia on the *current* tree.** Drift that both backends share — e.g.
  porting a Pillow composer to `Painter` primitives slightly wrong — renders 63/63 green while every image is
  subtly wrong. When you port an existing Pillow composition, diff it pixel-wise against `main`, not just
  across backends.
- **`ImageBg` defaults to `fade=0.1`**, and fade/blur rewrite pixels in the *constructor*. Passing an
  `AssetImageRef` there forces a full decode **on the event loop** and the ref never reaches the IR. Only pass a
  ref to `ImageBg(..., blur=False, fade=0)`; otherwise keep `get_img_from_path`.
- **`Painter.text` anchors the baseline** at `y + ink-height("哇")`; `ImageDraw.text` anchors the ascender top.
  A y-constant lifted from old ImageDraw code lands the text `ascent - ink_height` too high.
- **Pillow's `paste(im, pos, im)` lerps the destination alpha** toward the layer's, so anti-aliased overlay
  edges leave the result translucent. Use `paste_with_alpha_blend` (true `alpha_composite`) for overlays — that
  is also what Skia does for both paste variants.
- **Resizes are cached globally keyed on the resample filter too.** Pastes use BICUBIC (`PASTE_RESAMPLE`, matching
  Pillow's `Image.resize()` default); `get_img_resized` defaults to BILINEAR. Don't "unify" them casually.

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

## Git Commit Format

All commits **must** follow:

```
[Type] Short description starting with capital letter
```

| Type      | Usage                                                 |
|-----------|-------------------------------------------------------|
| `[Feat]`  | New feature or capability                             |
| `[Fix]`   | Bug fix                                               |
| `[Chore]` | Maintenance, refactoring, dependency or build changes |
| `[Docs]`  | Documentation-only changes                            |

Rules:

- Description starts with a **capital letter**.
- Use imperative mood: `Add ...`, not `Added ...`.
- No trailing period.
- Keep the subject at or below roughly 70 characters.
- Agent attribution uses the standard Git `Co-authored-by:` trailer in the commit body, not a free-form
  `Agent:` line. This makes GitHub render the co-author avatar on the commit page. The trailer must be on its
  own line, separated from the subject by a blank line, in the form `Co-authored-by: <Display Name> <email>`.

Suggested co-author trailers:

| Agent | Trailer |
|-------|---------|
| Claude (any 4.x) | `Co-authored-by: Claude Opus 4.7 <noreply@anthropic.com>` (substitute the actual model, e.g. `Claude Sonnet 4.6`) |
| Codex | `Co-authored-by: Codex <noreply@openai.com>` |
| Copilot | `Co-authored-by: Copilot <223556219+Copilot@users.noreply.github.com>` |

Examples from this project:

```
[Feat] Add dedicated thumbnail cache separated from general image cache
[Fix] Route map harvest-point resize through global cache
[Chore] Replace mysekai drawer with public placeholder stub
[Docs] Add resize cache section to optimizations.md
```

Example with agent attribution:

```
[Docs] Update commit attribution rules

Co-authored-by: Codex <noreply@openai.com>
```
