import asyncio
from collections import Counter
import contextvars
from dataclasses import dataclass
import hashlib
import json
import logging
import os
from pathlib import Path
import threading
import time
from typing import Any
from uuid import uuid4

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from src.core.pillow_telemetry import begin_pillow_touch_scope, end_pillow_touch_scope
from src.settings import (
    OVERLOAD_MAX_INFLIGHT_REQUESTS,
    OVERLOAD_RETRY_AFTER_SECONDS,
    READINESS_UNHEALTHY_ASYNCIO_TASKS,
    READINESS_UNHEALTHY_CGROUP_PERCENT,
    READINESS_UNHEALTHY_INFLIGHT_REQUESTS,
    READINESS_UNHEALTHY_RSS_MB,
)

logger = logging.getLogger("src.core.debug")

_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("drawing_request_id", default="-")
_request_path_var: contextvars.ContextVar[str] = contextvars.ContextVar("drawing_request_path", default="-")
_request_method_var: contextvars.ContextVar[str] = contextvars.ContextVar("drawing_request_method", default="-")

# Which renderer actually served this request: skia | skia_cache | skia_fallback | pillow.
# Requests that never attempt Skia keep the default.
DEFAULT_RENDER_BACKEND = "pillow"
_render_backend_var: contextvars.ContextVar[str] = contextvars.ContextVar(
    "drawing_render_backend",
    default=DEFAULT_RENDER_BACKEND,
)


@dataclass(slots=True)
class RequestStageRef:
    value: str = "startup"


_request_stage_var: contextvars.ContextVar[RequestStageRef | None] = contextvars.ContextVar(
    "drawing_request_stage",
    default=None,
)

_inflight_lock = threading.Lock()
_inflight_requests = 0

_http_request_stats_lock = threading.Lock()
_http_request_total = 0
_http_status_counts: Counter[int] = Counter()
_http_status_family_counts: Counter[str] = Counter()
_http_5xx_route_counts: Counter[str] = Counter()
_http_uncaught_exceptions = 0

_SLOW_REQUEST_SECONDS = 1.5
_BODY_PREVIEW_LIMIT = 512
_WATCHDOG_WARN_SECONDS = 10.0
_WATCHDOG_REPEAT_SECONDS = 15.0
_EXEMPT_RUNTIME_GUARD_PATHS = frozenset(
    {
        "/",
        "/health",
        "/ready",
        "/cache/stats",
        "/docs",
        "/redoc",
        "/openapi.json",
    }
)


def _request_route_label(request: Request) -> str:
    """Return a bounded, de-identified route template for aggregate telemetry."""

    route = request.scope.get("route")
    template = getattr(route, "path", None)
    if isinstance(template, str) and template.startswith("/"):
        return template
    return "__unmatched__"


def record_http_request_outcome(
    request: Request,
    status_code: int,
    *,
    uncaught_exception: bool = False,
    route_label: str | None = None,
) -> None:
    """Record an HTTP outcome without retaining request paths, bodies, or identities."""

    global _http_request_total, _http_uncaught_exceptions

    status = int(status_code)
    family = f"{status // 100}xx" if 100 <= status <= 599 else "other"
    label = route_label or _request_route_label(request)
    with _http_request_stats_lock:
        _http_request_total += 1
        _http_status_counts[status] += 1
        _http_status_family_counts[family] += 1
        if family == "5xx":
            _http_5xx_route_counts[label] += 1
        if uncaught_exception:
            _http_uncaught_exceptions += 1


def get_http_request_stats() -> dict[str, Any]:
    """Return process-lifetime aggregate HTTP outcomes for production gates."""

    with _http_request_stats_lock:
        status_counts = dict(sorted(_http_status_counts.items()))
        family_counts = dict(_http_status_family_counts)
        route_counts = dict(sorted(_http_5xx_route_counts.items()))
        total = _http_request_total
        uncaught = _http_uncaught_exceptions

    return {
        "total": total,
        "status_families": {
            family: family_counts.get(family, 0) for family in ("1xx", "2xx", "3xx", "4xx", "5xx", "other")
        },
        "status_codes": {str(status): count for status, count in status_counts.items()},
        "server_errors": {
            "total": family_counts.get("5xx", 0),
            "uncaught_exceptions": uncaught,
            "by_route": route_counts,
        },
    }


def reset_http_request_stats() -> None:
    """Clear aggregate HTTP telemetry (used by tests and controlled soak restarts)."""

    global _http_request_total, _http_uncaught_exceptions

    with _http_request_stats_lock:
        _http_request_total = 0
        _http_status_counts.clear()
        _http_status_family_counts.clear()
        _http_5xx_route_counts.clear()
        _http_uncaught_exceptions = 0


@dataclass(slots=True)
class RequestContextTokens:
    request_id: contextvars.Token
    path: contextvars.Token
    method: contextvars.Token
    stage: contextvars.Token
    render_backend: contextvars.Token | None = None
    pillow_telemetry: contextvars.Token | None = None


def current_request_context() -> dict[str, str]:
    stage_ref = _request_stage_var.get()
    return {
        "request_id": _request_id_var.get(),
        "path": _request_path_var.get(),
        "method": _request_method_var.get(),
        "stage": stage_ref.value if stage_ref is not None else "startup",
    }


def push_request_context(request_id: str, path: str, method: str) -> RequestContextTokens:
    return RequestContextTokens(
        request_id=_request_id_var.set(request_id),
        path=_request_path_var.set(path),
        method=_request_method_var.set(method),
        stage=_request_stage_var.set(RequestStageRef("middleware")),
        render_backend=_render_backend_var.set(DEFAULT_RENDER_BACKEND),
        pillow_telemetry=begin_pillow_touch_scope(),
    )


def pop_request_context(tokens: RequestContextTokens) -> None:
    if tokens.pillow_telemetry is not None:
        end_pillow_touch_scope(tokens.pillow_telemetry)
    _request_id_var.reset(tokens.request_id)
    _request_path_var.reset(tokens.path)
    _request_method_var.reset(tokens.method)
    _request_stage_var.reset(tokens.stage)
    if tokens.render_backend is not None:
        _render_backend_var.reset(tokens.render_backend)


def set_render_backend(backend: str) -> None:
    """Record which renderer served this request (read back by the image.response log line)."""
    _render_backend_var.set((backend or "").strip() or DEFAULT_RENDER_BACKEND)


def current_render_backend() -> str:
    return _render_backend_var.get()


def set_request_stage(stage: str) -> None:
    cleaned = (stage or "").strip() or "unknown"
    stage_ref = _request_stage_var.get()
    if stage_ref is None:
        _request_stage_var.set(RequestStageRef(cleaned))
        return
    stage_ref.value = cleaned


def current_request_stage() -> str:
    stage_ref = _request_stage_var.get()
    return stage_ref.value if stage_ref is not None else "startup"


@dataclass(slots=True)
class RequestWatchdog:
    request_id: str
    method: str
    path: str
    started_at: float
    cancelled: bool = False

    async def run(self) -> None:
        await asyncio.sleep(_WATCHDOG_WARN_SECONDS)
        while not self.cancelled:
            elapsed = time.perf_counter() - self.started_at
            logger.warning(
                "request.stuck id=%s method=%s path=%s stage=%s elapsed=%.3fs metrics=%s",
                self.request_id,
                self.method,
                self.path,
                current_request_stage(),
                elapsed,
                snapshot_process_metrics(include_asyncio=True),
            )
            await asyncio.sleep(_WATCHDOG_REPEAT_SECONDS)

    def cancel(self) -> None:
        self.cancelled = True


def next_request_id() -> str:
    return uuid4().hex[:12]


def inflight_enter() -> int:
    global _inflight_requests
    with _inflight_lock:
        _inflight_requests += 1
        return _inflight_requests


def inflight_leave() -> int:
    global _inflight_requests
    with _inflight_lock:
        _inflight_requests = max(0, _inflight_requests - 1)
        return _inflight_requests


def _read_process_status(status_path: Path) -> dict[str, str]:
    if not status_path.exists():
        return {}
    try:
        lines = status_path.read_text().splitlines()
    except OSError:
        return {}

    status: dict[str, str] = {}
    for line in lines:
        if ":" in line:
            key, value = line.split(":", 1)
            status[key.strip()] = value.strip()
    return status


def _parse_status_kb(status: dict[str, str], key: str) -> float | None:
    parts = status.get(key, "").split()
    if not parts:
        return None
    try:
        return round(int(parts[0]) / 1024, 2)
    except ValueError:
        return None


def _parse_status_int(status: dict[str, str], key: str) -> int | None:
    try:
        return int(status[key])
    except (KeyError, ValueError):
        return None


def _open_fd_count() -> int | None:
    try:
        return len(os.listdir("/proc/self/fd"))
    except OSError:
        return None


def _asyncio_task_count() -> int | None:
    try:
        return len(asyncio.all_tasks())
    except RuntimeError:
        return None


def snapshot_process_metrics(*, include_asyncio: bool = False) -> dict[str, Any]:
    status = _read_process_status(Path("/proc/self/status"))
    metrics = {
        "pid": os.getpid(),
        "rss_mb": _parse_status_kb(status, "VmRSS"),
        "threads": _parse_status_int(status, "Threads"),
        "fds": _open_fd_count(),
        "inflight": _inflight_requests,
    }
    if include_asyncio:
        metrics["asyncio_tasks"] = _asyncio_task_count()
    return metrics


# cgroup v2 first (what any current container runtime gives us), then v1.
_CGROUP_MEMORY_FILES: tuple[tuple[Path, Path], ...] = (
    (Path("/sys/fs/cgroup/memory.current"), Path("/sys/fs/cgroup/memory.max")),
    (
        Path("/sys/fs/cgroup/memory/memory.usage_in_bytes"),
        Path("/sys/fs/cgroup/memory/memory.limit_in_bytes"),
    ),
)
# cgroup v1 spells "no limit" as a huge sentinel (PAGE_COUNTER_MAX scaled by the page size), not as
# a word. Treat anything absurd as unlimited rather than as a ceiling we are 0.0000001% of.
_CGROUP_UNLIMITED_BYTES = 1 << 62


def read_cgroup_memory() -> tuple[float, float] | None:
    """``(usage_mb, limit_mb)`` for the whole container, or ``None`` when there is no limit to read.

    This is the only memory number that can see the heavy-render workers. They are separate
    processes, so the parent's ``VmRSS`` -- which is what ``rss_mb`` reports -- cannot account for
    them, and they are where most of the container's memory actually lives (measured: ~500 MB each
    once warm, versus a parent that peaks under 1 GB). A gate on the parent's RSS therefore cannot
    fire before the kernel OOM-kills the cgroup.

    Returns ``None`` outside a memory-limited cgroup -- bare metal, macOS, an unconstrained
    container -- so the readiness gate simply does not apply rather than guessing.
    """
    for usage_path, limit_path in _CGROUP_MEMORY_FILES:
        try:
            raw_limit = limit_path.read_text().strip()
            raw_usage = usage_path.read_text().strip()
        except (OSError, ValueError):
            continue
        if raw_limit == "max":  # cgroup v2, no limit set
            return None
        try:
            limit = int(raw_limit)
            usage = int(raw_usage)
        except ValueError:
            continue
        if limit <= 0 or limit >= _CGROUP_UNLIMITED_BYTES:
            return None
        return usage / (1024 * 1024), limit / (1024 * 1024)
    return None


def runtime_readiness_thresholds() -> dict[str, int]:
    return {
        "inflight": READINESS_UNHEALTHY_INFLIGHT_REQUESTS,
        "rss_mb": READINESS_UNHEALTHY_RSS_MB,
        "asyncio_tasks": READINESS_UNHEALTHY_ASYNCIO_TASKS,
        "cgroup_percent": READINESS_UNHEALTHY_CGROUP_PERCENT,
    }


def _threshold_reached(value: Any, threshold: int, expected_type: type | tuple[type, ...]) -> bool:
    return threshold > 0 and isinstance(value, expected_type) and value >= threshold


def evaluate_runtime_readiness(metrics: dict[str, Any] | None = None) -> tuple[bool, list[str], dict[str, Any]]:
    if metrics is None:
        metrics = snapshot_process_metrics(include_asyncio=True)

    reasons: list[str] = []
    inflight = metrics.get("inflight")
    rss_mb = metrics.get("rss_mb")
    asyncio_tasks = metrics.get("asyncio_tasks")

    if _threshold_reached(inflight, READINESS_UNHEALTHY_INFLIGHT_REQUESTS, int):
        reasons.append(f"inflight {inflight} >= {READINESS_UNHEALTHY_INFLIGHT_REQUESTS}")
    if _threshold_reached(rss_mb, READINESS_UNHEALTHY_RSS_MB, (int, float)):
        reasons.append(f"rss_mb {rss_mb} >= {READINESS_UNHEALTHY_RSS_MB}")
    if _threshold_reached(asyncio_tasks, READINESS_UNHEALTHY_ASYNCIO_TASKS, int):
        reasons.append(f"asyncio_tasks {asyncio_tasks} >= {READINESS_UNHEALTHY_ASYNCIO_TASKS}")

    # Read the cgroup here rather than in snapshot_process_metrics(): that runs on every request log
    # line, this runs on /ready.
    cgroup = read_cgroup_memory()
    if cgroup is not None:
        usage_mb, limit_mb = cgroup
        percent = usage_mb / limit_mb * 100
        metrics["cgroup_mb"] = round(usage_mb, 2)
        metrics["cgroup_limit_mb"] = round(limit_mb, 2)
        metrics["cgroup_percent"] = round(percent, 1)
        if READINESS_UNHEALTHY_CGROUP_PERCENT > 0 and percent >= READINESS_UNHEALTHY_CGROUP_PERCENT:
            reasons.append(
                f"cgroup_percent {percent:.1f} >= {READINESS_UNHEALTHY_CGROUP_PERCENT} "
                f"({usage_mb:.0f}/{limit_mb:.0f} MB)"
            )

    return len(reasons) == 0, reasons, metrics


def should_reject_for_overload(path: str, inflight: int) -> str | None:
    if OVERLOAD_MAX_INFLIGHT_REQUESTS <= 0:
        return None
    if path in _EXEMPT_RUNTIME_GUARD_PATHS or path.startswith("/docs/") or path.startswith("/redoc/"):
        return None
    if inflight > OVERLOAD_MAX_INFLIGHT_REQUESTS:
        return f"inflight {inflight} > {OVERLOAD_MAX_INFLIGHT_REQUESTS}"
    return None


# Raw dumps carry player payloads (names, IDs, profile text), so they must not outlive the
# capture window by much even when the operator forgets to unset the env: every dump also
# best-effort prunes .json files in the dedicated dump dir older than this.
_DUMP_RETENTION_SECONDS = 24 * 3600


def _prune_stale_dumps(dump_dir: Path) -> None:
    """Best-effort retention sweep of the dedicated dump dir; never raises."""
    cutoff = time.time() - _DUMP_RETENTION_SECONDS
    try:
        for entry in dump_dir.iterdir():
            try:
                if entry.suffix == ".json" and entry.is_file() and entry.stat().st_mtime < cutoff:
                    entry.unlink(missing_ok=True)
            except OSError:
                continue
    except OSError:
        pass


def _dump_request_body(path: str, request_id: str, body: bytes) -> None:
    """Debug-only raw request capture; never raises, no-op unless configured via env.

    The raw bytes ARE the parity fixture format (the sweep model_validates them), which is why
    this lives in the middleware and not a route: a route-level dump would re-serialize the
    parsed model with defaults/aliases applied. Enable with HARUKI_DRAWING__DEBUG_DUMP_REQUEST_DIR
    + _PATHS for a short window, collect, then unset (see the custom-profile migration plan).

    Dumps contain raw player payloads, so the dir and files are owner-only (0700/0600) and each
    write prunes dumps older than ``_DUMP_RETENTION_SECONDS`` from the dedicated dir.
    """
    try:
        from src.settings import settings

        dump_dir = settings.drawing.debug_dump_request_dir
        if not dump_dir or not body:
            return
        prefixes = [p.strip() for p in settings.drawing.debug_dump_request_paths.split(",") if p.strip()]
        if not any(path.startswith(prefix) for prefix in prefixes):
            return
        dump_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
        try:
            os.chmod(dump_dir, 0o700)  # mkdir mode does not apply to a pre-existing dir
        except OSError:
            pass
        _prune_stale_dumps(dump_dir)
        slug = path.strip("/").replace("/", "_")
        target = dump_dir / f"{slug}_{int(time.time())}_{request_id}.json"
        fd = os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "wb") as fh:
            fh.write(body)
    except Exception:
        logger.warning("request body dump failed", exc_info=True)


def _decode_body_preview(body: bytes, content_type: str) -> str:
    if not ("json" in content_type or "text" in content_type or content_type == ""):
        return ""
    try:
        preview = body.decode("utf-8", errors="replace")
    except Exception:
        return ""
    preview = preview.replace("\n", "\\n").replace("\r", "\\r")
    if len(preview) > _BODY_PREVIEW_LIMIT:
        return preview[:_BODY_PREVIEW_LIMIT] + "...(truncated)"
    return preview


def _summarize_json_body(body: bytes) -> tuple[str, Any]:
    try:
        return "shape", summarize_json_shape(json.loads(body))
    except Exception as exc:
        return "json_error", str(exc)


def summarize_request_body(body: bytes, content_type: str | None) -> dict[str, Any]:
    content_type = (content_type or "").strip()
    digest = hashlib.sha256(body).hexdigest()[:16]
    summary: dict[str, Any] = {
        "bytes": len(body),
        "sha256_16": digest,
        "content_type": content_type,
    }
    if not body:
        return summary

    text_preview = _decode_body_preview(body, content_type)
    if text_preview:
        summary["preview"] = text_preview

    if "json" in content_type:
        key, value = _summarize_json_body(body)
        summary[key] = value
    return summary


def _copy_nonempty(
    source: dict[str, Any], target: dict[str, Any], source_key: str, target_key: str | None = None
) -> None:
    value = source.get(source_key)
    if value not in (None, ""):
        target[target_key or source_key] = value


def _copy_list_lengths(payload: dict[str, Any], focus: dict[str, Any]) -> None:
    for source_key, target_key in (
        ("honors", "honors"),
        ("pcards", "pcards"),
        ("maps", "maps"),
        ("deck_data", "decks"),
    ):
        value = payload.get(source_key)
        if isinstance(value, list):
            focus[target_key] = len(value)


def _extract_bg_focus(payload: dict[str, Any]) -> dict[str, Any]:
    bg_settings = payload.get("bg_settings")
    if not isinstance(bg_settings, dict):
        return {}
    return {key: bg_settings.get(key) for key in ("alpha", "blur", "vertical", "img_path") if key in bg_settings}


def extract_debug_request_focus(path: str, body: bytes, content_type: str | None) -> dict[str, Any] | None:
    content_type = (content_type or "").strip().lower()
    if "json" not in content_type or not body:
        return None
    try:
        payload = json.loads(body)
    except Exception:
        return None

    if not isinstance(payload, dict):
        return None

    focus: dict[str, Any] = {}
    profile = payload.get("profile")
    if isinstance(profile, dict):
        _copy_nonempty(profile, focus, "id", "profile_id")
        _copy_nonempty(profile, focus, "region", "profile_region")

    _copy_nonempty(payload, focus, "region")
    _copy_list_lengths(payload, focus)
    bg_focus = _extract_bg_focus(payload)
    if bg_focus:
        focus["bg_settings"] = bg_focus

    if not focus:
        return None
    focus["path"] = path
    return focus


def _summarize_json_list(value: list[Any], depth: int) -> dict[str, Any]:
    result: dict[str, Any] = {"type": "list", "len": len(value)}
    if value:
        result["sample"] = summarize_json_shape(value[0], depth=depth + 1)
    return result


def _summarize_json_dict(value: dict[Any, Any]) -> dict[str, Any]:
    items = list(value.items())[:20]
    result: dict[str, Any] = {"type": "dict", "keys": list(value.keys())[:20]}
    list_lengths = {str(key): len(child) for key, child in items if isinstance(child, list)}
    if list_lengths:
        result["list_lengths"] = list_lengths
    return result


def summarize_json_shape(value: Any, *, depth: int = 0) -> Any:
    if depth >= 2:
        return type(value).__name__
    if value is None or isinstance(value, str | int | float | bool):
        if isinstance(value, str):
            return value[:80] + ("..." if len(value) > 80 else "")
        return value
    if isinstance(value, list):
        return _summarize_json_list(value, depth)
    if isinstance(value, dict):
        return _summarize_json_dict(value)
    return type(value).__name__


@dataclass(slots=True)
class _DebugRequestTrace:
    request_id: str
    started_at: float
    inflight: int
    tokens: RequestContextTokens | None = None
    watchdog: RequestWatchdog | None = None
    watchdog_task: asyncio.Task[None] | None = None

    def begin(self, request: Request) -> None:
        self.tokens = push_request_context(self.request_id, request.url.path, request.method)
        self.watchdog = RequestWatchdog(
            request_id=self.request_id,
            method=request.method,
            path=request.url.path,
            started_at=self.started_at,
        )
        self.watchdog_task = asyncio.create_task(self.watchdog.run())

    async def finish(self) -> None:
        if self.watchdog is not None:
            self.watchdog.cancel()
        if self.watchdog_task is not None:
            self.watchdog_task.cancel()
            await asyncio.gather(self.watchdog_task, return_exceptions=True)
        inflight_leave()
        if self.tokens is not None:
            pop_request_context(self.tokens)


def _new_debug_request_trace() -> _DebugRequestTrace:
    return _DebugRequestTrace(
        request_id=next_request_id(),
        started_at=time.perf_counter(),
        inflight=inflight_enter(),
    )


def _overload_response(request: Request, trace: _DebugRequestTrace) -> JSONResponse | None:
    reason = should_reject_for_overload(request.url.path, trace.inflight)
    if reason is None:
        return None

    metrics = snapshot_process_metrics(include_asyncio=True)
    logger.warning(
        "request.reject id=%s method=%s path=%s query=%s client=%s reason=%s inflight=%s metrics=%s",
        trace.request_id,
        request.method,
        request.url.path,
        request.url.query,
        getattr(request.client, "host", "-"),
        reason,
        trace.inflight,
        metrics,
    )
    headers = {}
    if OVERLOAD_RETRY_AFTER_SECONDS > 0:
        headers["Retry-After"] = str(OVERLOAD_RETRY_AFTER_SECONDS)
    record_http_request_outcome(request, 503, route_label="__overload__")
    return JSONResponse(
        status_code=503,
        headers=headers,
        content={"status": "overloaded", "reason": reason, "inflight": trace.inflight},
    )


async def _log_request_start(request: Request, trace: _DebugRequestTrace) -> None:
    body = await request.body()
    _dump_request_body(request.url.path, trace.request_id, body)
    content_type = request.headers.get("content-type")
    logger.info(
        "request.start id=%s method=%s path=%s query=%s client=%s inflight=%s body=%s focus=%s metrics=%s",
        trace.request_id,
        request.method,
        request.url.path,
        request.url.query,
        getattr(request.client, "host", "-"),
        trace.inflight,
        summarize_request_body(body, content_type),
        extract_debug_request_focus(request.url.path, body, content_type),
        snapshot_process_metrics(include_asyncio=True),
    )


def _log_request_error(request: Request, trace: _DebugRequestTrace) -> None:
    elapsed = time.perf_counter() - trace.started_at
    record_http_request_outcome(request, 500, uncaught_exception=True)
    logger.exception(
        "request.error id=%s method=%s path=%s stage=%s elapsed=%.3fs inflight=%s metrics=%s",
        trace.request_id,
        request.method,
        request.url.path,
        current_request_stage(),
        elapsed,
        _inflight_requests,
        snapshot_process_metrics(include_asyncio=True),
    )


def _slow_request_cache_stats(elapsed: float) -> dict[str, Any] | None:
    if elapsed < _SLOW_REQUEST_SECONDS:
        return None
    try:
        from src.sekai.base.utils import get_runtime_cache_stats

        return get_runtime_cache_stats()
    except Exception as exc:
        return {"error": str(exc)}


def _response_header(response: Any, name: str) -> str | None:
    headers = getattr(response, "headers", None)
    return headers.get(name) if headers is not None else None


def _log_request_end(request: Request, response: Any, trace: _DebugRequestTrace) -> None:
    elapsed = time.perf_counter() - trace.started_at
    record_http_request_outcome(request, getattr(response, "status_code", 500))
    level = logging.WARNING if elapsed >= _SLOW_REQUEST_SECONDS else logging.INFO
    logger.log(
        level,
        "request.end id=%s method=%s path=%s stage=%s status=%s elapsed=%.3fs inflight=%s "
        "metrics=%s headers={content_length=%s content_type=%s} cache_stats=%s",
        trace.request_id,
        request.method,
        request.url.path,
        current_request_stage(),
        getattr(response, "status_code", "-"),
        elapsed,
        _inflight_requests,
        snapshot_process_metrics(include_asyncio=True),
        _response_header(response, "content-length"),
        _response_header(response, "content-type"),
        _slow_request_cache_stats(elapsed),
    )


async def _run_debug_request(request: Request, call_next: Any):
    trace = _new_debug_request_trace()
    try:
        overload = _overload_response(request, trace)
        if overload is not None:
            return overload
        trace.begin(request)
        await _log_request_start(request, trace)
        set_request_stage("handler")
        response = await call_next(request)
    except Exception:
        _log_request_error(request, trace)
        raise
    else:
        _log_request_end(request, response, trace)
        return response
    finally:
        await trace.finish()


def install_debug_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def _debug_request_middleware(request: Request, call_next):
        return await _run_debug_request(request, call_next)
