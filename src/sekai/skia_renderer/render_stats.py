"""Process-wide counters for how each render request was actually served.

The Skia path is otherwise invisible: a request can be rendered natively, served from the
Skia payload cache, or silently fall back to Pillow, and nothing recorded which happened.
Every Skia entry point records exactly one outcome per render attempt here; ``/render-stats``
exposes the aggregate.

Thread-safe by an explicit lock — renders run in a thread pool on a free-threaded build, so
we must not rely on the GIL. Counters are per-process: the heavy-worker endpoints
(deck / chara-birthday) render in a spawned child process, so the child's counters are not
visible here. Those payloads carry their backend back to the parent instead
(see :class:`src.core.image_payload.EncodedImagePayload`) and the parent records them via
:func:`record_worker_payload_backend`.
"""

from __future__ import annotations

import logging
import threading

from src.core.pillow_telemetry import PillowTouchSnapshot, take_pillow_touch_snapshot
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

NATIVE_PURITY_KEYS: tuple[str, ...] = (
    "native_pure",
    "native_hybrid",
    "native_unclassified",
)
_NATIVE_OUTCOMES = frozenset((OUTCOME_SKIA, OUTCOME_CACHE_HIT))

ERROR_STAGES: tuple[str, ...] = (
    "renderer_init",
    "scene_build",
    "native_render",
    "payload_decode",
    "pool_dispatch",
    "unknown",
)
_ERROR_STAGE_SET = frozenset(ERROR_STAGES)

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
_native_purity_counters: dict[str, dict[str, int]] = {}
_pillow_touch_render_counts: dict[str, dict[str, int]] = {}
_pillow_touch_call_counts: dict[str, dict[str, int]] = {}
_scene_completeness: dict[str, dict] = {}
_error_stage_counters: dict[str, dict[str, int]] = {}
_font_fallbacks: int = 0

_SCENE_SUM_KEYS: tuple[str, ...] = (
    "elements_total",
    "visible_elements",
    "native_elements",
    "hybrid_elements",
    "noop_elements",
    "hidden_elements",
    "missing_elements",
    "unresolved_elements",
    "mem_images",
    "mem_bytes",
)
_SCENE_CLASSIFICATIONS: tuple[str, ...] = (
    "hidden",
    "native",
    "hybrid",
    "noop",
    "missing",
    "unresolved",
)


def _counter_bucket(counters: dict[str, dict[str, int]], name: str, keys: tuple[str, ...]) -> dict[str, int]:
    bucket = counters.get(name)
    if bucket is None:
        bucket = dict.fromkeys(keys, 0)
        counters[name] = bucket
    return bucket


def _record_native_purity(name: str, outcome: str, snapshot: PillowTouchSnapshot) -> None:
    if outcome not in _NATIVE_OUTCOMES:
        return
    bucket = _counter_bucket(_native_purity_counters, name, NATIVE_PURITY_KEYS)
    bucket[f"native_{snapshot.native_purity}"] += 1


def _record_pillow_touches(name: str, outcome: str, snapshot: PillowTouchSnapshot) -> None:
    if outcome not in _NATIVE_OUTCOMES or not snapshot.scoped or not snapshot.counts:
        return
    render_counts = _pillow_touch_render_counts.setdefault(name, {})
    call_counts = _pillow_touch_call_counts.setdefault(name, {})
    for reason, count in snapshot.counts.items():
        render_counts[reason] = render_counts.get(reason, 0) + 1
        call_counts[reason] = call_counts.get(reason, 0) + count


def _record_error_stage(name: str, outcome: str, error_stage: str | None) -> None:
    if outcome != OUTCOME_ERROR or not error_stage:
        return
    stage = str(error_stage).strip() or "unknown"
    if stage not in _ERROR_STAGE_SET:
        stage = "unknown"
    stage_counts = _error_stage_counters.setdefault(name, {})
    stage_counts[stage] = stage_counts.get(stage, 0) + 1


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


def _nonnegative_metric(metrics: dict, key: str) -> int:
    try:
        return max(0, int(metrics.get(key, 0) or 0))
    except (TypeError, ValueError):
        return 0


def _scene_counts_by_kind(raw_counts_by_kind: object, statuses: tuple[str, ...]) -> dict[str, dict[str, int]]:
    if not isinstance(raw_counts_by_kind, dict):
        return {}
    counts_by_kind: dict[str, dict[str, int]] = {}
    for raw_kind, raw_counts in raw_counts_by_kind.items():
        kind = str(raw_kind or "").strip()
        if not kind or not isinstance(raw_counts, dict):
            continue
        counts = {status: count for status in statuses if (count := _nonnegative_metric(raw_counts, status))}
        if counts:
            counts_by_kind[kind] = counts
    return counts_by_kind


def _new_scene_bucket() -> dict:
    return {
        "checked": 0,
        "complete": 0,
        "incomplete": 0,
        **dict.fromkeys(_SCENE_SUM_KEYS, 0),
        "issues_by_kind": {},
        "classifications_by_kind": {},
    }


def _merge_counts_by_kind(
    aggregate: dict[str, dict[str, int]],
    update: dict[str, dict[str, int]],
    default_statuses: tuple[str, ...] = (),
) -> None:
    for kind, counts in update.items():
        kind_bucket = aggregate.setdefault(kind, dict.fromkeys(default_statuses, 0))
        for status, count in counts.items():
            kind_bucket[status] = kind_bucket.get(status, 0) + count


def _merge_scene_metrics(
    bucket: dict,
    *,
    complete: bool,
    values: dict[str, int],
    issues: dict[str, dict[str, int]],
    classifications: dict[str, dict[str, int]],
) -> None:
    bucket["checked"] += 1
    bucket["complete" if complete else "incomplete"] += 1
    for key, value in values.items():
        bucket[key] += value
    _merge_counts_by_kind(bucket["issues_by_kind"], issues, ("missing", "unresolved"))
    _merge_counts_by_kind(bucket["classifications_by_kind"], classifications)


def record_scene_completeness(endpoint: str, metrics: dict | None) -> None:
    """Aggregate optional scene-coverage diagnostics without changing render outcomes."""

    if not isinstance(metrics, dict):
        return
    name = (endpoint or "").strip() or "unknown"
    values = {key: _nonnegative_metric(metrics, key) for key in _SCENE_SUM_KEYS}
    issues = _scene_counts_by_kind(metrics.get("issues_by_kind"), ("missing", "unresolved"))
    classifications = _scene_counts_by_kind(metrics.get("classifications_by_kind"), _SCENE_CLASSIFICATIONS)

    with _lock:
        bucket = _scene_completeness.setdefault(name, _new_scene_bucket())
        _merge_scene_metrics(
            bucket,
            complete=bool(metrics.get("complete", False)),
            values=values,
            issues=issues,
            classifications=classifications,
        )


def record_render(
    endpoint: str,
    outcome: str,
    *,
    pillow_touches: PillowTouchSnapshot | None = None,
    error_stage: str | None = None,
) -> None:
    """Record one render attempt and consume its request-scoped Pillow touches.

    ``native_pure`` means a successful native render had an active request telemetry
    scope and recorded no Pillow dependency. Calls outside a request scope, including
    heavy-worker results whose touch data has not crossed the process boundary, are
    ``native_unclassified`` rather than being reported as false positives.

    Never raises — observability must not break a request.
    """
    name = (endpoint or "").strip() or "unknown"
    if outcome not in _BACKEND_BY_OUTCOME:
        logger.warning("render_stats got unknown outcome %r for endpoint %s; recording as error", outcome, name)
        outcome = OUTCOME_ERROR
    snapshot = pillow_touches if pillow_touches is not None else take_pillow_touch_snapshot()
    with _lock:
        bucket = _counter_bucket(_counters, name, OUTCOMES)
        bucket[outcome] += 1
        _record_native_purity(name, outcome, snapshot)
        _record_pillow_touches(name, outcome, snapshot)
        _record_error_stage(name, outcome, error_stage)


def record_worker_payload_backend(
    endpoint: str,
    backend: str | None,
    pillow_touch_counts: dict[str, int] | None = None,
) -> str:
    """Parent-side record for a payload rendered inside a heavy-worker process.

    The worker's own counters live in that child process, so the parent replays the outcome
    from the backend carried on the payload. ``backend is None`` means the worker produced the
    image with Pillow (no Skia payload), which is either "Skia is off" or "Skia declined".
    ``pillow_touch_counts is None`` means the child could not classify a successful native
    render; an empty mapping proves it was native-pure.
    Returns the resolved backend label.
    """
    if not backend:
        backend = BACKEND_SKIA_FALLBACK if settings.drawing.use_skia_plot else BACKEND_PILLOW
    local_snapshot = take_pillow_touch_snapshot()
    if pillow_touch_counts is None:
        # Missing child telemetry cannot prove purity, but a known parent-side touch is
        # sufficient to prove the combined request was hybrid.
        snapshot = (
            local_snapshot if local_snapshot.scoped and local_snapshot.counts else PillowTouchSnapshot.unclassified()
        )
    else:
        merged_counts = dict(PillowTouchSnapshot.from_counts(pillow_touch_counts).counts)
        if local_snapshot.scoped:
            for reason, count in local_snapshot.counts.items():
                merged_counts[reason] = merged_counts.get(reason, 0) + count
        snapshot = PillowTouchSnapshot.from_counts(merged_counts)
    record_render(endpoint, _OUTCOME_BY_BACKEND.get(backend, OUTCOME_FALLBACK), pillow_touches=snapshot)
    return backend


def get_render_stats() -> dict:
    """Per-endpoint outcome/purity counters plus totals. JSON-serializable."""
    totals = dict.fromkeys(OUTCOMES, 0)
    totals.update(dict.fromkeys(NATIVE_PURITY_KEYS, 0))
    total_touch_reasons: dict[str, dict[str, int]] = {}
    endpoints: dict[str, dict] = {}
    with _lock:
        for name, bucket in _counters.items():
            entry = dict(bucket)
            entry["total"] = sum(bucket.values())
            purity_bucket = _native_purity_counters.get(name, {})
            for purity in NATIVE_PURITY_KEYS:
                value = purity_bucket.get(purity, 0)
                entry[purity] = value
                totals[purity] += value

            reasons: dict[str, dict[str, int]] = {}
            render_counts = _pillow_touch_render_counts.get(name, {})
            call_counts = _pillow_touch_call_counts.get(name, {})
            for reason in sorted(render_counts):
                reason_entry = {
                    "renders": render_counts[reason],
                    "touches": call_counts.get(reason, 0),
                }
                reasons[reason] = reason_entry
                aggregate = total_touch_reasons.setdefault(reason, {"renders": 0, "touches": 0})
                aggregate["renders"] += reason_entry["renders"]
                aggregate["touches"] += reason_entry["touches"]
            entry["pillow_touch_reasons"] = reasons
            error_stages = _error_stage_counters.get(name)
            if error_stages:
                entry["errors_by_stage"] = dict(sorted(error_stages.items()))
            scene_completeness = _scene_completeness.get(name)
            if scene_completeness is not None:
                entry["scene_completeness"] = {
                    **{
                        key: value
                        for key, value in scene_completeness.items()
                        if key not in {"issues_by_kind", "classifications_by_kind"}
                    },
                    "issues_by_kind": {
                        kind: dict(counts) for kind, counts in sorted(scene_completeness["issues_by_kind"].items())
                    },
                    "classifications_by_kind": {
                        kind: dict(sorted(counts.items()))
                        for kind, counts in sorted(scene_completeness["classifications_by_kind"].items())
                    },
                }
            endpoints[name] = entry
            for outcome in OUTCOMES:
                totals[outcome] += bucket[outcome]
        totals["total"] = sum(totals[outcome] for outcome in OUTCOMES)
        totals["pillow_touch_reasons"] = dict(sorted(total_touch_reasons.items()))
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
        _native_purity_counters.clear()
        _pillow_touch_render_counts.clear()
        _pillow_touch_call_counts.clear()
        _scene_completeness.clear()
        _error_stage_counters.clear()
        _font_fallbacks = 0
