"""Request-scoped telemetry for Pillow work performed before a native render.

The Skia path still uses Pillow at a few boundaries (layout metrics, image probes, and
runtime ``PIL.Image`` rasters).  A backend label of ``skia`` therefore does not by itself
mean that the request was Pillow-free.  This module records those touches in a mutable
request scope that is safe to share with copied ``contextvars`` across worker threads.

The scope deliberately stores counters behind an explicit lock.  CPython 3.14t does not
provide a GIL-based safety net, and a single request can probe many assets concurrently.
"""

from __future__ import annotations

import contextvars
from dataclasses import dataclass, field
import threading
from typing import Literal

PILLOW_TOUCH_IRPAINTER_PIL_IMAGE = "irpainter_pil_image"
PILLOW_TOUCH_IRPAINTER_MEM_RASTER = "irpainter_pil_mem_raster"
PILLOW_TOUCH_CUSTOM_PROFILE_MEM_RASTER = "custom_profile_pil_mem_raster"
PILLOW_TOUCH_TEXT_METRIC = "pillow_text_metric"
PILLOW_TOUCH_IMAGE_HEADER_PROBE = "pillow_image_header_probe"
PILLOW_TOUCH_IMAGE_DECODE = "pillow_image_decode"
PILLOW_TOUCH_PLACEHOLDER = "pillow_placeholder"

KNOWN_PILLOW_TOUCH_REASONS: tuple[str, ...] = (
    PILLOW_TOUCH_IRPAINTER_PIL_IMAGE,
    PILLOW_TOUCH_IRPAINTER_MEM_RASTER,
    PILLOW_TOUCH_CUSTOM_PROFILE_MEM_RASTER,
    PILLOW_TOUCH_TEXT_METRIC,
    PILLOW_TOUCH_IMAGE_HEADER_PROBE,
    PILLOW_TOUCH_IMAGE_DECODE,
    PILLOW_TOUCH_PLACEHOLDER,
)

NativePurity = Literal["pure", "hybrid", "unclassified"]


@dataclass(frozen=True, slots=True)
class PillowTouchSnapshot:
    """Pillow touches since the previous snapshot in one request scope."""

    scoped: bool
    counts: dict[str, int]

    @property
    def native_purity(self) -> NativePurity:
        if not self.scoped:
            return "unclassified"
        return "hybrid" if self.counts else "pure"

    @classmethod
    def unclassified(cls) -> PillowTouchSnapshot:
        return cls(scoped=False, counts={})

    @classmethod
    def from_counts(cls, counts: dict[str, int]) -> PillowTouchSnapshot:
        cleaned: dict[str, int] = {}
        for reason, count in counts.items():
            name = str(reason).strip()
            try:
                value = int(count)
            except (TypeError, ValueError):
                continue
            if name and value > 0:
                cleaned[name] = cleaned.get(name, 0) + value
        return cls(scoped=True, counts=cleaned)


@dataclass(slots=True)
class _PillowTouchScope:
    _lock: threading.Lock = field(default_factory=threading.Lock)
    _pending: dict[str, int] = field(default_factory=dict)
    _last_snapshot: PillowTouchSnapshot = field(default_factory=PillowTouchSnapshot.unclassified)

    def record(self, reason: str, count: int) -> None:
        with self._lock:
            self._pending[reason] = self._pending.get(reason, 0) + count

    def take_snapshot(self) -> PillowTouchSnapshot:
        with self._lock:
            snapshot = PillowTouchSnapshot(scoped=True, counts=dict(self._pending))
            self._pending.clear()
            self._last_snapshot = snapshot
            return snapshot

    def last_snapshot(self) -> PillowTouchSnapshot:
        with self._lock:
            return PillowTouchSnapshot(
                scoped=self._last_snapshot.scoped,
                counts=dict(self._last_snapshot.counts),
            )


_scope_var: contextvars.ContextVar[_PillowTouchScope | None] = contextvars.ContextVar(
    "drawing_pillow_touch_scope",
    default=None,
)


def begin_pillow_touch_scope() -> contextvars.Token[_PillowTouchScope | None]:
    """Start a fresh request scope and return the token needed to restore its parent."""

    return _scope_var.set(_PillowTouchScope())


def end_pillow_touch_scope(token: contextvars.Token[_PillowTouchScope | None]) -> None:
    """Restore the scope active before :func:`begin_pillow_touch_scope`."""

    _scope_var.reset(token)


def record_pillow_touch(reason: str, count: int = 1) -> None:
    """Record a Pillow dependency without ever affecting request correctness."""

    scope = _scope_var.get()
    if scope is None:
        return
    name = str(reason or "").strip()
    try:
        value = int(count)
    except (TypeError, ValueError):
        return
    if not name or value <= 0:
        return
    scope.record(name, value)


def take_pillow_touch_snapshot() -> PillowTouchSnapshot:
    """Take and clear touches accumulated since the previous render record."""

    scope = _scope_var.get()
    if scope is None:
        return PillowTouchSnapshot.unclassified()
    return scope.take_snapshot()


def get_last_pillow_touch_snapshot() -> PillowTouchSnapshot:
    """Return the most recently consumed snapshot, for cross-process payload handoff."""

    scope = _scope_var.get()
    if scope is None:
        return PillowTouchSnapshot.unclassified()
    return scope.last_snapshot()
