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
# CI checks src, tests AND scripts — lint only src/ and you will still go red.
uv run ruff check src tests scripts
uv run ruff format src tests scripts

# Tests (needs the 3.14t interpreter; native Skia tests skip if the extension is not built)
uv run pytest -q

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
- `src/sekai/` — Domain logic and image drawing. Each subdirectory (card, music, profile, sk, mysekai, ...) contains models and drawer/rendering code. `src/sekai/base/` provides shared Pillow utilities (`painter.py`, `draw.py`, `img_utils.py`, `plot.py`) and the thread pool executor + image cache infrastructure (`utils.py`). `src/sekai/skia_renderer/` is the Python half of the Skia backend — see the Skia chapter below.
- `src/settings.py` — Pydantic-settings singleton loaded from `configs.yaml`. Access via `from src.settings import settings` or convenience module-level exports (e.g. `ASSETS_BASE_DIR`, `DEFAULT_FONT`, `EXPORT_IMAGE_FORMAT`, `JPG_QUALITY`).

**Key design decisions**:
- Requires free-threaded Python (no-GIL). The lifespan handler calls `_ensure_nogil_runtime()` and refuses to start when the GIL is enabled.
- CPU-intensive Pillow rendering is offloaded via `run_in_pool()` to the thread pool in `src/sekai/base/utils.py`. There is **no painter process pool** — it was a GIL-era design and was removed: on 3.14t there is no GIL to dodge, but pickling every decoded image across the boundary still cost ~48% of throughput at concurrency 8 while relocating, not saving, memory.
- A separate **isolated subprocess pool** (`src/core/heavy_render_pool.py`) runs only the two crash-prone heavy tasks (`HeavyTaskKind = "deck_recommend" | "chara_birthday"`), with its own queue limit and hard timeout. Everything else stays in-process. Its `EncodedImagePayload` dataclass is also the common return type of the Skia `try_render_*_payload` functions, which is why most drawers import from this module.
- All multi-step image loading should use `asyncio.gather` to overlap I/O + decoding across threads. See `docs/optimizations.md` §3.
- **Every image response leaves through `encoded_image_payload_to_response` / `image_to_response` (`src/core/utils.py`) as ONE `Response` body.** Do not turn them back into a `StreamingResponse` over a `BytesIO`: the bytes are already whole in memory (nothing to stream), and Starlette drives a *sync* iterable through `iterate_in_threadpool` — while `BytesIO` iterates by **line**, so a binary PNG splits on every `0x0A` byte. That shipped for a long time: ~384-byte chunks, ~2,300 thread-pool round-trips per 870 KB image (~19,000 for a 7.3 MB card/box), no `Content-Length`, and **32x less throughput** (0.78 → 24.8 req/s on deck/recommend at concurrency 8). It hid because it is functionally *correct* — every byte arrives — and because `request.end elapsed=` is stamped when the endpoint returns the Response object, before the body is ever sent, so the server logged 0.12s while the client waited 10s. `tests/test_image_response.py` asserts the ASGI body-message count is 1; that count is the only thing that betrays it.
- Static assets (fonts, images, triangles) live in `data/` and are configured via `configs.yaml`. In Docker, the host data directory is mounted at `/pjskdata/Data`.

## Image Cache Infrastructure (`src/sekai/base/utils.py`)

Three independent in-memory caches. They do **not** share a key shape — only the two file-backed pools use the
6-tuple:

| Cache | Key | Routing | Typical Use |
|---|---|---|---|
| `_image_cache` | `(full_path, mtime_ns, file_size, target_w, target_h, resample)` | general (default) | site backgrounds, card arts, large assets |
| `_thumb_cache` | same 6-tuple | paths containing `"thumbnail"` | small icons, high reuse, high count |
| `_composed_image_cache` | sha256 hex **string** from `build_rendered_image_cache_key()` | explicitly via the composed-cache APIs (`get_composed_image_cached(cache_key: str)`) | decoded `PIL.Image` of an already-composed page, TTL-based |

In the 6-tuple, the `resample` element matters: the same file at the same target size resized with a different filter
is a **different** entry (`(0, 0)` target means "no resize", and then `resample` is `0` too). Don't drop it from a key.

`_composed_image_cache` is a `_TTLImageCache` and stores **decoded images**, not encoded PNG/JPG bytes — the encoded
composed-image disk cache below is simply the L2 tier of that same cache — same sha256 key, PIL image in,
PNG on disk, PIL image out — and it holds sub-page FRAGMENTS (event/vlive list entries, profile modules),
not end-result responses. The only encoded response bytes cached anywhere are **honor's**, in
`skia_payload_cache` — card/box and card/list had theirs removed (they bake the wall clock into the page; see the
Skia chapter), and every other endpoint re-renders.

**A rendered-image cache key carries two things the request does not.**

1. **A fingerprint of the drawing code**, folded in for you by `build_rendered_image_cache_key()`:
   `renderer_code_fingerprint()` is a sha256 of every `.py` under `src/` — **content, not mtime**, or every image
   rebuild would throw the whole disk cache away. It exists because the composed-image L2 lives under `data/`, which
   is a **mounted volume** in Docker and outlives a deploy: without it, a new build cheerfully served the *old* pixels
   off the volume for up to the 7-day TTL. Reproduced across two processes with the volume kept between them.
2. **Asset signatures, which are yours to pass.** Either `collect_asset_signatures(ASSETS_BASE_DIR, material)`, which
   stats every path-shaped string in the key material (what `event/drawer.py` does), or a hand-listed dict of
   `get_image_asset_signature()` calls (what `honor/drawer.py` does — fourteen paths by name, correct today and
   silently wrong the day someone adds a fifteenth; **prefer the collector**). Without them, an asset **replaced** at a
   path the request already names — or one that finally **arrives** after a `?` placeholder was cached in its place —
   does not move the key, and the stale picture is served until the entry expires. `vlive/drawer.py` still keys on the
   request alone; it gets away with it only because its key carries a minute bucket, so it self-heals within 60s.

`tests/test_asset_signature_cache_key.py` pins both. A new page/fragment cache must satisfy it.

Resize results are **also** cached in the same general/thumb pool — see `get_img_resized()` and `get_img_resized_long_edge()`. Always prefer these over per-request `dict` caches; the global pool persists across requests.

Sizes are configured in `configs.yaml` under `drawing.*` and exported as `IMAGE_CACHE_SIZE`, `THUMB_CACHE_SIZE`, `COMPOSED_IMAGE_CACHE_SIZE`, with their `*_MAX_BYTES` companions. Setting a size to `0` disables that pool.

**The Skia payload cache has no knob of its own.** `payload_cache.py` builds it from `COMPOSED_IMAGE_CACHE_SIZE` /
`COMPOSED_IMAGE_CACHE_MAX_BYTES` / `COMPOSED_IMAGE_CACHE_TTL_SECONDS` — the same three keys that size
`_composed_image_cache`. So those three `composed_image_cache_*` keys size **two** independent pools, and zeroing any
one of them (it needs size, bytes *and* TTL all `> 0`) silently disables honor's encoded-response cache too.

Beyond these three there are two disk tiers, both swept periodically by the lifespan handler (`_cleanup_disk_caches`
in `src/core/main.py`): the composed-image disk cache (`data/utils/composed_image_disk_cache`, TTL-based) and
`Painter`'s own disk cache (`PAINTER_CACHE_DIR`, swept via `Painter.cleanup_old_disk_cache()`).

Sweeping is where the symmetry ends — **the two tiers are not both observable.** `GET /cache/stats` returns exactly
what `get_runtime_cache_stats()` builds, which is five keys: `image_cache`, `thumbnail_cache`,
`composed_image_cache`, `composed_image_disk_cache`, and `skia_payload_cache` (a *fourth* in-memory pool, owned by
the Skia chapter below — the three caches in the table above are not the whole dump). The `Painter` disk cache has no
`stats()` and appears nowhere in `src/core/health.py`; to size it you have to look at the directory.

## Configuration

`configs.yaml` at the project root (or `configs.docker.yaml` in Docker). Environment variables with the `HARUKI_` prefix and `__` nesting also work (e.g. `HARUKI_DRAWING__THREAD_POOL_SIZE=16`) and **take precedence over the YAML file** — `Settings.settings_customise_sources` deliberately orders `env_settings` ahead of the yaml-derived init kwargs, so an operator can override a key at runtime even when it is written in `configs.yaml`.

Notable `drawing.*` keys:
- `thread_pool_size` — default thread pool size (CPU-bound rendering).
- `isolated_worker_pool_size` / `isolated_worker_queue_limit` / `isolated_worker_queue_timeout_seconds` / `request_hard_timeout_seconds` — the heavy-task subprocess pool (`src/core/heavy_render_pool.py`). **Size it to the CPU allocation, not higher.** The workers are spawned at boot and never recycled except on crash, and each builds its *own* asset/font/raster caches: measured in the image, one grows from 47 MB idle to ~500 MB after serving, and the pool plateaus at ~270 MB × N. `deck_recommend` is a CPU-bound search (~12 s), so oversubscribing the CPUs buys nothing: 8 workers vs 2 was 6% p50 latency for 1.5 GB of RSS.
- `overload_max_inflight_requests` / `overload_retry_after_seconds` — optional overload guard; reject new requests with `503` once in-flight requests exceed the threshold.
- `readiness_unhealthy_inflight_requests` / `readiness_unhealthy_cgroup_percent` / `readiness_unhealthy_asyncio_tasks` / `readiness_unhealthy_rss_mb` — readiness thresholds used by `/ready`; once exceeded, the service reports `503` so orchestration can stop routing more traffic. **The memory gate is `readiness_unhealthy_cgroup_percent`** (default 90): `read_cgroup_memory()` reads `memory.current` against `memory.max` (cgroup v2, falling back to v1's `usage_in_bytes`/`limit_in_bytes`), so it sees the whole container — including the heavy-render workers, which are separate processes and hold most of the memory (~500 MB each warm, versus a parent that idles at 267 MB while the cgroup is at 585 MB). It is a *percentage* precisely so it cannot be set above the hard limit and become unfirable. Outside a memory-limited cgroup (bare metal, macOS, unconstrained container) it reads `None` and the gate simply does not apply. **`readiness_unhealthy_rss_mb` is `0` (off) by design**: it reads `/proc/self/status` VmRSS — the *parent only* — which grows with concurrency (483/757/838/958 MB at 1/4/8/12 concurrent card/box), so it behaves like a miscalibrated concurrency gate that fires before the explicit `readiness_unhealthy_inflight_requests` one, while still being blind to the memory that actually fills the cgroup.
- `image_cache_size` / `image_cache_max_mb` — general image LRU.
- `thumbnail_cache_size` / `thumbnail_cache_max_mb` — dedicated thumbnail LRU (recommend 4096 / 256MB).
- `composed_image_cache_size` / `composed_image_cache_max_mb` / `composed_image_cache_ttl_seconds` — the
  decoded-**fragment** cache (event/vlive list entries, profile modules, honor's composed badge), *not* a
  final-output cache. These same three keys also size the **Skia payload cache** (honor's encoded responses),
  so zeroing any one of them disables **both** pools — see the cache chapter.
- `export_image_format` — `"png"` or `"jpg"`.
- `jpg_quality` — JPEG quality (1–100), only applied when format is `"jpg"`.
- `use_skia_plot` — the Skia gate (default `true`). By convention it is **not** written into `configs.yaml`; flip it with `HARUKI_DRAWING__USE_SKIA_PLOT`. See the Skia chapter below.

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
`isinstance(p, IRPainter)`. A hand-written scene builder (`IRBuilder` used directly, bypassing the tree *and*
`IRPainter`) needs a specific justification; **two endpoints still do it** and they are the exceptions, not the
rule:

- `src/sekai/chart/drawer.py` — the chart pixels come from `pjsekai_scores_rs`; the IR only adds the watermark
  footer around them.
- `src/sekai/honor/skia.py` — the badge's **watermark footer** only (a `SelfImage` strip of the badge's own
  bottom rows plus two shadowed text lines — the widget tree cannot express that). The badge itself is a shared
  widget subtree: `honor/widget.py::HonorBadgeBox`, built by `build_honor_badge_canvas()`, which **both** backends
  consume — Pillow renders it with `get_img_sync()`, Skia splices its IR under the footer with
  `IRBuilder.splice_root_children`. **The layout exists once.** (An earlier version of this file claimed honor
  duplicated its layout in Pillow and IR; that stopped being true when honor moved onto the shared tree, and the
  stale warning cost real time.)

Card List is **not** one of them any more — it and Card Box have no dedicated scene builder and draw the shared
`plot.py` widget tree like everything else.

**Fail-open.** A missing, stale, or broken extension must degrade to Pillow, never 500. `try_render_*_payload`
returns `None` to mean "Pillow, please". Never let a Skia error escape.

**One switch, env-only** (`HARUKI_` prefix, `__` nesting): `HARUKI_DRAWING__USE_SKIA_PLOT` (default on). It is the
only Skia gate — the older per-endpoint gates (`use_skia_card_list`, `use_skia_card_box`) are gone. Rollback =
flip the env var and restart; the image itself is unchanged. Renderer tunables: `HARUKI_SKIA_PNG_ENCODER`,
`HARUKI_SKIA_RASTER_CACHE_MB`, `HARUKI_SKIA_RASTER_CACHE_MAX_ENTRY_MB`, `HARUKI_SKIA_RASTER_CACHE_OVERSAMPLE`,
`HARUKI_SKIA_TEXT_HINTING`, `HARUKI_SKIA_TEXT_GAMMA`, `HARUKI_SKIA_PROFILE`.

**Capability handshake.** The extension exports `IR_CAPABILITY` (currently **7**) and `RAW_BUFFER_CAPABILITY`;
`src/sekai/skia_renderer/canvas.py` checks the former against `REQUIRED_NATIVE_IR_CAPABILITY` (also 7). A too-old
extension raises `ImportError` and fails open. **When you add an IR node, bump BOTH sides and the two CI smoke
assertions** (`.github/workflows/quick-check.yml`, `.github/workflows/skia-wheels.yml`). The Docker build's
self-check needs **no** edit: it greps `REQUIRED_NATIVE_IR_CAPABILITY` out of `canvas.py` and compares the installed
wheel against that (it used to hardcode its own number, which drifted below the required one, so a stale wheel passed
the image self-check and then silently fell back to Pillow at runtime). Four hardcoded copies of the
number already exist (Rust, canvas.py, and the two CI assertions) — do not add a fifth.

**Observability.** `GET /render-stats` reports, per endpoint, how many requests were served
`skia` / `cache_hit` / `fallback` / `disabled` / `error` (`src/sekai/skia_renderer/render_stats.py`), plus the
Skia payload cache (`payload_cache.py`, **used by honor only**). The `image.response` log line carries a
`backend=` field.

**No page-level cache on a page that renders the clock.** card/box and card/list used to have one and it was
removed: `add_request_watermark` stamps a `DT: <timestamp>` footer, and card/list's 未上线 badge is decided by
`request_now()` against each card's `release_at` — neither was in the key. Two requests differing only in `dt`
therefore shared a key and the second was handed the first one's footer; a card that had gone live an hour
earlier still rendered 未上线 out of the cache, for up to the 7-day TTL. A key honest about the clock cannot hit
(`dt` is millisecond wall-clock), so there was nothing to fix in the key — the cache itself was the bug. honor
keeps its payload cache because its key *does* carry the watermark text and every asset signature, and its page
has no other clock term. The rest of the endpoints deliberately have no page-level cache because the caller
already dedupes by payload.

```bash
# Rebuild the extension after ANY Rust change (otherwise you are testing the old .so)
uv run maturin develop --release --manifest-path rust/haruki_skia_renderer/Cargo.toml

# cargo test needs an explicit libpython link (pyo3 extension-module breaks a bare `cargo test`)
PYLIB=$(uv run python -c "import sysconfig; print(sysconfig.get_config_var('LIBDIR'))")
RUSTFLAGS="-L $PYLIB -C link-arg=-lpython3.14t" cargo test --release \
  --manifest-path rust/haruki_skia_renderer/Cargo.toml

# Parity sweep over 63 real payloads — the regression gate for ANY rendering change
uv run python -X gil=0 scripts/skia_parity_sweep.py            # baseline: {'ok': 63}, 0 failures

# Pillow-vs-Pillow against a baseline ref — catches the drift BOTH backends share (see traps)
uv run python -X gil=0 scripts/skia_legacy_baseline.py --ref main [--only profile,card_list]

# Warm-cache parity — the ONLY gate that renders with the caches ON. Run it on any cache change.
uv run python -X gil=0 scripts/skia_warm_parity.py            # baseline: 53 ok / 10 nondeterministic, 0 drift

# Pillow vs Skia timings. NOT the parity sweep — see below.
uv run python -X gil=0 scripts/skia_bench.py [--cold]         # warm: 3.65x overall; honor is the one loser
```

**Never time anything with the parity sweep.** It used to print `elapsed_pillow` / `elapsed_skia` and
every number in the migration doc came from them; they were wrong twice, in opposite directions. It renders
Pillow *first* and does not bypass the decode caches, so Pillow paid for the cold decodes and Skia inherited
a warm cache (`mysekai_music_record` read as 10.39x; it is 1.12x). And `compose_*_image()` returns a
`PIL.Image` while `try_render_*_payload()` returns *encoded bytes*, so only Skia was charged for the PNG
encode — which invented six "Skia is slower" endpoints that do not exist (Pillow spends **110 ms** encoding
the 1536×880 mysekai map that Skia encodes in 14 ms; Pillow's PNG encoder is 19% of the whole Pillow path).
The timings are gone from the sweep — it is a correctness gate — and `scripts/skia_bench.py` does the job
properly: both sides produce response bytes, both start warm, min-of-N, and a `--cold` mode for first-request
latency.

**The parity sweep renders with the caches OFF.** `skia_parity_sweep.py` calls `bypass_caches()` on
purpose (honest timings), so its 63/63 only ever proves that *re-rendering from scratch* is correct — it
says nothing about the path production actually takes, which is a **cache hit**. A key that omits
something the output depends on serves a different-but-perfectly-valid image and nothing errors.
`skia_warm_parity.py` is the gate for that: cold reference per case, then two warm passes over all 63
(forward, then **backward** — the backward pass makes each page render against a cache filled by 62
*other* pages, which is what surfaces a key collision, a mutation-on-hit, or bleed through the single
shared Rust raster pool). It excludes the ~10 endpoints that draw a live countdown, and it cannot see a
collision between two *different* payloads (there is one payload per endpoint) — for those you have to
read the key material.

Wheels are built by `.github/workflows/skia-wheels.yml` (linux-x86_64 + macos-arm64 artifacts, not published to
an index) and installed conditionally by the Docker build. Wheels are Python-version-specific: **upgrading
Python means rebuilding wheels first**, otherwise the image silently falls back to Pillow.

**Traps that have already cost real debugging time:**

- **The parity sweep only compares Pillow ↔ Skia on the *current* tree.** Drift that both backends share — e.g.
  porting a Pillow composer to `Painter` primitives slightly wrong — renders 63/63 green while every image is
  subtly wrong. That is exactly what `scripts/skia_legacy_baseline.py` exists for: it re-renders the same
  payloads with Pillow on a baseline ref (default `main`) in a throwaway worktree and diffs. Run it whenever you
  port an existing Pillow composition into the tree.
- **`ImageBg` defaults to `fade=0.1`**, and fade/blur rewrite pixels in the *constructor*. Passing an
  `AssetImageRef` there forces a full decode **on the event loop** and the ref never reaches the IR. Only pass a
  ref to `ImageBg(..., blur=False, fade=0)`; otherwise keep `get_img_from_path`.
- **`Painter.text` anchors the baseline** at `y + ink-height("哇")`; `ImageDraw.text` anchors the ascender top.
  A y-constant lifted from old ImageDraw code lands the text `ascent - ink_height` too high.
- **There are THREE paste primitives, not two.** Pillow's `paste(im, pos, im)` lerps the destination alpha toward
  the layer's, so anti-aliased overlay edges leave the result translucent — use `paste_with_alpha_blend` (true
  `alpha_composite`) for overlays; Skia draws both of those src-over. The third is `Painter.paste_src` (Porter-Duff
  **Src**, IR `blend="src"`, `BlendMode::Src` in Rust, no AA), for the **base layer** of an absolute-coordinate
  composite — the asset that *is* the canvas. There, `paste` squares the alpha of an anti-aliased edge and
  `paste_with_alpha_blend` zeroes the rgb under fully transparent pixels; an honor badge's frame over the transparent
  corners of its base art shifts by up to 228/255 without it.
- **The triangle background is generated in Python, not in either renderer.**
  `src/sekai/base/triangle_bg.py` scatters the triangles from a seed quantized to the whole hour;
  `Painter` draws that list and `IRPainter` ships it on `TriangleBg.tris`. Do **not** re-add a PRNG to
  either backend: they used to roll their own (Pillow from the *unseeded global* `random`, Rust from a
  millisecond-precision seed), so the backgrounds could never match, neither backend reproduced itself,
  and the parity sweep needed a loose threshold that hid real drift. The palette still follows the
  *fractional* hour by design, so a harness that wants byte-stable output must pin
  `HARUKI_BG_TEST_HOUR` (both `skia_parity_sweep.py` and `skia_legacy_baseline.py` do).
- **Resizes are cached globally keyed on the resample filter too.** Pastes use BICUBIC (`PASTE_RESAMPLE`, matching
  Pillow's `Image.resize()` default); `get_img_resized` defaults to BILINEAR. Don't "unify" them casually.

## CI

GitHub Actions workflow `.github/workflows/free-threaded-smoke.yml` runs on every push/PR: installs 3.14t, verifies no-GIL imports, compiles all source, runs concurrency smoke tests, and compares GIL vs no-GIL benchmark throughput.

`quick-check.yml` also runs on every push/PR and has two jobs:
- `lint-test` — `ruff check` + `ruff format --check` + `compileall` over **`src tests scripts`**, a config/repo guard (both YAML configs must validate; `drawer.real.py` must stay untracked), `docker compose config`, then `pytest -q` (Skia-native tests skip, no extension).
- `native-tests` — builds `haruki_skia_renderer` with maturin, asserts the `IR_CAPABILITY` handshake, and re-runs pytest so the native tests actually execute.

`skia-wheels.yml` builds the release wheels; `docker.yml` builds the image with the wheel baked in (and stays green without one).

## Code Style

Ruff with `line-length = 120`. See `pyproject.toml [tool.ruff]` for the full ruleset. Notable: isort via ruff, pyupgrade rules enabled, `RUF001-003` (ambiguous unicode) ignored since the codebase contains CJK text.

## When Making Changes

- **Run `uv run ruff check src tests scripts` and `uv run ruff format src tests scripts`** before committing — CI checks all three trees, not just `src/`. Only fix new violations you introduce, not pre-existing ones unrelated to your task.
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
