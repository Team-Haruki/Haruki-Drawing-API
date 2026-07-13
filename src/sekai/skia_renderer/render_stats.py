"""Process-wide counters for how each render request was actually served.

The Skia path is otherwise invisible: a request can be rendered natively, served from the
Skia payload cache, or silently fall back to Pillow, and nothing recorded which happened.
Every Skia entry point records exactly one outcome per render attempt here; ``/render-stats``
exposes the aggregate.

Thread-safe by an explicit lock — renders run in a thread pool on a free-threaded build, so
we must not rely on the GIL. Counters are per-process: the heavy-worker endpoints
(deck / chara-birthday) render in a spawned child process, so the child's counters are not
visible here. Those payloads carry their backend back to the parent instead
(see :class:`src.core.heavy_render_pool.EncodedImagePayload`) and the parent records them via
:func:`record_worker_payload_backend`.
"""

from __future__ import annotations

import logging
import threading

from src.settings import settings

logger = logging.getLogger("plot.draw.perf")

# Render outcomes (one recorded per render attempt).
OUTCOME_SKIA = "skia"  # rendered natively this request
OUTCOME_CACHE_HIT = "cache_hit"  # served from the Skia payload cache
OUTCOME_FALLBACK = "fallback"  # Skia declined (unsupported primitive / native ext missing)
OUTCOME_DISABLED = "disabled"  # use_skia_plot is off
OUTCOME_ERROR = "error"  # Skia raised unexpectedly -> caller uses Pillow

OUTCOMES: tuple[str, ...] = (
    OUTCOME_SKIA,
    OUTCOME_CACHE_HIT,
    OUTCOME_FALLBACK,
    OUTCOME_DISABLED,
    OUTCOME_ERROR,
)

# Backend labels for the ``image.response`` log line.
BACKEND_SKIA = "skia"
BACKEND_SKIA_CACHE = "skia_cache"
BACKEND_SKIA_FALLBACK = "skia_fallback"
BACKEND_PILLOW = "pillow"

BACKENDS: tuple[str, ...] = (BACKEND_SKIA, BACKEND_SKIA_CACHE, BACKEND_SKIA_FALLBACK, BACKEND_PILLOW)

_BACKEND_BY_OUTCOME: dict[str, str] = {
    OUTCOME_SKIA: BACKEND_SKIA,
    OUTCOME_CACHE_HIT: BACKEND_SKIA_CACHE,
    OUTCOME_FALLBACK: BACKEND_SKIA_FALLBACK,
    OUTCOME_ERROR: BACKEND_SKIA_FALLBACK,
    OUTCOME_DISABLED: BACKEND_PILLOW,
}

_OUTCOME_BY_BACKEND: dict[str, str] = {
    BACKEND_SKIA: OUTCOME_SKIA,
    BACKEND_SKIA_CACHE: OUTCOME_CACHE_HIT,
    BACKEND_SKIA_FALLBACK: OUTCOME_FALLBACK,
    BACKEND_PILLOW: OUTCOME_DISABLED,
}

_lock = threading.Lock()
_counters: dict[str, dict[str, int]] = {}
_font_fallbacks: int = 0


def backend_for_outcome(outcome: str) -> str:
    """Map a render outcome to the ``backend=`` label used on the image.response log line."""
    return _BACKEND_BY_OUTCOME.get(outcome, BACKEND_PILLOW)


def record_native_metrics(metrics: dict | None) -> None:
    """Fold a render's native metrics into the process-wide counters.

    The Rust side counts font fallbacks in a process-local static, which the parent cannot read
    for the two endpoints that render inside a spawned heavy worker (deck, chara-birthday). The
    per-render count rides back on the payload instead, so aggregate it here — otherwise
    /render-stats would report a healthy 0 while every deck image silently renders in
    sans-serif. Never raises.
    """
    global _font_fallbacks
    if not metrics:
        return
    try:
        fallbacks = int(metrics.get("font_fallbacks") or 0)
    except (TypeError, ValueError):
        return
    if fallbacks <= 0:
        return
    with _lock:
        _font_fallbacks += fallbacks


def record_render(endpoint: str, outcome: str) -> None:
    """Record one render attempt. Never raises — observability must not break a request."""
    name = (endpoint or "").strip() or "unknown"
    if outcome not in _BACKEND_BY_OUTCOME:
        logger.warning("render_stats got unknown outcome %r for endpoint %s; recording as error", outcome, name)
        outcome = OUTCOME_ERROR
    with _lock:
        bucket = _counters.get(name)
        if bucket is None:
            bucket = dict.fromkeys(OUTCOMES, 0)
            _counters[name] = bucket
        bucket[outcome] += 1


def record_skia_cache_hit(endpoint: str, payload) -> None:
    """Record a payload-cache hit for an endpoint that short-circuits before rendering.

    ``render_canvas_payload`` records its own outcomes, but card/list and card/box return a cached
    payload before ever reaching it, so without this they would silently under-count themselves.
    (honor also has a payload cache, but it hand-builds its IR and records through its own
    ``_record`` helper instead of this one.)
    """
    from src.core.debug import set_render_backend

    record_render(endpoint, OUTCOME_CACHE_HIT)
    backend = backend_for_outcome(OUTCOME_CACHE_HIT)
    set_render_backend(backend)
    if payload is not None:
        payload.backend = backend


def record_worker_payload_backend(endpoint: str, backend: str | None) -> str:
    """Parent-side record for a payload rendered inside a heavy-worker process.

    The worker's own counters live in that child process, so the parent replays the outcome
    from the backend carried on the payload. ``backend is None`` means the worker produced the
    image with Pillow (no Skia payload), which is either "Skia is off" or "Skia declined".
    Returns the resolved backend label.
    """
    if not backend:
        backend = BACKEND_SKIA_FALLBACK if settings.drawing.use_skia_plot else BACKEND_PILLOW
    record_render(endpoint, _OUTCOME_BY_BACKEND.get(backend, OUTCOME_FALLBACK))
    return backend


def get_render_stats() -> dict:
    """Per-endpoint counters plus totals. JSON-serializable."""
    totals = dict.fromkeys(OUTCOMES, 0)
    endpoints: dict[str, dict[str, int]] = {}
    with _lock:
        for name, bucket in _counters.items():
            entry = dict(bucket)
            entry["total"] = sum(bucket.values())
            endpoints[name] = entry
            for outcome in OUTCOMES:
                totals[outcome] += bucket[outcome]
    totals["total"] = sum(totals[outcome] for outcome in OUTCOMES)
    with _lock:
        font_fallbacks = _font_fallbacks
    return {
        "endpoints": dict(sorted(endpoints.items())),
        "totals": totals,
        # Text that rendered in sans-serif because the configured face could not be resolved.
        # Anything above 0 means a broken font config — the images are wrong, not just slow.
        "font_fallbacks": font_fallbacks,
    }


def reset_render_stats() -> None:
    global _font_fallbacks
    with _lock:
        _counters.clear()
        _font_fallbacks = 0
