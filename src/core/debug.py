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


logger = logging.getLogger("src.core.debug")

_request_id_var: contextvars.ContextVar[str] = contextvars.ContextVar("drawing_request_id", default="-")
_request_path_var: contextvars.ContextVar[str] = contextvars.ContextVar("drawing_request_path", default="-")
_request_method_var: contextvars.ContextVar[str] = contextvars.ContextVar("drawing_request_method", default="-")

_inflight_lock = threading.Lock()
_inflight_requests = 0

_SLOW_REQUEST_SECONDS = 1.5
_BODY_PREVIEW_LIMIT = 512


@dataclass(slots=True)
class RequestContextTokens:
    request_id: contextvars.Token
    path: contextvars.Token
    method: contextvars.Token


def current_request_context() -> dict[str, str]:
    return {
        "request_id": _request_id_var.get(),
        "path": _request_path_var.get(),
        "method": _request_method_var.get(),
    }


def push_request_context(request_id: str, path: str, method: str) -> RequestContextTokens:
    return RequestContextTokens(
        request_id=_request_id_var.set(request_id),
        path=_request_path_var.set(path),
        method=_request_method_var.set(method),
    )


def pop_request_context(tokens: RequestContextTokens) -> None:
    _request_id_var.reset(tokens.request_id)
    _request_path_var.reset(tokens.path)
    _request_method_var.reset(tokens.method)


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
        body = await request.body()
        inflight_now = inflight_enter()
        tokens = push_request_context(request_id, request.url.path, request.method)
        body_summary = summarize_request_body(body, request.headers.get("content-type"))
        start_metrics = snapshot_process_metrics(include_asyncio=True)

        logger.info(
            "request.start id=%s method=%s path=%s query=%s client=%s inflight=%s body=%s metrics=%s",
            request_id,
            request.method,
            request.url.path,
            request.url.query,
            getattr(request.client, "host", "-"),
            inflight_now,
            body_summary,
            start_metrics,
        )
        try:
            response = await call_next(request)
        except Exception:
            elapsed = time.perf_counter() - start
            end_metrics = snapshot_process_metrics(include_asyncio=True)
            logger.exception(
                "request.error id=%s method=%s path=%s elapsed=%.3fs inflight=%s metrics=%s",
                request_id,
                request.method,
                request.url.path,
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
                "request.end id=%s method=%s path=%s status=%s elapsed=%.3fs inflight=%s metrics=%s headers={content_length=%s content_type=%s} cache_stats=%s",
                request_id,
                request.method,
                request.url.path,
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
            inflight_leave()
            pop_request_context(tokens)
