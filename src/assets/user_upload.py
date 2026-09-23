"""On-demand reader for Cloud-written user uploads (profile backgrounds) in the `user-upload` bucket.

Cloud's `ProfileBGStore` persists `bg_settings.img_path` as `user_upload/profile_bg/<server>/uid_<id>_<hex>.jpg`
and writes the object under exactly that key (bucket `user-upload`, no root). Today the same file also lives at
`<assets.base_dir>/user_upload/profile_bg/...`, which is where Drawing has always read it from.

Unlike the asset mirror, nothing here materialises files: the profile drawer runs on the event loop, so the
read is awaited directly on the caller's loop (opendal's `AsyncOperator` is loop-agnostic) and the encoded
bytes travel to the renderer as an `EncodedImageRef`. A bounded TTL cache keyed by object key skips the
round trip for a repeated render; Cloud names every upload uniquely, so a key's bytes never change.

Nothing here touches the network at import time, and `get_user_upload_store()` returns a disabled store
(no operator, never imports `opendal`) unless `assets.user_upload.enabled` is set.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable
import logging
import threading
import time
from typing import TYPE_CHECKING, Any

from src.storage.protocols import StorageError, StorageNotFound, validate_object_key

if TYPE_CHECKING:  # pragma: no cover
    from src.settings import Settings, UserUploadSettings
    from src.storage.protocols import ObjectStore

logger = logging.getLogger("src.assets.user_upload")

PROFILE_BG_NAMESPACE = ("user_upload", "profile_bg")


def profile_bg_object_key(img_path: str | None) -> str | None:
    """Map `bg_settings.img_path` to its `user-upload` object key; `None` when it is not a user upload.

    Cloud sends the persisted relative path verbatim (`user_upload/profile_bg/<server>/<file>`). Also accepted:
    a leading `/` or `./`, backslashes, surrounding whitespace, and an absolute host path that contains the
    `user_upload/profile_bg/` segments (the file's location on the shared asset disk). Anything else — an
    ordinary asset path, another `user_upload/` namespace — is `None` so the caller keeps its local read.

    Raises `ValueError` for traversal (`..` anywhere), NUL bytes, or a namespace path without a file segment.
    """
    if not img_path or not img_path.strip():
        return None
    raw = img_path.strip().replace("\\", "/")
    if "\x00" in raw:
        raise ValueError("profile background path contains NUL")
    segments = raw.split("/")
    if ".." in segments:
        raise ValueError(f"profile background path escapes its root: {img_path!r}")
    parts = [part for part in segments if part not in ("", ".")]
    try:
        start = parts.index(PROFILE_BG_NAMESPACE[0])
    except ValueError:
        return None
    key_parts = parts[start:]
    if len(key_parts) < 2 or key_parts[1] != PROFILE_BG_NAMESPACE[1]:
        return None
    if len(key_parts) < 4:
        raise ValueError(f"profile background path has no file segment: {img_path!r}")
    return validate_object_key("/".join(key_parts))


class _TTLBytesCache:
    """Bounded LRU of encoded bytes with a TTL; one lock (free-threaded build). Size/bytes/TTL `0` disables."""

    def __init__(self, max_size: int, max_bytes: int, ttl_seconds: float, clock: Callable[[], float]) -> None:
        self._max_size = max_size
        self._max_bytes = max_bytes
        self._ttl = ttl_seconds
        self._clock = clock
        self._lock = threading.Lock()
        self._entries: OrderedDict[str, tuple[bytes, float]] = OrderedDict()
        self._total_bytes = 0
        self.hits = 0
        self.misses = 0

    def _enabled(self) -> bool:
        return self._max_size > 0 and self._max_bytes > 0 and self._ttl > 0

    def get(self, key: str) -> bytes | None:
        if not self._enabled():
            return None
        now = self._clock()
        with self._lock:
            entry = self._entries.get(key)
            if entry is None:
                self.misses += 1
                return None
            data, expires_at = entry
            if now >= expires_at:
                self._entries.pop(key, None)
                self._total_bytes -= len(data)
                self.misses += 1
                return None
            self._entries.move_to_end(key)
            self.hits += 1
            return data

    def set(self, key: str, data: bytes) -> None:
        if not self._enabled() or len(data) > self._max_bytes:
            return
        with self._lock:
            old = self._entries.pop(key, None)
            if old is not None:
                self._total_bytes -= len(old[0])
            self._entries[key] = (data, self._clock() + self._ttl)
            self._total_bytes += len(data)
            while self._entries and (len(self._entries) > self._max_size or self._total_bytes > self._max_bytes):
                _, (evicted, _) = self._entries.popitem(last=False)
                self._total_bytes -= len(evicted)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()
            self._total_bytes = 0

    def snapshot(self) -> dict[str, int]:
        with self._lock:
            return {
                "entries": len(self._entries),
                "bytes": self._total_bytes,
                "hits": self.hits,
                "misses": self.misses,
            }


class UserUploadStore:
    """Reads profile backgrounds by object key. `enabled` is False for the disabled/failed store.

    `fetch(key)` never raises for a remote problem: a missing object, a transport failure, a timeout or an
    oversized object logs a WARNING and returns `None`, and the caller takes its fallback path.
    """

    def __init__(
        self,
        *,
        settings: UserUploadSettings,
        store: ObjectStore | None,
        disabled_reason: str = "",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._settings = settings
        self._store = store
        self.disabled_reason = disabled_reason if store is None else ""
        self.closed = False
        self._cache = _TTLBytesCache(
            settings.cache_size, settings.cache_max_mb * 1024 * 1024, settings.cache_ttl_seconds, clock
        )
        self.fetches = 0
        self.not_found = 0
        self.errors = 0

    @property
    def enabled(self) -> bool:
        return self._store is not None and not self.closed

    @property
    def local_fallback(self) -> bool:
        return self._settings.local_fallback

    @property
    def bucket(self) -> str:
        return self._store.bucket if self._store is not None else ""

    async def fetch(self, key: str) -> bytes | None:
        """The object's bytes, or `None` after a WARNING. Must run on a running event loop."""
        store = self._store
        if store is None or self.closed:
            return None
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        self.fetches += 1
        budget = max(0.1, float(self._settings.fetch_timeout_seconds)) + 1.0  # hard cap over the layered timeout
        try:
            data, _ = await asyncio.wait_for(store.read(key, max_bytes=self._settings.fetch_max_bytes), budget)
        except StorageNotFound:
            self.not_found += 1
            logger.warning("user_upload.not_found bucket=%s key=%s", store.bucket, key)
            return None
        except (StorageError, TimeoutError, OSError) as exc:
            self.errors += 1
            logger.warning(
                "user_upload.read_failed bucket=%s key=%s exc=%s: %s", store.bucket, key, type(exc).__name__, exc
            )
            return None
        data = bytes(data)
        self._cache.set(key, data)
        return data

    def clear_cache(self) -> None:
        self._cache.clear()

    def stats_snapshot(self) -> dict[str, Any]:
        return {
            "enabled": self.enabled,
            "disabled_reason": self.disabled_reason,
            "bucket": self.bucket,
            "fetches": self.fetches,
            "not_found": self.not_found,
            "errors": self.errors,
            "cache": self._cache.snapshot(),
        }

    async def close(self) -> None:
        """Close the store. Idempotent, never raises."""
        if self.closed:
            return
        self.closed = True
        self._cache.clear()
        store, self._store = self._store, None
        if store is None:
            return
        try:
            await store.close()
        except Exception as exc:
            logger.warning("user_upload.close_failed exc=%s: %s", type(exc).__name__, exc)


def _default_store_factory(settings: UserUploadSettings) -> ObjectStore:
    from src.storage.opendal_store import OpendalObjectStore
    from src.storage.provider import opendal_kwargs

    opendal_kwargs(settings.provider, None)  # validate the provider block (e.g. missing bucket) without I/O
    return OpendalObjectStore.from_provider(
        settings.provider,
        region=None,
        timeout=settings.fetch_timeout_seconds,
        io_timeout=settings.fetch_io_timeout_seconds,
        retries=settings.fetch_retries,
        concurrency=settings.fetch_concurrency,
        name="user-upload",
        max_read_bytes=settings.fetch_max_bytes,
    )


def build_user_upload_store(
    settings: Settings,
    *,
    store_factory: Callable[[UserUploadSettings], ObjectStore] | None = None,
    clock: Callable[[], float] = time.monotonic,
) -> UserUploadStore:
    """Build the store for `settings`; never raises (a failure yields a disabled store with a reason)."""
    config = settings.assets.user_upload
    if not config.enabled:
        return UserUploadStore(settings=config, store=None, disabled_reason="disabled", clock=clock)
    try:
        store = (store_factory or _default_store_factory)(config)
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        logger.error(
            "user_upload.disabled reason=%s provider=%s (profile backgrounds stay on local disk)",
            reason,
            config.provider.describe(),
        )
        return UserUploadStore(settings=config, store=None, disabled_reason=reason, clock=clock)
    if config.provider.root:
        logger.warning(
            "user_upload provider root=%r is ignored: Cloud's keys already start with user_upload/ "
            "(keep assets.user_upload.provider.root empty)",
            config.provider.root,
        )
    logger.info("user_upload.enabled provider=%s", config.provider.describe())
    return UserUploadStore(settings=config, store=store, clock=clock)


# ---------------------------------------------------------------------- process-wide accessor
_user_upload_store: UserUploadStore | None = None
_user_upload_store_lock = threading.Lock()


def get_user_upload_store() -> UserUploadStore:
    """Lazy process-wide store (a harness that never runs lifespan still gets a working, usually disabled, one)."""
    global _user_upload_store
    store = _user_upload_store
    if store is not None:
        return store
    with _user_upload_store_lock:
        if _user_upload_store is None:
            from src.settings import settings

            _user_upload_store = build_user_upload_store(settings)
        return _user_upload_store


def set_user_upload_store(store: UserUploadStore | None) -> None:
    """Install `store` (test seam / shutdown); `None` resets to lazy construction. Does not close the old one."""
    global _user_upload_store
    with _user_upload_store_lock:
        _user_upload_store = store


def start_user_upload_store() -> UserUploadStore:
    """Lifespan startup: build the process store. Never raises (fail-open to local disk)."""
    try:
        return get_user_upload_store()
    except Exception as exc:  # build is already fail-open; this guards a bug in the accessor itself
        logger.error(
            "user_upload.start_failed exc=%s: %s (profile backgrounds stay on local disk)", type(exc).__name__, exc
        )
        from src.settings import settings

        store = UserUploadStore(settings=settings.assets.user_upload, store=None, disabled_reason=str(exc))
        set_user_upload_store(store)
        return store


async def shutdown_user_upload_store() -> None:
    """Lifespan shutdown: close the installed store. Never builds one, never raises, idempotent.

    The closed store stays installed so a late render during shutdown falls back to local disk instead of
    lazily building a new operator.
    """
    with _user_upload_store_lock:
        store = _user_upload_store
    if store is None:
        return
    try:
        await store.close()
    except Exception as exc:  # close() already swallows; defensive
        logger.warning("user_upload.shutdown_failed exc=%s: %s", type(exc).__name__, exc)
