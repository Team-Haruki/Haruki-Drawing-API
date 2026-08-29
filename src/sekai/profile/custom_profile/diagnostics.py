"""Owner-only, request-free diagnostics for committed Custom Profile Skia failures.

The production request is deliberately absent.  A record contains only a bounded source
traceback, exception type, render stage, and aggregate scene coverage.
That is enough to correlate and locate failures without retaining a card, profile, user ID,
asset path, URL, exception message, or request filename.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
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


def capture_safe_exception(exc: BaseException) -> dict[str, Any]:
    """Return a request-free exception chain. Never includes the exception message itself."""

    chain: list[dict[str, Any]] = []
    seen: set[int] = set()
    current: BaseException | None = exc
    relation = "raised"
    while current is not None and len(chain) < _MAX_EXCEPTION_CHAIN and id(current) not in seen:
        seen.add(id(current))
        frames: list[dict[str, Any]] = []
        traceback_cursor = current.__traceback__
        while traceback_cursor is not None and len(frames) < _MAX_FRAMES_PER_EXCEPTION:
            frames.append(_safe_frame(traceback_cursor))
            traceback_cursor = traceback_cursor.tb_next
        chain.append(
            {
                "relation": relation,
                "type": _safe_exception_type(current),
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


def _non_negative_int(value: Any) -> int:
    try:
        return max(0, int(value or 0))
    except (TypeError, ValueError, OverflowError):
        return 0


def _sanitize_frame_mapping(value: Any) -> dict[str, Any] | None:
    """Normalize one traceback frame without accepting filenames, paths, or locals."""

    if not isinstance(value, Mapping):
        return None
    module = str(value.get("module", "unknown"))
    if not _SAFE_MODULE_RE.fullmatch(module):
        module = "unknown"
    function = str(value.get("function", "unknown"))
    if not _SAFE_FUNCTION_RE.fullmatch(function):
        function = "unknown"
    return {"module": module, "function": function, "line": _non_negative_int(value.get("line"))}


def _sanitize_exception_item(value: Any) -> dict[str, Any] | None:
    """Normalize one exception-chain item through the persistence whitelist."""

    if not isinstance(value, Mapping):
        return None
    relation = str(value.get("relation", "raised"))
    if relation not in _SAFE_RELATIONS:
        relation = "raised"
    error_type = str(value.get("type", "OtherError"))
    if error_type not in _SAFE_EXCEPTION_TYPES and error_type != "OtherError":
        error_type = "OtherError"
    raw_frames = value.get("frames")
    frames = (
        []
        if not isinstance(raw_frames, list)
        else [_sanitize_frame_mapping(frame) for frame in raw_frames[:_MAX_FRAMES_PER_EXCEPTION]]
    )
    return {
        "relation": relation,
        "type": error_type,
        "frames": [frame for frame in frames if frame is not None],
    }


def _sanitize_exception_diagnostic(value: Mapping[str, Any] | None) -> dict[str, Any] | None:
    """Re-apply a strict field whitelist at the persistence boundary."""

    if not isinstance(value, Mapping):
        return None
    raw_chain = value.get("exception_chain")
    if not isinstance(raw_chain, list):
        return None
    chain = [_sanitize_exception_item(item) for item in raw_chain[:_MAX_EXCEPTION_CHAIN]]
    safe_chain = [item for item in chain if item is not None]
    return {"exception_chain": safe_chain} if safe_chain else None


def _http_class(status: int | None) -> str:
    try:
        code = int(status or 0)
    except (TypeError, ValueError, OverflowError):
        return "unknown"
    return f"{code // 100}xx" if 100 <= code <= 599 else "unknown"


def _sanitize_count_mapping(value: Any) -> dict[str, int]:
    """Keep positive counts keyed only by renderer-owned category tokens."""

    if not isinstance(value, Mapping):
        return {}
    counts = {
        str(raw_status or "").strip().lower(): _non_negative_int(raw_count)
        for raw_status, raw_count in value.items()
        if _SAFE_STAGE_RE.fullmatch(str(raw_status or "").strip().lower())
    }
    return dict(sorted((status, count) for status, count in counts.items() if count))


def _sanitize_aggregate_mapping(value: Any) -> dict[str, dict[str, int]]:
    """Normalize a category-to-counts aggregate without retaining element details."""

    if not isinstance(value, Mapping):
        return {}
    aggregate = {
        str(raw_kind or "").strip().lower(): _sanitize_count_mapping(raw_counts)
        for raw_kind, raw_counts in value.items()
        if _SAFE_STAGE_RE.fullmatch(str(raw_kind or "").strip().lower())
    }
    return dict(sorted((kind, counts) for kind, counts in aggregate.items() if counts))


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
        result[aggregate_key] = _sanitize_aggregate_mapping(metrics.get(aggregate_key))
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


def _prune(directory: Path, *, retention_hours: int, max_files: int, now: float) -> int:
    """Remove expired or excess records and return the aggregate removal count."""

    cutoff = now - retention_hours * 3600
    retained: list[tuple[float, Path]] = []
    removed = 0
    for path in _diagnostic_files(directory):
        try:
            modified = path.stat().st_mtime
            if modified < cutoff:
                path.unlink(missing_ok=True)
                removed += 1
            else:
                retained.append((modified, path))
        except OSError:
            continue
    retained.sort(key=lambda item: item[0])
    for _, path in retained[: max(0, len(retained) - max_files)]:
        try:
            path.unlink(missing_ok=True)
            removed += 1
        except OSError:
            continue
    return removed


def cleanup_custom_profile_diagnostics() -> int:
    """Prune configured records independently of future rendering failures."""

    try:
        from src.settings import settings

        directory = settings.drawing.custom_profile_diagnostic_dir
        if directory is None or not directory.is_dir():
            return 0
        with _write_lock:
            return _prune(
                directory,
                retention_hours=int(settings.drawing.custom_profile_diagnostic_retention_hours),
                max_files=int(settings.drawing.custom_profile_diagnostic_max_files),
                now=time.time(),
            )
    except Exception as exc:
        logger.warning("custom profile diagnostic cleanup failed: error_type=%s", type(exc).__name__)
        return 0


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
