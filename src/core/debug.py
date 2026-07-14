import asyncio
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


@dataclass(slots=True)
class RequestContextTokens:
    request_id: contextvars.Token
    path: contextvars.Token
    method: contextvars.Token
    stage: contextvars.Token
    render_backend: contextvars.Token | None = None


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
    )


def pop_request_context(tokens: RequestContextTokens) -> None:
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


def snapshot_process_metrics(*, include_asyncio: bool = False) -> dict[str, Any]:
    status = {}
    status_path = Path("/proc/self/status")
    if status_path.exists():
        try:
            for line in status_path.read_text().splitlines():
                if ":" not in line:
                    continue
                key, value = line.split(":", 1)
                status[key.strip()] = value.strip()
        except OSError:
            pass

    rss_mb = None
    vm_rss = status.get("VmRSS")
    if vm_rss:
        parts = vm_rss.split()
        if parts:
            try:
                rss_mb = round(int(parts[0]) / 1024, 2)
            except ValueError:
                rss_mb = None

    thread_count = None
    if status.get("Threads"):
        try:
            thread_count = int(status["Threads"])
        except ValueError:
            thread_count = None

    fd_count = None
    try:
        fd_count = len(os.listdir("/proc/self/fd"))
    except OSError:
        fd_count = None

    metrics = {
        "pid": os.getpid(),
        "rss_mb": rss_mb,
        "threads": thread_count,
        "fds": fd_count,
        "inflight": _inflight_requests,
    }
    if include_asyncio:
        try:
            metrics["asyncio_tasks"] = len(asyncio.all_tasks())
        except RuntimeError:
            metrics["asyncio_tasks"] = None
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


def evaluate_runtime_readiness(metrics: dict[str, Any] | None = None) -> tuple[bool, list[str], dict[str, Any]]:
    if metrics is None:
        metrics = snapshot_process_metrics(include_asyncio=True)

    reasons: list[str] = []
    inflight = metrics.get("inflight")
    rss_mb = metrics.get("rss_mb")
    asyncio_tasks = metrics.get("asyncio_tasks")

    if READINESS_UNHEALTHY_INFLIGHT_REQUESTS > 0 and isinstance(inflight, int):
        if inflight >= READINESS_UNHEALTHY_INFLIGHT_REQUESTS:
            reasons.append(f"inflight {inflight} >= {READINESS_UNHEALTHY_INFLIGHT_REQUESTS}")
    if READINESS_UNHEALTHY_RSS_MB > 0 and isinstance(rss_mb, int | float):
        if rss_mb >= READINESS_UNHEALTHY_RSS_MB:
            reasons.append(f"rss_mb {rss_mb} >= {READINESS_UNHEALTHY_RSS_MB}")
    if READINESS_UNHEALTHY_ASYNCIO_TASKS > 0 and isinstance(asyncio_tasks, int):
        if asyncio_tasks >= READINESS_UNHEALTHY_ASYNCIO_TASKS:
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

    text_preview = ""
    if "json" in content_type or "text" in content_type or content_type == "":
        try:
            text_preview = body.decode("utf-8", errors="replace")
        except Exception:
            text_preview = ""

    if text_preview:
        text_preview = text_preview.replace("\n", "\\n").replace("\r", "\\r")
        if len(text_preview) > _BODY_PREVIEW_LIMIT:
            text_preview = text_preview[:_BODY_PREVIEW_LIMIT] + "...(truncated)"
        summary["preview"] = text_preview

    if "json" in content_type and body:
        try:
            payload = json.loads(body)
            summary["shape"] = summarize_json_shape(payload)
        except Exception as exc:
            summary["json_error"] = str(exc)
    return summary


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
        if profile.get("id") not in (None, ""):
            focus["profile_id"] = profile.get("id")
        if profile.get("region") not in (None, ""):
            focus["profile_region"] = profile.get("region")

    if payload.get("region") not in (None, ""):
        focus["region"] = payload.get("region")
    if isinstance(payload.get("honors"), list):
        focus["honors"] = len(payload["honors"])
    if isinstance(payload.get("pcards"), list):
        focus["pcards"] = len(payload["pcards"])
    if isinstance(payload.get("maps"), list):
        focus["maps"] = len(payload["maps"])
    if isinstance(payload.get("deck_data"), list):
        focus["decks"] = len(payload["deck_data"])

    bg_settings = payload.get("bg_settings")
    if isinstance(bg_settings, dict):
        bg_focus = {}
        for key in ("alpha", "blur", "vertical", "img_path"):
            if key in bg_settings:
                bg_focus[key] = bg_settings.get(key)
        if bg_focus:
            focus["bg_settings"] = bg_focus

    if not focus:
        return None
    focus["path"] = path
    return focus


def summarize_json_shape(value: Any, *, depth: int = 0) -> Any:
    if depth >= 2:
        return type(value).__name__
    if value is None or isinstance(value, str | int | float | bool):
        if isinstance(value, str):
            return value[:80] + ("..." if len(value) > 80 else "")
        return value
    if isinstance(value, list):
        result = {"type": "list", "len": len(value)}
        if value:
            result["sample"] = summarize_json_shape(value[0], depth=depth + 1)
        return result
    if isinstance(value, dict):
        keys = list(value.keys())
        result = {"type": "dict", "keys": keys[:20]}
        list_lengths = {}
        for key, child in list(value.items())[:20]:
            if isinstance(child, list):
                list_lengths[str(key)] = len(child)
        if list_lengths:
            result["list_lengths"] = list_lengths
        return result
    return type(value).__name__


def install_debug_middleware(app: FastAPI) -> None:
    @app.middleware("http")
    async def _debug_request_middleware(request: Request, call_next):
        request_id = next_request_id()
        start = time.perf_counter()
        inflight_now = inflight_enter()
        tokens: RequestContextTokens | None = None
        watchdog: RequestWatchdog | None = None
        watchdog_task: asyncio.Task[None] | None = None
        try:
            overload_reason = should_reject_for_overload(request.url.path, inflight_now)
            if overload_reason is not None:
                metrics = snapshot_process_metrics(include_asyncio=True)
                logger.warning(
                    "request.reject id=%s method=%s path=%s query=%s client=%s reason=%s inflight=%s metrics=%s",
                    request_id,
                    request.method,
                    request.url.path,
                    request.url.query,
                    getattr(request.client, "host", "-"),
                    overload_reason,
                    inflight_now,
                    metrics,
                )
                headers = {}
                if OVERLOAD_RETRY_AFTER_SECONDS > 0:
                    headers["Retry-After"] = str(OVERLOAD_RETRY_AFTER_SECONDS)
                return JSONResponse(
                    status_code=503,
                    headers=headers,
                    content={
                        "status": "overloaded",
                        "reason": overload_reason,
                        "inflight": inflight_now,
                    },
                )

            tokens = push_request_context(request_id, request.url.path, request.method)
            watchdog = RequestWatchdog(
                request_id=request_id,
                method=request.method,
                path=request.url.path,
                started_at=start,
            )
            watchdog_task = asyncio.create_task(watchdog.run())
            body = await request.body()
            body_summary = summarize_request_body(body, request.headers.get("content-type"))
            focus_summary = extract_debug_request_focus(request.url.path, body, request.headers.get("content-type"))
            start_metrics = snapshot_process_metrics(include_asyncio=True)

            logger.info(
                "request.start id=%s method=%s path=%s query=%s client=%s inflight=%s body=%s focus=%s metrics=%s",
                request_id,
                request.method,
                request.url.path,
                request.url.query,
                getattr(request.client, "host", "-"),
                inflight_now,
                body_summary,
                focus_summary,
                start_metrics,
            )
            set_request_stage("handler")
            response = await call_next(request)
        except Exception:
            elapsed = time.perf_counter() - start
            end_metrics = snapshot_process_metrics(include_asyncio=True)
            logger.exception(
                "request.error id=%s method=%s path=%s stage=%s elapsed=%.3fs inflight=%s metrics=%s",
                request_id,
                request.method,
                request.url.path,
                current_request_stage(),
                elapsed,
                _inflight_requests,
                end_metrics,
            )
            raise
        else:
            elapsed = time.perf_counter() - start
            end_metrics = snapshot_process_metrics(include_asyncio=True)
            level = logging.WARNING if elapsed >= _SLOW_REQUEST_SECONDS else logging.INFO
            cache_stats = None
            if elapsed >= _SLOW_REQUEST_SECONDS:
                try:
                    from src.sekai.base.utils import get_runtime_cache_stats

                    cache_stats = get_runtime_cache_stats()
                except Exception as exc:
                    cache_stats = {"error": str(exc)}
            logger.log(
                level,
                "request.end id=%s method=%s path=%s stage=%s status=%s elapsed=%.3fs inflight=%s "
                "metrics=%s headers={content_length=%s content_type=%s} cache_stats=%s",
                request_id,
                request.method,
                request.url.path,
                current_request_stage(),
                getattr(response, "status_code", "-"),
                elapsed,
                _inflight_requests,
                end_metrics,
                getattr(response, "headers", {}).get("content-length") if getattr(response, "headers", None) else None,
                getattr(response, "headers", {}).get("content-type") if getattr(response, "headers", None) else None,
                cache_stats,
            )
            return response
        finally:
            if watchdog is not None:
                watchdog.cancel()
            if watchdog_task is not None:
                watchdog_task.cancel()
                await asyncio.gather(watchdog_task, return_exceptions=True)
            inflight_leave()
            if tokens is not None:
                pop_request_context(tokens)
