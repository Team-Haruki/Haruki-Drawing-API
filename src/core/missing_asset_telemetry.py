"""Missing-asset counters: process totals by reason plus a request-scoped count (plan §5.3, inventory H1).

Most `on_missing="raise"` call sites catch the error and substitute a fallback image, so a missing or
not-yet-mirrored asset degrades silently. Every occurrence (not once per key) is counted here.

The functions live in `src.core` rather than `src.sekai.base.utils` because `push_request_context`
(`src.core.debug`) opens the request scope and `utils` already imports `debug`; `utils` re-exports them
under the names of plan §5.2. Heavy-worker misses stay in the child process's counters (documented limit).
"""

from __future__ import annotations

from collections import Counter
import contextvars
from dataclasses import dataclass, field
import threading
from typing import Any

MISSING_EMPTY_PATH = "empty_path"
MISSING_LOCAL_NOT_FOUND = "local_not_found"
MISSING_MIRROR_NOT_FOUND = "mirror_not_found"
MISSING_MIRROR_FETCH_ERROR = "mirror_fetch_error"
MISSING_MIRROR_BREAKER_OPEN = "mirror_breaker_open"
MISSING_CANDIDATES_EXHAUSTED = "candidates_exhausted"
MISSING_BIRTHDAY_FALLBACK = "birthday_fallback"
MISSING_VANISHED = "vanished"

MISSING_ASSET_REASONS: tuple[str, ...] = (
    MISSING_EMPTY_PATH,
    MISSING_LOCAL_NOT_FOUND,
    MISSING_MIRROR_NOT_FOUND,
    MISSING_MIRROR_FETCH_ERROR,
    MISSING_MIRROR_BREAKER_OPEN,
    MISSING_CANDIDATES_EXHAUSTED,
    MISSING_BIRTHDAY_FALLBACK,
    MISSING_VANISHED,
)
_KNOWN = frozenset(MISSING_ASSET_REASONS)

_missing_asset_lock = threading.Lock()
_missing_asset_counts: Counter[str] = Counter()


@dataclass(slots=True)
class _MissingAssetScope:
    _lock: threading.Lock = field(default_factory=threading.Lock)
    count: int = 0

    def add(self) -> None:
        with self._lock:
            self.count += 1

    def read(self) -> int:
        with self._lock:
            return self.count


@dataclass(frozen=True, slots=True)
class MissingAssetScopeToken:
    token: contextvars.Token[_MissingAssetScope | None]
    scope: _MissingAssetScope


_scope_var: contextvars.ContextVar[_MissingAssetScope | None] = contextvars.ContextVar(
    "drawing_missing_asset_scope",
    default=None,
)


def record_missing_asset(reason: str) -> None:
    """Count one missing-asset occurrence; an unknown reason is ignored (telemetry never breaks a render)."""
    if reason not in _KNOWN:
        return
    with _missing_asset_lock:
        _missing_asset_counts[reason] += 1
    scope = _scope_var.get()
    if scope is not None:
        scope.add()


def begin_missing_asset_scope() -> MissingAssetScopeToken:
    """Open a fresh request scope (shared by copied contexts on pool threads)."""
    scope = _MissingAssetScope()
    return MissingAssetScopeToken(token=_scope_var.set(scope), scope=scope)


def current_missing_asset_count() -> int:
    """Missing assets recorded in the current request scope (0 outside a request)."""
    scope = _scope_var.get()
    return 0 if scope is None else scope.read()


def end_missing_asset_scope(token: MissingAssetScopeToken) -> int:
    """Restore the parent scope; returns the count of the scope being closed."""
    count = token.scope.read()
    _scope_var.reset(token.token)
    return count


def get_missing_asset_stats() -> dict[str, Any]:
    """`{"total": n, "by_reason": {reason: n}}` with every known reason present."""
    with _missing_asset_lock:
        by_reason = {reason: _missing_asset_counts.get(reason, 0) for reason in MISSING_ASSET_REASONS}
    return {"total": sum(by_reason.values()), "by_reason": by_reason}


def reset_missing_asset_stats() -> None:
    """Zero the process counters (tests and controlled soak restarts)."""
    with _missing_asset_lock:
        _missing_asset_counts.clear()
