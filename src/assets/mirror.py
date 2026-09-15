"""On-demand asset mirror: fetch a bucket object into a local, manifest-versioned directory (C3, E5).

`ensure_local` is SYNC because every caller (`_resolve_and_stat`) runs on a render-pool thread; the object
store is async, so the mirror owns a dedicated fetch-loop thread and submits with
`asyncio.run_coroutine_threadsafe`. It refuses outright to fetch from a thread that already runs an event
loop (invariant I2) and goes straight to the local fallback instead.

Degradation order after a miss of the mirrored file: loop-thread refusal -> negative memo (NotFound only,
TTL + version scoped, addendum C-9) -> single-flight wait -> circuit breaker -> remote read -> atomic
publish. Every failure branch ends in the local fallback (today's `<base>/asset/...` path) and a counter.

Nothing here touches the network or starts a thread at import time, and `get_asset_mirror()` returns a
`NullMirror` (zero overhead, no thread, no operator) unless `assets.source == "mirror"`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
import concurrent.futures
from dataclasses import dataclass, field, fields
import hashlib
import logging
import os
from pathlib import Path
from stat import S_ISREG
import sys
import threading
import time
from typing import TYPE_CHECKING, Any

from src.assets.keymap import AssetKeyMap, MappedAsset
from src.storage.protocols import StorageNotFound, StorageTooLarge

if TYPE_CHECKING:  # pragma: no cover
    from src.assets.version import ManifestVersionSource
    from src.settings import AssetMirrorSettings, Settings
    from src.storage.protocols import ObjectStore

logger = logging.getLogger("src.assets.mirror")

_WARNED_MAX = 4096
_TMP_DIR = ".tmp"

# Values `last_miss_reason()` can report; they match the missing-asset reasons of plan §5.3.
REASON_NOT_FOUND = "mirror_not_found"
REASON_FETCH_ERROR = "mirror_fetch_error"
REASON_BREAKER_OPEN = "mirror_breaker_open"


@dataclass
class MirrorStats:
    """Mirror counters; every mutation goes through one `threading.Lock` (free-threaded build)."""

    enabled: bool = False
    source: str = "local"
    disabled_reason: str = ""
    manifest_version: str = ""
    provider: str = ""
    bucket: str = ""
    local_hits: int = 0
    fetches: int = 0
    fetch_bytes: int = 0
    fetch_elapsed_total: float = 0.0
    remote_miss: int = 0
    fetch_errors: int = 0
    too_large: int = 0
    negative_memo_hits: int = 0
    local_fallback_hits: int = 0
    skipped_on_loop: int = 0
    single_flight_waits: int = 0
    breaker_open: int = 0
    breaker_trips: int = 0
    breaker_skips: int = 0
    version_changes: int = 0
    dir_entries: int = 0
    dir_bytes: int = 0
    sweeps: int = 0
    evicted_entries: int = 0
    evicted_bytes: int = 0
    versions_removed: int = 0
    _lock: threading.Lock = field(default_factory=threading.Lock, repr=False, compare=False)

    def incr(self, name: str, amount: float = 1) -> None:
        with self._lock:
            setattr(self, name, getattr(self, name) + amount)

    def set(self, **values: Any) -> None:
        with self._lock:
            for name, value in values.items():
                if name.startswith("_") or not hasattr(self, name):
                    raise AttributeError(f"unknown mirror stat: {name}")
                setattr(self, name, value)

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {f.name: getattr(self, f.name) for f in fields(self) if not f.name.startswith("_")}


def _stat_regular(path: Path) -> os.stat_result | None:
    try:
        st = os.stat(path)
    except (OSError, ValueError):
        return None
    return st if S_ISREG(st.st_mode) else None


def _on_running_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


class _OnceLogger:
    """Bounded once-per-key log gate (clear-when-full)."""

    def __init__(self) -> None:
        self._seen: set[str] = set()
        self._lock = threading.Lock()

    def first(self, key: str) -> bool:
        with self._lock:
            if key in self._seen:
                return False
            if len(self._seen) >= _WARNED_MAX:
                self._seen.clear()
            self._seen.add(key)
            return True

    def clear(self) -> None:
        with self._lock:
            self._seen.clear()


def _clear_resolution_caches() -> None:
    """Drop `src.sekai.base.utils`' resolved-path memo and asset-ref lru after a version change.

    Looked up in `sys.modules` instead of imported: when utils was never imported, those caches do not exist,
    and importing it here would invert the package layering (`assets` must not depend on `sekai`).
    """
    utils = sys.modules.get("src.sekai.base.utils")
    if utils is None:
        return
    clear_paths = getattr(utils, "clear_resolved_path_cache", None)
    if callable(clear_paths):
        clear_paths()
    else:
        cache = getattr(utils, "_resolved_path_cache", None)
        if cache is not None:
            cache.clear()
    ref_cache = getattr(utils, "_load_asset_image_ref_cached", None)
    if ref_cache is not None and hasattr(ref_cache, "cache_clear"):
        ref_cache.cache_clear()


class AssetMirror:
    """Fetch-on-miss mirror of the `pjsk-assets` bucket under `<base_dir>/<dir>/<version>/`."""

    def __init__(
        self,
        *,
        base_dir: Path,
        settings: AssetMirrorSettings,
        store_factory: Callable[[str], ObjectStore],
        version_source: ManifestVersionSource,
        stats: MirrorStats,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._base_dir = Path(base_dir)
        self._settings = settings
        self._store_factory = store_factory
        self._version_source = version_source
        self.stats = stats
        self._clock = clock
        self._map = AssetKeyMap(mirror_dir=settings.dir, version=version_source.current())
        self._mirror_root = self._base_dir / self._map.mirror_dir

        self._stores: dict[str, ObjectStore] = {}
        self._stores_lock = threading.Lock()

        self._loop: asyncio.AbstractEventLoop | None = None
        self._loop_thread: threading.Thread | None = None
        self._loop_lock = threading.Lock()
        self._closed = False

        self._negative: dict[tuple[str, str, str], float] = {}
        self._negative_lock = threading.Lock()

        self._inflight: dict[str, threading.Event] = {}
        self._inflight_lock = threading.Lock()
        self._fetch_slots = threading.BoundedSemaphore(max(1, int(settings.fetch_concurrency)))

        self._breaker_lock = threading.Lock()
        self._consecutive_failures = 0
        self._open_until: float | None = None
        self._probe_in_flight = False

        self._fallback_log = _OnceLogger()
        self._error_log = _OnceLogger()
        self._tls = threading.local()

        provider = settings.provider
        stats.set(
            enabled=True,
            source="mirror",
            disabled_reason="",
            manifest_version=self._map.version,
            provider=provider.provider or provider.scheme,
            bucket=provider.bucket,
        )

    # ------------------------------------------------------------------ properties
    @property
    def version(self) -> str:
        return self._map.version

    @property
    def mirror_root(self) -> Path:
        return self._mirror_root

    @property
    def mirror_dir(self) -> str:
        """The normalised mirror directory, relative to `base_dir` (the `.tmp` digest prefix)."""
        return self._map.mirror_dir

    @property
    def fetch_loop_running(self) -> bool:
        thread = self._loop_thread
        return thread is not None and thread.is_alive()

    def last_miss_reason(self) -> str | None:
        """Why the last `ensure_local` on THIS thread did not produce a mirrored file (`None` = no miss)."""
        return getattr(self._tls, "reason", None)

    # ------------------------------------------------------------------ sync API
    def local_path(self, logical: str) -> tuple[Path, MappedAsset] | None:
        """Pure mapping, no I/O: `(<base_dir>/<mirror_rel>, mapped)` or `None` for a non-bucket key."""
        mapped = self._map.map(logical)
        if mapped is None:
            return None
        return self._base_dir / mapped.mirror_rel, mapped

    def ensure_local(self, logical: str) -> Path | None:
        """Return a local regular file for `logical`, fetching it on a miss; `None` when unresolvable."""
        self._tls.reason = None
        key_map = self._map
        mapped = key_map.map(logical)
        if mapped is None:
            return None
        final = self._base_dir / mapped.mirror_rel
        if _stat_regular(final) is not None:
            self.stats.incr("local_hits")
            return final

        if _on_running_loop():
            self.stats.incr("skipped_on_loop")
            return self._fallback(mapped)
        if self._closed:
            self._tls.reason = REASON_FETCH_ERROR
            return self._fallback(mapped)

        store = self._store_or_none(mapped)
        if store is None:
            self._tls.reason = REASON_FETCH_ERROR
            return self._fallback(mapped)

        memo_key = (key_map.version, store.bucket, mapped.object_key)
        if self._negative_hit(memo_key):
            self.stats.incr("negative_memo_hits")
            self._tls.reason = REASON_NOT_FOUND
            return self._fallback(mapped)

        disk_key = str(final)
        with self._inflight_lock:
            event = self._inflight.get(disk_key)
            leader = event is None
            if leader:
                event = threading.Event()
                self._inflight[disk_key] = event
        if not leader:
            self.stats.incr("single_flight_waits")
            event.wait(self._settings.fetch_timeout_seconds + 1.0)
            if _stat_regular(final) is not None:
                return final
            self._tls.reason = REASON_FETCH_ERROR
            return self._fallback(mapped)

        try:
            return self._lead_fetch(store, mapped, final, memo_key)
        finally:
            with self._inflight_lock:
                if self._inflight.get(disk_key) is event:
                    del self._inflight[disk_key]
            event.set()

    def _lead_fetch(
        self, store: ObjectStore, mapped: MappedAsset, final: Path, memo_key: tuple[str, str, str]
    ) -> Path | None:
        admission = self._breaker_admit()
        if admission is None:
            self.stats.incr("breaker_skips")
            self._tls.reason = REASON_BREAKER_OPEN
            return self._fallback(mapped)
        probe = admission == "probe"
        outcome_recorded = False
        try:
            if not self._fetch_slots.acquire(timeout=self._settings.fetch_timeout_seconds):
                self.stats.incr("fetch_errors")
                self._log_error(mapped, "fetch concurrency slot wait timed out")
                self._tls.reason = REASON_FETCH_ERROR
                return self._fallback(mapped)
            try:
                started = time.perf_counter()
                try:
                    data, stat = self._read_remote(store, mapped.object_key)
                except StorageNotFound:
                    self._breaker_success()
                    outcome_recorded = True
                    self.stats.incr("remote_miss")
                    self._remember_missing(memo_key)
                    self._tls.reason = REASON_NOT_FOUND
                    return self._fallback(mapped)
                except StorageTooLarge as exc:
                    self._breaker_success()
                    outcome_recorded = True
                    self.stats.incr("too_large")
                    self._log_error(mapped, f"refused: {exc}")
                    self._tls.reason = REASON_FETCH_ERROR
                    return self._fallback(mapped)
                except Exception as exc:  # transport failure of any kind (StorageUnavailable, timeouts, ...)
                    self._breaker_failure(probe)
                    outcome_recorded = True
                    self.stats.incr("fetch_errors")
                    self._log_error(mapped, f"{type(exc).__name__}: {exc}")
                    self._tls.reason = REASON_FETCH_ERROR
                    return self._fallback(mapped)
                self._breaker_success()
                outcome_recorded = True
                try:
                    self._publish(final, mapped, data, stat.last_modified_ns)
                except OSError as exc:
                    self.stats.incr("fetch_errors")
                    self._log_error(mapped, f"publish failed: {type(exc).__name__}: {exc}")
                    self._tls.reason = REASON_FETCH_ERROR
                    return self._fallback(mapped)
                elapsed = time.perf_counter() - started
                with self.stats._lock:
                    self.stats.fetches += 1
                    self.stats.fetch_bytes += len(data)
                    self.stats.fetch_elapsed_total += elapsed
                return final
            finally:
                self._fetch_slots.release()
        finally:
            if probe and not outcome_recorded:
                with self._breaker_lock:
                    self._probe_in_flight = False

    # ------------------------------------------------------------------ remote read + publish
    def _read_remote(self, store: ObjectStore, key: str) -> tuple[bytes, Any]:
        loop = self._ensure_loop()
        budget = float(self._settings.fetch_timeout_seconds)
        coro = asyncio.wait_for(store.read(key, max_bytes=self._settings.fetch_max_bytes), budget)
        future = asyncio.run_coroutine_threadsafe(coro, loop)
        try:
            return future.result(timeout=budget + 1.0)
        except concurrent.futures.TimeoutError:
            future.cancel()
            raise

    def _publish(self, final: Path, mapped: MappedAsset, data: bytes, last_modified_ns: int | None) -> None:
        """Write to `<mirror_root>/.tmp/…`, stamp mtime, then `os.replace` — a partial file is never visible."""
        tmp_dir = self._mirror_root / _TMP_DIR
        os.makedirs(tmp_dir, exist_ok=True)
        os.makedirs(final.parent, exist_ok=True)
        digest = hashlib.sha256(str(mapped.mirror_rel).encode("utf-8")).hexdigest()[:16]
        tmp = tmp_dir / f"{digest}.{os.getpid()}.{threading.get_ident()}.tmp"
        try:
            with open(tmp, "wb") as handle:
                handle.write(data)
            stamp = last_modified_ns if last_modified_ns is not None else time.time_ns()
            os.utime(tmp, ns=(stamp, stamp))
            os.replace(tmp, final)
        except BaseException:
            try:
                os.unlink(tmp)
            except OSError:
                pass
            raise

    def _fallback(self, mapped: MappedAsset) -> Path | None:
        if not self._settings.local_fallback:
            return None
        legacy = self._base_dir / mapped.legacy_rel
        if _stat_regular(legacy) is None:
            return None
        self.stats.incr("local_fallback_hits")
        if self._fallback_log.first(str(mapped.legacy_rel)):
            logger.warning("mirror.local_fallback key=%s", mapped.legacy_rel)
        return legacy

    def _log_error(self, mapped: MappedAsset, detail: str) -> None:
        if self._error_log.first(mapped.object_key):
            logger.warning("mirror.fetch_error key=%s detail=%s", mapped.object_key, detail)

    # ------------------------------------------------------------------ stores + loop
    def store_for(self, region: str) -> ObjectStore:
        store = self._stores.get(region)
        if store is not None:
            return store
        with self._stores_lock:
            store = self._stores.get(region)
            if store is None:
                store = self._store_factory(region)
                self._stores[region] = store
        return store

    def _store_or_none(self, mapped: MappedAsset) -> ObjectStore | None:
        try:
            return self.store_for(mapped.region)
        except Exception as exc:
            self.stats.incr("fetch_errors")
            if self._error_log.first(f"store:{mapped.region}"):
                logger.error("mirror.store_unavailable region=%s error=%s: %s", mapped.region, type(exc).__name__, exc)
            return None

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        loop = self._loop
        if loop is not None:
            return loop
        with self._loop_lock:
            if self._closed:
                raise RuntimeError("asset mirror is closed")
            if self._loop is None:
                loop = asyncio.new_event_loop()
                thread = threading.Thread(target=loop.run_forever, name="asset-mirror-fetch", daemon=True)
                thread.start()
                self._loop_thread = thread
                self._loop = loop
            return self._loop

    # ------------------------------------------------------------------ negative memo
    def _negative_hit(self, memo_key: tuple[str, str, str]) -> bool:
        if self._settings.negative_ttl_seconds <= 0:
            return False
        now = self._clock()
        with self._negative_lock:
            expires = self._negative.get(memo_key)
            if expires is None:
                return False
            if expires > now:
                return True
            del self._negative[memo_key]
            return False

    def _remember_missing(self, memo_key: tuple[str, str, str]) -> None:
        ttl = self._settings.negative_ttl_seconds
        if ttl <= 0:
            return
        with self._negative_lock:
            if len(self._negative) >= max(1, int(self._settings.negative_memo_max)):
                self._negative.clear()
            self._negative[memo_key] = self._clock() + ttl

    # ------------------------------------------------------------------ breaker
    def _breaker_admit(self) -> str | None:
        """`"closed"` (fetch), `"probe"` (the single half-open fetch) or `None` (skip)."""
        with self._breaker_lock:
            if self._open_until is None:
                return "closed"
            if self._clock() < self._open_until or self._probe_in_flight:
                return None
            self._probe_in_flight = True
            return "probe"

    def _breaker_success(self) -> None:
        with self._breaker_lock:
            self._consecutive_failures = 0
            self._open_until = None
            self._probe_in_flight = False
        self.stats.set(breaker_open=0)

    def _breaker_failure(self, probe: bool) -> None:
        with self._breaker_lock:
            self._consecutive_failures += 1
            tripped = probe or (
                self._open_until is None and self._consecutive_failures >= max(1, int(self._settings.breaker_failures))
            )
            if tripped:
                self._open_until = self._clock() + float(self._settings.breaker_open_seconds)
            self._probe_in_flight = False
        if tripped:
            with self.stats._lock:
                self.stats.breaker_trips += 1
                self.stats.breaker_open = 1
            logger.warning(
                "mirror.breaker_open failures=%s open_seconds=%s",
                self._consecutive_failures,
                self._settings.breaker_open_seconds,
            )

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        """Start the fetch-loop thread (idempotent, thread-safe). `ensure_local` also starts it lazily."""
        if not self._closed:
            self._ensure_loop()

    def close(self) -> None:
        """Close every store on the fetch loop, stop the loop and join the thread. Never raises."""
        with self._loop_lock:
            if self._closed:
                return
            self._closed = True
            loop, thread = self._loop, self._loop_thread
            self._loop = None
            self._loop_thread = None
        with self._stores_lock:
            stores = list(self._stores.values())
            self._stores.clear()
        if loop is None:
            return
        try:
            if stores:
                future = asyncio.run_coroutine_threadsafe(self._close_stores(stores), loop)
                future.result(timeout=self._settings.fetch_timeout_seconds + 1.0)
        except Exception as exc:
            logger.warning("mirror.close_stores_failed error=%s: %s", type(exc).__name__, exc)
        finally:
            loop.call_soon_threadsafe(loop.stop)
            if thread is not None:
                thread.join(timeout=5.0)
            if thread is None or not thread.is_alive():
                loop.close()

    @staticmethod
    async def _close_stores(stores: list[ObjectStore]) -> None:
        for store in stores:
            try:
                await store.close()
            except Exception as exc:
                logger.warning("mirror.store_close_failed store=%s error=%s", getattr(store, "name", "?"), exc)

    def set_version(self, version: str) -> bool:
        """Swap the key map for `version`; returns whether the effective version changed."""
        new_map = AssetKeyMap(mirror_dir=self._map.mirror_dir, version=version)
        if new_map.version == self._map.version:
            return False
        self._map = new_map  # one assignment: a single lookup never sees two versions
        with self._negative_lock:
            self._negative.clear()
        _clear_resolution_caches()
        with self.stats._lock:
            self.stats.version_changes += 1
            self.stats.manifest_version = new_map.version
        logger.info("mirror.version_changed version=%s", new_map.version)
        return True

    def refresh_version(self) -> bool:
        """Poll the version source once and apply a change (called by the periodic poll task)."""
        return self.set_version(self._version_source.current())

    def clear_memos(self) -> None:
        """Clear the negative memo and release the single-flight table (waiters re-stat)."""
        with self._negative_lock:
            self._negative.clear()
        with self._inflight_lock:
            events = list(self._inflight.values())
            self._inflight.clear()
        for event in events:
            event.set()
        self._fallback_log.clear()
        self._error_log.clear()

    def stats_snapshot(self) -> dict[str, Any]:
        return self.stats.snapshot()


class NullMirror:
    """`assets.source == "local"` (or a failed construction): every lookup is `None`, nothing is allocated."""

    version = ""

    def __init__(self, *, source: str = "local", disabled_reason: str = "") -> None:
        self.stats = MirrorStats(enabled=False, source=source, disabled_reason=disabled_reason)

    def local_path(self, logical: str) -> None:
        return None

    def ensure_local(self, logical: str) -> None:
        return None

    def last_miss_reason(self) -> None:
        return None

    def start(self) -> None:
        return None

    def close(self) -> None:
        return None

    def set_version(self, version: str) -> bool:
        return False

    def refresh_version(self) -> bool:
        return False

    def clear_memos(self) -> None:
        return None

    def stats_snapshot(self) -> dict[str, Any]:
        return self.stats.snapshot()


# ---------------------------------------------------------------------- process-wide accessor
_asset_mirror: AssetMirror | NullMirror | None = None
_asset_mirror_lock = threading.Lock()


def build_asset_mirror(settings: Settings) -> AssetMirror | NullMirror:
    """Build the mirror for `settings`; never raises (a failure yields a `NullMirror` with a reason)."""
    assets = settings.assets
    if assets.source != "mirror":
        return NullMirror(source=assets.source)
    try:
        return _build_real_mirror(settings)
    except Exception as exc:
        reason = f"{type(exc).__name__}: {exc}"
        logger.error(
            "mirror.disabled reason=%s provider=%s (falling back to local assets)",
            reason,
            assets.mirror.provider.describe(),
        )
        return NullMirror(source="mirror", disabled_reason=reason)


def _build_real_mirror(settings: Settings) -> AssetMirror:
    import importlib

    from src.assets.version import build_version_source
    from src.storage.provider import opendal_kwargs

    assets = settings.assets
    mirror_settings = assets.mirror
    provider = mirror_settings.provider
    opendal_kwargs(provider, "jp")  # validate the provider block (e.g. missing bucket) without any I/O
    importlib.import_module("opendal")  # an import failure disables the mirror now, not on the first fetch

    def store_factory(region: str) -> ObjectStore:
        from src.storage.opendal_store import OpendalObjectStore

        return OpendalObjectStore.from_provider(
            provider,
            region=region,
            timeout=mirror_settings.fetch_timeout_seconds,
            io_timeout=mirror_settings.fetch_io_timeout_seconds,
            retries=mirror_settings.fetch_retries,
            concurrency=mirror_settings.fetch_concurrency,
            name=f"asset-mirror-{region}",
            max_read_bytes=mirror_settings.fetch_max_bytes,
        )

    return AssetMirror(
        base_dir=assets.base_dir,
        settings=mirror_settings,
        store_factory=store_factory,
        version_source=build_version_source(assets),
        stats=MirrorStats(),
    )


def get_asset_mirror() -> AssetMirror | NullMirror:
    """Lazy process-wide mirror (each spawned heavy worker builds its own on first use)."""
    global _asset_mirror
    mirror = _asset_mirror
    if mirror is not None:
        return mirror
    with _asset_mirror_lock:
        if _asset_mirror is None:
            from src.settings import settings

            _asset_mirror = build_asset_mirror(settings)
        return _asset_mirror


def set_asset_mirror(mirror: AssetMirror | NullMirror | None) -> None:
    """Install `mirror` (test seam / shutdown); `None` resets to lazy construction. Does not close the old one."""
    global _asset_mirror
    with _asset_mirror_lock:
        _asset_mirror = mirror


def start_asset_mirror() -> AssetMirror | NullMirror:
    """Lifespan startup: build the process mirror and start its fetch loop. Never raises (fail-open to local)."""
    try:
        mirror = get_asset_mirror()
        mirror.start()
    except Exception as exc:
        logger.error("mirror.start_failed error=%s: %s (falling back to local assets)", type(exc).__name__, exc)
        mirror = NullMirror(source="mirror", disabled_reason=f"{type(exc).__name__}: {exc}")
        set_asset_mirror(mirror)
        return mirror
    if isinstance(mirror, AssetMirror):
        logger.info(
            "mirror.started version=%s root=%s bucket=%s", mirror.version, mirror.mirror_root, mirror.stats.bucket
        )
    return mirror


def shutdown_asset_mirror() -> None:
    """Lifespan shutdown: close the installed mirror (stores, fetch loop). Never builds one, never raises.

    The closed mirror stays installed so late lookups during shutdown fall back to local paths instead of
    lazily building a new mirror.
    """
    with _asset_mirror_lock:
        mirror = _asset_mirror
    if mirror is None:
        return
    try:
        mirror.close()
    except Exception as exc:
        logger.warning("mirror.shutdown_failed error=%s: %s", type(exc).__name__, exc)
