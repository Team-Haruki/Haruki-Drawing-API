"""Request-local asset revisions; concurrent generations never mutate each other's paths.

One ASGI process owns each mirror root. Managed heavy workers are protected by
the parent's request lease; independent ASGI processes must use separate roots.
"""

from __future__ import annotations

from collections.abc import Callable
import contextvars
from dataclasses import dataclass
from pathlib import Path
import threading

_revision: contextvars.ContextVar[str | None] = contextvars.ContextVar("asset_revision", default=None)
_lock = threading.Lock()
_active: dict[str, int] = {}
_latest = ""


@dataclass(frozen=True, slots=True)
class AssetRevisionToken:
    token: contextvars.Token[str | None]
    revision: str


def begin_asset_revision(revision: str) -> AssetRevisionToken:
    global _latest
    token = _revision.set(revision)
    with _lock:
        _active[revision] = _active.get(revision, 0) + 1
        if revision:
            _latest = revision
    return AssetRevisionToken(token, revision)


def end_asset_revision(token: AssetRevisionToken) -> None:
    _revision.reset(token.token)
    with _lock:
        count = _active[token.revision] - 1
        if count:
            _active[token.revision] = count
        else:
            del _active[token.revision]


def current_asset_revision() -> str:
    return _revision.get() or ""


def has_asset_revision_scope() -> bool:
    return _revision.get() is not None


def latest_asset_revision() -> str:
    with _lock:
        return _latest


def active_asset_revisions() -> set[str]:
    with _lock:
        return set(_active)


def retire_asset_revision(revision: str, source: Path, retired: Path) -> bool:
    """Atomically detach an unused namespace; slow deletion happens outside the lease lock."""
    with _lock:
        # An old client without a revision can still use the configured mirror version.
        if _active.get(revision) or _active.get(""):
            return False
        try:
            source.rename(retired)
        except OSError:
            return False
        return True


def evict_asset_file(revision: str, remove: Callable[[], bool]) -> bool:
    """Do not evict a lazy-decoded file between its resolution and native read."""
    with _lock:
        if _active.get(revision) or _active.get(""):
            return False
        return remove()
