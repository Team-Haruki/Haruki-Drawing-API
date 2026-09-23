"""On-demand reader for Cloud-written user uploads (profile backgrounds) in the `user-upload` bucket.

Cloud's `ProfileBGStore` persists `bg_settings.img_path` as `user_upload/profile_bg/<server>/uid_<id>_<hex>.jpg`
and writes the object under exactly that key (bucket `user-upload`, no root). Today the same file also lives at
`<assets.base_dir>/user_upload/profile_bg/...`, which is where Drawing has always read it from.

Unlike the asset mirror, nothing here materialises files: the profile drawer runs on the event loop, so the
read is awaited directly on the caller's loop (opendal's `AsyncOperator` is loop-agnostic) and the encoded
bytes travel to the renderer as an `EncodedImageRef`. A bounded TTL cache keyed by object key skips the
round trip for a repeated render; Cloud names every upload uniquely, so a key's bytes never change.

Degradation order for one key: cache -> circuit breaker (open: skip the bucket) -> single-flight (one read per
key per loop) -> bounded read (`fetch_timeout_seconds` is a real wall-clock `wait_for` over HEAD + GET and
retries) -> `None` on any failure. The caller then takes its fallback path (local file, default background).

Nothing here touches the network at import time, and `get_user_upload_store()` returns a disabled store
(no operator, never imports `opendal`) unless `assets.user_upload.enabled` is set.
"""

from __future__ import annotations

import asyncio
from collections import OrderedDict
from collections.abc import Callable
import logging
import re
import threading
import time
from typing import TYPE_CHECKING, Any

from src.storage.protocols import StorageError, StorageNotFound, validate_object_key

if TYPE_CHECKING:  # pragma: no cover
    from src.settings import Settings, UserUploadSettings
    from src.storage.protocols import ObjectStore

logger = logging.getLogger("src.assets.user_upload")

PROFILE_BG_NAMESPACE = ("user_upload", "profile_bg")
# Cloud's ProfileBGStore writes `uid_<userID>_<8 hex>.jpg`; rows from before April 2026 are
# `binding_<id>.jpg` / `binding_<id>_<8 hex>.jpg`. Nothing else has ever been written under this prefix.
_PROFILE_BG_SERVER = re.compile(r"[a-z]{2,4}\Z")
_PROFILE_BG_FILENAME = re.compile(r"(?:uid_[A-Za-z0-9]{1,64}|binding_[0-9]{1,20})(?:_[0-9a-f]{8})?\.jpg\Z")


def profile_bg_object_key(img_path: str | None) -> str | None:
    """Map `bg_settings.img_path` to its `user-upload` object key; `None` when it is not a user upload.

    Cloud sends the persisted relative path verbatim (`user_upload/profile_bg/<server>/<file>`). Also accepted:
    a leading `/` or `./`, backslashes, surrounding whitespace, and an absolute host path that contains the
    `user_upload/profile_bg/` segments (the file's location on the shared asset disk). A path outside that
    namespace — an ordinary asset path, another `user_upload/` area — is `None` so the caller keeps its local read.

    Raises `ValueError` for traversal (`..` anywhere), NUL bytes, or a namespace path that is not exactly
    `user_upload/profile_bg/<server>/<file>` with a filename Cloud's writer produces.
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
    if (
        len(key_parts) != 4
        or not _PROFILE_BG_SERVER.match(key_parts[2])
        or not _PROFILE_BG_FILENAME.match(key_parts[3])
    ):
        raise ValueError(f"profile background path is malformed: {img_path!r}")
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

    `fetch(key)` never raises for a remote problem: a missing object, a transport failure, a timeout, an
    oversized object or an open breaker logs (a WARNING, the breaker skip at DEBUG) and returns `None`, and the
    caller takes its fallback path.
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
        self._clock = clock
        self.disabled_reason = disabled_reason if store is None else ""
        self.closed = False
        self._cache = _TTLBytesCache(
            settings.cache_size, settings.cache_max_mb * 1024 * 1024, settings.cache_ttl_seconds, clock
        )
        # Single-flight: one read per (loop, key). Keyed by loop because Granian runs lifespan and requests on
        # different loops and a Future cannot be awaited from another loop.
        self._inflight: dict[tuple[int, str], asyncio.Future[bytes | None]] = {}
        self._inflight_lock = threading.Lock()
        # Breaker (same shape as the asset mirror's): N consecutive failures open it for `breaker_open_seconds`;
        # the first fetch after that window is the single half-open probe.
        self._breaker_lock = threading.Lock()
        self._consecutive_failures = 0
        self._open_until: float | None = None
        self._probe_in_flight = False
        self.fetches = 0
        self.not_found = 0
        self.errors = 0
        self.single_flight_waits = 0
        self.breaker_trips = 0
        self.breaker_skips = 0

    @property
    def enabled(self) -> bool:
        return self._store is not None and not self.closed

    @property
    def local_fallback(self) -> bool:
        return self._settings.local_fallback

    @property
    def bucket(self) -> str:
        return self._store.bucket if self._store is not None else ""

    @property
    def breaker_open(self) -> bool:
        with self._breaker_lock:
            return self._open_until is not None

    # ------------------------------------------------------------------ breaker
    def _breaker_admit(self) -> str | None:
        """`"closed"` (read), `"probe"` (the single half-open read) or `None` (skip the bucket)."""
        with self._breaker_lock:
            if self._open_until is None:
                return "closed"
            if self._clock() < self._open_until or self._probe_in_flight:
                return None
            self._probe_in_flight = True
            return "probe"

    def _breaker_success(self) -> None:
        with self._breaker_lock:
            was_open = self._open_until is not None
            self._consecutive_failures = 0
            self._open_until = None
            self._probe_in_flight = False
        if was_open:
            logger.info("user_upload.breaker_closed bucket=%s", self.bucket)

    def _breaker_failure(self, probe: bool) -> None:
        threshold = max(1, int(self._settings.breaker_failures))
        with self._breaker_lock:
            self._consecutive_failures += 1
            tripped = probe or (self._open_until is None and self._consecutive_failures >= threshold)
            if tripped:
                self._open_until = self._clock() + max(0.0, float(self._settings.breaker_open_seconds))
            self._probe_in_flight = False
            failures = self._consecutive_failures
        if tripped:
            self.breaker_trips += 1
            logger.warning(
                "user_upload.breaker_open bucket=%s failures=%s open_seconds=%s (profile backgrounds fall back)",
                self.bucket,
                failures,
                self._settings.breaker_open_seconds,
            )

    # ------------------------------------------------------------------ read
    async def fetch(self, key: str) -> bytes | None:
        """The object's bytes, or `None` after a log line. Must run on a running event loop."""
        if self._store is None or self.closed:
            return None
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        loop = asyncio.get_running_loop()
        flight = (id(loop), key)
        with self._inflight_lock:
            future = self._inflight.get(flight)
            if future is None:
                future = loop.create_future()
                self._inflight[flight] = future
                leader = True
            else:
                leader = False
        if not leader:
            self.single_flight_waits += 1
            return await asyncio.shield(future)
        try:
            result = await self._read(key)
        except BaseException as exc:  # cancellation included: waiters must not hang on a dead leader
            if not future.done():
                future.set_exception(exc)
            raise
        else:
            if not future.done():
                future.set_result(result)
            return result
        finally:
            with self._inflight_lock:
                if self._inflight.get(flight) is future:
                    del self._inflight[flight]

    async def _read(self, key: str) -> bytes | None:
        store = self._store
        if store is None:
            return None
        admission = self._breaker_admit()
        if admission is None:
            self.breaker_skips += 1
            logger.debug("user_upload.breaker_skip bucket=%s key=%s", store.bucket, key)
            return None
        probe = admission == "probe"
        self.fetches += 1
        budget = max(0.1, float(self._settings.fetch_timeout_seconds))  # the whole read: HEAD + GET + retries
        try:
            data, _ = await asyncio.wait_for(store.read(key, max_bytes=self._settings.fetch_max_bytes), budget)
        except StorageNotFound:
            self._breaker_success()  # the bucket answered; a missing object is not an outage
            self.not_found += 1
            logger.warning("user_upload.not_found bucket=%s key=%s", store.bucket, key)
            return None
        except (StorageError, TimeoutError, OSError) as exc:
            self._breaker_failure(probe)
            self.errors += 1
            logger.warning(
                "user_upload.read_failed bucket=%s key=%s exc=%s: %s", store.bucket, key, type(exc).__name__, exc
            )
            return None
        self._breaker_success()
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
            "single_flight_waits": self.single_flight_waits,
            "breaker_open": self.breaker_open,
            "breaker_trips": self.breaker_trips,
            "breaker_skips": self.breaker_skips,
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
    total = max(0.1, float(settings.fetch_timeout_seconds))
    per_op = min(total, max(0.1, float(settings.fetch_io_timeout_seconds)))
    return OpendalObjectStore.from_provider(
        settings.provider,
        region=None,
        timeout=per_op,  # opendal's layer bounds ONE operation (HEAD or GET); `fetch` bounds the whole read
        io_timeout=per_op,
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
    provider = config.provider
    if provider.scheme != "fs" and (provider.root.strip() or (provider.prefix or "").strip()):
        # The settings validator drops an s3 root; a value can only get here by assignment after construction.
        reason = "provider root must stay empty: object keys already start with user_upload/"
        logger.error("user_upload.disabled reason=%s provider=%s", reason, provider.describe())
        return UserUploadStore(settings=config, store=None, disabled_reason=reason, clock=clock)
    try:
        store = (store_factory or _default_store_factory)(config)
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        logger.error(
            "user_upload.disabled reason=%s provider=%s (profile backgrounds stay on local disk)",
            reason,
            provider.describe(),
        )
        return UserUploadStore(settings=config, store=None, disabled_reason=reason, clock=clock)
    logger.info("user_upload.enabled provider=%s", provider.describe())
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
