"""Owner-only, request-free diagnostics for committed Custom Profile Skia failures.

The production request is deliberately absent.  A record contains only a bounded source
traceback, exception type/message fingerprint, render stage, and aggregate scene coverage.
That is enough to correlate and locate failures without retaining a card, profile, user ID,
asset path, URL, exception message, or request filename.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
import hashlib
import json
import logging
import os
from pathlib import Path
import re
import threading
import time
from typing import Any
from uuid import uuid4

logger = logging.getLogger("custom_profile.diagnostics")

_DIAGNOSTIC_PREFIX = "custom-profile-diagnostic-"
_DIAGNOSTIC_SUFFIX = ".json"
_MAX_EXCEPTION_CHAIN = 4
_MAX_FRAMES_PER_EXCEPTION = 64
_SAFE_EXCEPTION_TYPES = frozenset(
    {
        "FileNotFoundError",
        "ImportError",
        "KeyError",
        "MemoryError",
        "OSError",
        "OverflowError",
        "RuntimeError",
        "TypeError",
        "ValueError",
    }
)
_SAFE_STAGE_RE = re.compile(r"^[a-z][a-z0-9_]{0,63}$")
_SAFE_FUNCTION_RE = re.compile(r"^[A-Za-z0-9_<>.]{1,160}$")
_SAFE_MODULE_RE = re.compile(r"^(?:src|tests)(?:\.[A-Za-z_][A-Za-z0-9_]*)+$")
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_SAFE_RELATIONS = frozenset({"raised", "cause", "context"})
_write_lock = threading.Lock()


def _safe_exception_type(exc: BaseException) -> str:
    candidate = type(exc).__name__
    return candidate if candidate in _SAFE_EXCEPTION_TYPES else "OtherError"


def _safe_stage(stage: str | None) -> str:
    candidate = str(stage or "unknown").strip().lower()
    return candidate if _SAFE_STAGE_RE.fullmatch(candidate) else "unknown"


def _safe_frame(frame) -> dict[str, Any]:
    # Module/function/line identify repository code without retaining co_filename: even a
    # basename could be a production request or asset filename in dynamically loaded code.
    module = str(frame.tb_frame.f_globals.get("__name__", "unknown"))
    if not _SAFE_MODULE_RE.fullmatch(module):
        module = "unknown"
    function = frame.tb_frame.f_code.co_name
    if not _SAFE_FUNCTION_RE.fullmatch(function):
        function = "unknown"
    return {
        "module": module,
        "function": function,
        "line": max(0, int(frame.tb_lineno)),
    }


def _exception_message_fingerprint(exc: BaseException) -> tuple[str, int]:
    try:
        message = str(exc)
    except Exception:
        message = ""
    return hashlib.sha256(message.encode("utf-8", errors="replace")).hexdigest(), len(message)


def capture_safe_exception(exc: BaseException) -> dict[str, Any]:
    """Return a request-free exception chain. Never includes the exception message itself."""

    chain: list[dict[str, Any]] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    relation = "raised"
    while current is not None and len(chain) < _MAX_EXCEPTION_CHAIN and id(current) not in seen:
        seen.add(id(current))
        message_sha256, message_length = _exception_message_fingerprint(current)
        frames: list[dict[str, Any]] = []
        traceback_cursor = current.__traceback__
        while traceback_cursor is not None and len(frames) < _MAX_FRAMES_PER_EXCEPTION:
            frames.append(_safe_frame(traceback_cursor))
            traceback_cursor = traceback_cursor.tb_next
        chain.append(
            {
                "relation": relation,
                "type": _safe_exception_type(current),
                "message_sha256": message_sha256,
                "message_length": message_length,
                "frames": frames,
            }
        )
        if current.__cause__ is not None:
            current = current.__cause__
            relation = "cause"
        elif current.__context__ is not None and not current.__suppress_context__:
            current = current.__context__
            relation = "context"
        else:
            current = None
    return {"exception_chain": chain}


def _sanitize_exception_diagnostic(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Re-apply a strict field whitelist at the persistence boundary."""

    if not isinstance(value, Mapping):
        return None
    raw_chain = value.get("exception_chain")
    if not isinstance(raw_chain, list):
        return None
    chain: list[dict[str, Any]] = []
    for raw_item in raw_chain[:_MAX_EXCEPTION_CHAIN]:
        if not isinstance(raw_item, Mapping):
            continue
        relation = str(raw_item.get("relation", "raised"))
        if relation not in _SAFE_RELATIONS:
            relation = "raised"
        error_type = str(raw_item.get("type", "OtherError"))
        if error_type not in _SAFE_EXCEPTION_TYPES and error_type != "OtherError":
            error_type = "OtherError"
        digest = str(raw_item.get("message_sha256", ""))
        if not _SHA256_RE.fullmatch(digest):
            digest = hashlib.sha256(b"").hexdigest()
        frames: list[dict[str, Any]] = []
        raw_frames = raw_item.get("frames")
        if isinstance(raw_frames, list):
            for raw_frame in raw_frames[:_MAX_FRAMES_PER_EXCEPTION]:
                if not isinstance(raw_frame, Mapping):
                    continue
                module = str(raw_frame.get("module", "unknown"))
                if not _SAFE_MODULE_RE.fullmatch(module):
                    module = "unknown"
                function = str(raw_frame.get("function", "unknown"))
                if not _SAFE_FUNCTION_RE.fullmatch(function):
                    function = "unknown"
                frames.append(
                    {
                        "module": module,
                        "function": function,
                        "line": _non_negative_int(raw_frame.get("line")),
                    }
                )
        chain.append(
            {
                "relation": relation,
                "type": error_type,
                "message_sha256": digest,
                "message_length": _non_negative_int(raw_item.get("message_length")),
                "frames": frames,
            }
        )
    return {"exception_chain": chain} if chain else None


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _http_class(status: int | None) -> str:
    try:
        code = int(status or 0)
    except (TypeError, ValueError, OverflowError):
        return "unknown"
    return f"{code // 100}xx" if 100 <= code <= 599 else "unknown"


def sanitize_scene_metrics(metrics: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Keep only aggregate counters and renderer-owned category names."""

    if not isinstance(metrics, Mapping):
        return None
    result: dict[str, Any] = {
        "complete": bool(metrics.get("complete", False)),
    }
    for key in (
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
    ):
        result[key] = _non_negative_int(metrics.get(key))

    for aggregate_key in ("issues_by_kind", "classifications_by_kind"):
        raw_aggregate = metrics.get(aggregate_key)
        aggregate: dict[str, dict[str, int]] = {}
        if isinstance(raw_aggregate, Mapping):
            for raw_kind, raw_counts in raw_aggregate.items():
                kind = str(raw_kind or "").strip().lower()
                if not _SAFE_STAGE_RE.fullmatch(kind) or not isinstance(raw_counts, Mapping):
                    continue
                counts: dict[str, int] = {}
                for raw_status, raw_count in raw_counts.items():
                    status = str(raw_status or "").strip().lower()
                    if not _SAFE_STAGE_RE.fullmatch(status):
                        continue
                    count = _non_negative_int(raw_count)
                    if count:
                        counts[status] = count
                if counts:
                    aggregate[kind] = dict(sorted(counts.items()))
        result[aggregate_key] = dict(sorted(aggregate.items()))
    return result


def _diagnostic_files(directory: Path) -> list[Path]:
    try:
        return [
            entry
            for entry in directory.iterdir()
            if entry.is_file() and entry.name.startswith(_DIAGNOSTIC_PREFIX) and entry.suffix == _DIAGNOSTIC_SUFFIX
        ]
    except OSError:
        return []


def _prune(directory: Path, *, retention_hours: int, max_files: int, now: float) -> None:
    cutoff = now - retention_hours * 3600
    retained: list[tuple[float, Path]] = []
    for path in _diagnostic_files(directory):
        try:
            modified = path.stat().st_mtime
            if modified < cutoff:
                path.unlink(missing_ok=True)
            else:
                retained.append((modified, path))
        except OSError:
            continue
    retained.sort(key=lambda item: item[0])
    for _, path in retained[: max(0, len(retained) - max_files)]:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            continue


def persist_custom_profile_diagnostic(
    *,
    outcome: str,
    stage: str | None,
    error_type: str | None,
    exception: Mapping[str, Any] | None = None,
    scene_metrics: Mapping[str, Any] | None = None,
    final_http_status: int | None = None,
) -> bool:
    """Atomically persist one committed diagnostic. Best-effort and never raises."""

    try:
        from src.settings import settings

        directory = settings.drawing.custom_profile_diagnostic_dir
        if directory is None:
            return False
        retention_hours = int(settings.drawing.custom_profile_diagnostic_retention_hours)
        max_files = int(settings.drawing.custom_profile_diagnostic_max_files)
        safe_outcome = outcome if outcome in {"error", "fallback"} else "error"
        safe_error_type = error_type if error_type in _SAFE_EXCEPTION_TYPES else "OtherError" if error_type else None
        record = {
            "schema_version": 1,
            "recorded_at": datetime.now(UTC).isoformat(),
            "endpoint": "custom_profile_card",
            "outcome": safe_outcome,
            "stage": _safe_stage(stage),
            "error_type": safe_error_type,
            "final_http_class": _http_class(final_http_status),
            "exception": _sanitize_exception_diagnostic(exception),
            "scene": sanitize_scene_metrics(scene_metrics),
        }
        payload = json.dumps(record, ensure_ascii=True, sort_keys=True, separators=(",", ":")).encode("utf-8")

        with _write_lock:
            directory.mkdir(parents=True, exist_ok=True, mode=0o700)
            try:
                os.chmod(directory, 0o700)
            except OSError:
                pass
            now = time.time()
            _prune(directory, retention_hours=retention_hours, max_files=max_files - 1, now=now)
            stem = f"{_DIAGNOSTIC_PREFIX}{time.time_ns()}-{uuid4().hex}"
            temporary = directory / f".{stem}.tmp"
            target = directory / f"{stem}{_DIAGNOSTIC_SUFFIX}"
            fd = os.open(temporary, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            try:
                with os.fdopen(fd, "wb") as handle:
                    handle.write(payload)
                    handle.flush()
                    os.fsync(handle.fileno())
                os.replace(temporary, target)
                try:
                    os.chmod(target, 0o600)
                except OSError:
                    pass
            finally:
                try:
                    temporary.unlink(missing_ok=True)
                except OSError:
                    pass
        return True
    except Exception as exc:
        logger.warning("custom profile diagnostic persistence failed: error_type=%s", type(exc).__name__)
        return False
