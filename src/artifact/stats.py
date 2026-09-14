"""Artifact counters exposed as `/render-stats["artifacts"]` (plan §8.7), plus `artifact_node_name()`.

Every degradation branch of the artifact path increments a named counter here (invariant I3). One
`threading.Lock` guards all state (free-threaded build). With storage disabled every counter reads zero.
"""

from __future__ import annotations

from collections import Counter
import socket
import threading
import time
from typing import Any

SIMPLE_COUNTERS: tuple[str, ...] = (
    "requests_with_directive",
    "bytes_no_directive",
    "store_skipped",
    "published",
    "reused",
    "reused_foreign",
    "uploads",
    "upload_bytes",
    "upload_failures",
    "upload_timeouts",
    "index_lookups",
    "index_lookup_hits",
    "index_lookup_errors",
    "index_writes",
    "index_write_failures",
)
INDEX_SKIP_REASONS: tuple[str, ...] = ("disabled", "schema_missing", "unavailable")
DEGRADED_REASONS: tuple[str, ...] = (
    "disabled",
    "runtime_unavailable",
    "upload_failed",
    "upload_timeout",
    "unsupported_media",
    "internal",
)
STAGES: tuple[str, ...] = ("hash", "index_lookup", "upload", "index_write")

_node_name_lock = threading.Lock()
_node_name: str | None = None


def artifact_node_name() -> str:
    """settings.storage.node_name or socket.gethostname(); computed once, cached in a module global."""
    global _node_name
    cached = _node_name
    if cached is not None:
        return cached
    with _node_name_lock:
        if _node_name is None:
            from src.settings import settings

            configured = (settings.storage.node_name or "").strip()
            if not configured:
                try:
                    configured = socket.gethostname()
                except OSError:
                    configured = ""
            _node_name = configured or "unknown"
        return _node_name


def reset_artifact_node_name() -> None:
    """Forget the cached node name (tests)."""
    global _node_name
    with _node_name_lock:
        _node_name = None


def _error_record(stage: str, exc: BaseException | str) -> dict[str, Any]:
    text = exc if isinstance(exc, str) else f"{type(exc).__name__}: {exc}"
    return {"ts": time.time(), "stage": stage, "exc": text[:300]}


class ArtifactStats:
    """Process-wide artifact counters. Thread-safe; only `incr` on an unknown counter name raises (a code bug)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._counters: Counter[str] = Counter()
        self._upload_elapsed_total = 0.0
        self._index_skipped: Counter[str] = Counter()
        self._degraded: Counter[str] = Counter()
        self._directive_rejected: Counter[str] = Counter()
        self._stage_counts: Counter[str] = Counter()
        self._stage_totals: dict[str, float] = {}
        self._last_error: dict[str, Any] | None = None
        self._enabled = False
        self._bucket: str | None = None
        self._index_configured = False
        self._index_usable = False
        self._index_last_error: dict[str, Any] | None = None

    def incr(self, name: str, amount: int = 1) -> None:
        if name not in SIMPLE_COUNTERS:
            raise KeyError(name)
        with self._lock:
            self._counters[name] += amount

    def add_upload_elapsed(self, seconds: float) -> None:
        with self._lock:
            self._upload_elapsed_total += max(0.0, float(seconds))

    def index_skipped(self, reason: str) -> None:
        with self._lock:
            self._index_skipped[reason] += 1

    def degraded(self, reason: str) -> None:
        with self._lock:
            self._degraded[reason] += 1

    def directive_rejected(self, header: str) -> None:
        with self._lock:
            self._directive_rejected[header] += 1

    def record_stage(self, stage: str, elapsed: float) -> None:
        with self._lock:
            self._stage_counts[stage] += 1
            self._stage_totals[stage] = self._stage_totals.get(stage, 0.0) + max(0.0, float(elapsed))

    def record_error(self, stage: str, exc: BaseException | str) -> None:
        with self._lock:
            self._last_error = _error_record(stage, exc)

    def set_runtime_state(self, *, enabled: bool, bucket: str | None = None) -> None:
        with self._lock:
            self._enabled = bool(enabled)
            self._bucket = bucket

    def set_index_state(
        self,
        *,
        configured: bool,
        usable: bool,
        error: tuple[str, BaseException | str] | None = None,
    ) -> None:
        with self._lock:
            self._index_configured = bool(configured)
            self._index_usable = bool(usable)
            if error is not None:
                self._index_last_error = _error_record(*error)

    def _bucket_name(self) -> str:
        if self._bucket is not None:
            return self._bucket
        from src.settings import settings

        return settings.storage.provider.bucket

    def snapshot(self) -> dict[str, Any]:
        node_name = artifact_node_name()
        with self._lock:
            payload: dict[str, Any] = {
                "enabled": self._enabled,
                "node_name": node_name,
                "bucket": self._bucket_name(),
                "index": {
                    "configured": self._index_configured,
                    "usable": self._index_usable,
                    "last_error": dict(self._index_last_error) if self._index_last_error else None,
                },
            }
            for name in SIMPLE_COUNTERS:
                payload[name] = self._counters.get(name, 0)
            payload["upload_elapsed_total"] = round(self._upload_elapsed_total, 6)
            payload["index_skipped"] = {reason: self._index_skipped.get(reason, 0) for reason in INDEX_SKIP_REASONS}
            payload["degraded"] = {reason: self._degraded.get(reason, 0) for reason in DEGRADED_REASONS}
            payload["directive_rejected"] = dict(sorted(self._directive_rejected.items()))
            stage_names = list(STAGES) + sorted(set(self._stage_counts) - set(STAGES))
            payload["stages"] = {
                stage: {
                    "count": self._stage_counts.get(stage, 0),
                    "total": round(self._stage_totals.get(stage, 0.0), 6),
                }
                for stage in stage_names
            }
            payload["last_error"] = dict(self._last_error) if self._last_error else None
        return payload

    def reset(self) -> None:
        with self._lock:
            self._counters.clear()
            self._upload_elapsed_total = 0.0
            self._index_skipped.clear()
            self._degraded.clear()
            self._directive_rejected.clear()
            self._stage_counts.clear()
            self._stage_totals.clear()
            self._last_error = None
            self._enabled = False
            self._bucket = None
            self._index_configured = False
            self._index_usable = False
            self._index_last_error = None


artifact_stats = ArtifactStats()


def get_artifact_stats() -> dict[str, Any]:
    return artifact_stats.snapshot()


def reset_artifact_stats() -> None:
    artifact_stats.reset()
