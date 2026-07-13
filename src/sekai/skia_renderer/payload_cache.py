"""Process-wide TTL + LRU cache for rendered Skia payloads (the encoded final image).

Lives in its own module (it used to sit in ``card_common``) so that the generic render path
and ``src.sekai.base.utils`` can report/clear it without importing the card layout helpers.
``card_common`` re-exports the accessors, so existing callers keep working unchanged.
"""

from __future__ import annotations

from collections import OrderedDict
import threading
import time
from typing import Any

from src.settings import (
    COMPOSED_IMAGE_CACHE_MAX_BYTES,
    COMPOSED_IMAGE_CACHE_SIZE,
    COMPOSED_IMAGE_CACHE_TTL_SECONDS,
)


class _SkiaPayloadCache:
    """The Skia render path otherwise re-renders on every request; this mirrors the role of
    the Pillow composed-image cache so repeated identical requests are served instantly.

    Stores opaque payloads keyed by a stable request key; eviction is by entry count, total
    bytes and TTL. Thread-safe (the render runs in a thread pool).
    """

    def __init__(self, max_size: int, max_bytes: int, ttl_seconds: int) -> None:
        self._max_size = max_size
        self._max_bytes = max_bytes
        self._ttl = ttl_seconds
        self._lock = threading.RLock()
        self._cache: OrderedDict[str, tuple[Any, int, float]] = OrderedDict()
        self._total_bytes = 0
        self._hits = 0
        self._misses = 0
        self._sets = 0
        self._evictions = 0
        self._expired = 0

    def _enabled(self) -> bool:
        return self._max_size > 0 and self._max_bytes > 0 and self._ttl > 0

    def _drop(self, key: str, entry: tuple[Any, int, float]) -> None:
        self._cache.pop(key, None)
        self._total_bytes -= entry[1]

    def get(self, key: str) -> Any | None:
        if not self._enabled():
            return None
        now = time.monotonic()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                return None
            if now >= entry[2]:
                self._expired += 1
                self._misses += 1
                self._drop(key, entry)
                return None
            self._hits += 1
            self._cache.move_to_end(key)
            return entry[0]

    def set(self, key: str, payload: Any, nbytes: int) -> None:
        if not self._enabled() or nbytes > self._max_bytes:
            return
        now = time.monotonic()
        with self._lock:
            old = self._cache.get(key)
            if old is not None:
                self._drop(key, old)
            self._cache[key] = (payload, nbytes, now + self._ttl)
            self._total_bytes += nbytes
            self._sets += 1
            while self._cache and (len(self._cache) > self._max_size or self._total_bytes > self._max_bytes):
                _, evicted = self._cache.popitem(last=False)
                self._total_bytes -= evicted[1]
                self._evictions += 1

    def stats(self) -> dict[str, Any]:
        with self._lock:
            total_queries = self._hits + self._misses
            return {
                "enabled": self._enabled(),
                "entries": len(self._cache),
                "max_entries": self._max_size,
                "bytes": self._total_bytes,
                "max_bytes": self._max_bytes,
                "ttl_seconds": self._ttl,
                "hits": self._hits,
                "misses": self._misses,
                "sets": self._sets,
                "evictions": self._evictions,
                "expired": self._expired,
                "hit_rate": (self._hits / total_queries) if total_queries > 0 else None,
            }

    def clear(self) -> None:
        with self._lock:
            self._cache.clear()
            self._total_bytes = 0
            self._hits = 0
            self._misses = 0
            self._sets = 0
            self._evictions = 0
            self._expired = 0


_skia_payload_cache = _SkiaPayloadCache(
    COMPOSED_IMAGE_CACHE_SIZE,
    COMPOSED_IMAGE_CACHE_MAX_BYTES,
    COMPOSED_IMAGE_CACHE_TTL_SECONDS,
)


def get_skia_payload_cached(key: str) -> Any | None:
    return _skia_payload_cache.get(key)


def put_skia_payload_cache(key: str, payload: Any, nbytes: int) -> None:
    _skia_payload_cache.set(key, payload, nbytes)


def get_skia_payload_cache_stats() -> dict[str, Any]:
    return _skia_payload_cache.stats()


def clear_skia_payload_cache() -> None:
    _skia_payload_cache.clear()
