# Pillow retirement roadmap

## Goal

Retire Pillow from the production native-render path first, then remove the
legacy renderer and the dependency itself. A response being labelled `skia`
is not sufficient: Python may still use Pillow for layout metrics, image
probing/decoding, placeholders, or intermediate rasters before Rust encodes
the final image.

The migration therefore uses this order:

1. make coverage and pixel-parity failures impossible to hide;
2. measure every Pillow touch on successful native requests;
3. remove shared Pillow services in descending production impact;
4. make native-only execution a release requirement;
5. delete the fallback and dependency only after a soak period.

There are currently 28 tracked Python modules under `src/` with a direct
Pillow import. This is an inventory signal, not the completion metric: one
shared text-measurement call can keep every endpoint hybrid, while a legacy
drawer import may never execute in production.

## Milestone 0: trustworthy migration controls

This milestone is implemented in the current change set.

### Route coverage

`tests/test_route_render_contract.py` walks every leaf route, including routes
whose local path is the empty string, and cross-checks the result against the
registered OpenAPI POST paths. A newly registered drawing endpoint can no
longer disappear from the Skia coverage test because of the route walker.

### Strict parity

`scripts/skia_parity_budgets.py` gives all 67 parity cases an explicit
`(mean, p99)` pixel-difference ceiling. The normal sweep remains convenient
for development; the Pillow-removal gate is:

```bash
uv run python -X gil=0 scripts/skia_parity_sweep.py --strict
```

Strict mode accepts only `ok` and also rejects:

- missing, duplicate, or unexpected result rows;
- missing or unused budgets;
- fixtures without a case mapping;
- `no-payload`, `skipped`, `pillow-only`, and known-blocked cases;
- partial `--only` runs.

The private corpus contains 65 payloads and two custom-profile cases without
captured payloads. The budgets were seeded from the last accepted 65-case
results. Strict mode intentionally stays red until the missing fixtures exist,
all cases render natively, and any newly exposed pixel drift is resolved. This
is a prerequisite for deleting the Pillow fallback, not for shipping a stable
version that still retains fail-open behavior.

### Backend-neutral response payload

`EncodedImagePayload` now lives in `src/core/image_payload.py`. Importing the
payload contract or heavy-worker module no longer imports Pillow. The old
heavy-worker import remains as a compatibility re-export while callers move
to the neutral module.

### Native dependency telemetry

Every request gets a thread-safe Pillow-touch scope. The scope is propagated
through `run_in_pool`; heavy workers carry their consumed snapshot back in
`EncodedImagePayload`.

`GET /render-stats` reports these fields per endpoint and in totals:

- `native_pure`: successful `skia`/`cache_hit`, scoped, no Pillow touch;
- `native_hybrid`: successful native result with one or more Pillow touches;
- `native_unclassified`: successful native result without trustworthy scope
  data;
- `pillow_touch_reasons`: affected render count and raw touch count by reason.

Fallback, disabled, and error outcomes are not included in the native-purity
denominator. Current reason buckets cover Pillow text metrics, image header
probes, image decoding, missing placeholders, `IRPainter` PIL-image/memory
rasters, and custom-profile memory rasters.

## Next implementation order

### 1. Remove Pillow text measurement

This is the highest-leverage shared dependency. Introduce a renderer-neutral
font descriptor and text-metrics interface, then make the native
implementation use the same Skia font resolution and shaping rules as the
Rust renderer. Migrate widget sizing and watermark layout to it before
changing endpoint drawers.

Completion gate:

- `pillow_text_metric` is zero across the full parity corpus and production
  soak;
- text baselines, emoji, wrapping, and fallback-font fixtures stay within
  their explicit parity budgets;
- Pillow-versus-baseline remains green while the legacy renderer exists.

### 2. Remove Pillow image metadata and decode services

Move asset dimension/mode probing and encoded-image metadata behind a neutral
image-info API. Let the native extension decode encoded images and files
directly; keep file signatures and cache-key semantics unchanged.

Completion gate:

- `pillow_image_header_probe`, `pillow_image_decode`, and
  `pillow_placeholder` are zero on native requests;
- cold and warm parity both pass;
- missing/replaced asset tests still invalidate the correct cache entries.

### 3. Eliminate intermediate PIL rasters

Use `pillow_touch_reasons` to rank real traffic instead of migrating by file
count. The known islands are:

- custom-profile layer rasters;
- `IRPainter` inputs that still arrive as `PIL.Image`;
- endpoint-specific eager crop/resize/tint pipelines;
- honor and other legacy fragments that cross the memory-raster boundary.

Port one semantic pipeline at a time. Add IR/native primitives where the
operation is reusable; do not fork drawer layout by backend.

Completion gate:

- `irpainter_pil_image`, `irpainter_pil_mem_raster`, and
  `custom_profile_pil_mem_raster` are zero for every native endpoint;
- the 67-case strict sweep and both directions of warm parity pass;
- the native cache does not retain unbounded decoded/intermediate rasters.

### 4. Add a native-required release lane

Keep fail-open behavior in normal production until the native path is proven,
but add a test/release mode in which any fallback, Pillow touch, or
unclassified native result fails the run. Exercise it in a clean interpreter
over the complete payload corpus and assert that importing the serving path
does not load `PIL`.

Required release signals:

- all 67 strict parity cases are `ok`;
- `fallback == error == disabled == 0`;
- `native_hybrid == native_unclassified == 0`;
- `native_pure == skia + cache_hit` for every endpoint;
- warm parity and legacy-baseline parity pass;
- wheels for every deployment platform satisfy the capability handshake.

### 5. Remove the legacy renderer and dependency

After at least one representative production soak with the native-required
signals above:

1. remove endpoint Pillow compose fallbacks;
2. remove Pillow-only caches and their configuration;
3. remove `Painter` and Pillow-specific image utilities once no caller remains;
4. remove `pilmoji`, then Pillow, from project dependencies;
5. build and test the deployment image without Pillow installed.

The environment rollback switch should remain until the soak is complete.
Deleting Pillow earlier would turn a missing/stale native wheel from a
controlled fallback into an outage.

## Gates to run for every retirement change

```bash
uv run ruff check src tests scripts
uv run ruff format --check src tests scripts
uv run pytest -q
uv run python -X gil=0 scripts/skia_parity_sweep.py --strict
uv run python -X gil=0 scripts/skia_warm_parity.py --backend both
uv run python -X gil=0 scripts/skia_legacy_baseline.py --ref main
```

Use `scripts/skia_bench.py` for performance. The parity sweeps are correctness
gates and must not be used as timing evidence.
