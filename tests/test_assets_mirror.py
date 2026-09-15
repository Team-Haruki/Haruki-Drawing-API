"""`AssetMirror`: fetch-on-miss, loop refusal, negative memo, breaker, single-flight, atomic publish, fallback."""

from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
import subprocess
import sys
import threading
import time
import types

import pytest

from src.assets import mirror as mirror_mod
from src.assets.mirror import (
    REASON_BREAKER_OPEN,
    REASON_FETCH_ERROR,
    REASON_NOT_FOUND,
    AssetMirror,
    MirrorStats,
    NullMirror,
    build_asset_mirror,
    get_asset_mirror,
    set_asset_mirror,
)
from src.assets.version import StaticVersion
from src.settings import AssetMirrorSettings, AssetsSettings, Settings
from src.storage.protocols import StorageNotFound, StorageUnavailable
from tests.storage_fakes import FakeObjectStore

LOGICAL = "asset/jp-assets/startapp/music/jacket/j001.png"
OBJECT_KEY = "jp-assets/startapp/music/jacket/j001.png"

STATS_KEYS = {
    "enabled",
    "source",
    "disabled_reason",
    "manifest_version",
    "provider",
    "bucket",
    "local_hits",
    "fetches",
    "fetch_bytes",
    "fetch_elapsed_total",
    "remote_miss",
    "fetch_errors",
    "too_large",
    "negative_memo_hits",
    "local_fallback_hits",
    "skipped_on_loop",
    "single_flight_waits",
    "breaker_open",
    "breaker_trips",
    "breaker_skips",
    "version_changes",
    "dir_entries",
    "dir_bytes",
    "sweeps",
    "evicted_entries",
    "evicted_bytes",
    "versions_removed",
}


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


class _MutableVersion:
    def __init__(self, value: str) -> None:
        self.value = value

    def current(self) -> str:
        return self.value


def _make(
    tmp_path: Path,
    store: FakeObjectStore | None = None,
    *,
    clock: _Clock | None = None,
    version=None,
    **settings,
) -> tuple[AssetMirror, FakeObjectStore]:
    store = store if store is not None else FakeObjectStore(bucket="pjsk-assets")
    mirror = AssetMirror(
        base_dir=tmp_path,
        settings=AssetMirrorSettings(**settings),
        store_factory=lambda region: store,
        version_source=version or StaticVersion("v0"),
        stats=MirrorStats(),
        clock=clock or _Clock(),
    )
    return mirror, store


@pytest.fixture
def made(tmp_path: Path):
    created: list[AssetMirror] = []

    def factory(store=None, **kwargs):
        mirror, fake = _make(tmp_path, store, **kwargs)
        created.append(mirror)
        return mirror, fake

    yield factory
    for mirror in created:
        mirror.close()


def _legacy(tmp_path: Path, logical: str = LOGICAL, data: bytes = b"legacy") -> Path:
    path = tmp_path / logical
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)
    return path


# ---------------------------------------------------------------------- mapping + local hits


def test_local_path_is_pure_mapping(made, tmp_path: Path) -> None:
    mirror, store = made()
    path, mapped = mirror.local_path(LOGICAL)
    assert path == tmp_path / "mirror" / "v0" / OBJECT_KEY
    assert mapped.object_key == OBJECT_KEY
    assert mirror.local_path("static_images/x.png") is None
    assert mirror.ensure_local("static_images/x.png") is None
    assert store.ops == 0
    assert not path.exists()
    assert mirror.version == "v0"
    assert mirror.mirror_root == tmp_path / "mirror"


def test_local_hit_does_not_fetch(made, tmp_path: Path) -> None:
    mirror, store = made()
    target = tmp_path / "mirror" / "v0" / OBJECT_KEY
    target.parent.mkdir(parents=True)
    target.write_bytes(b"mirrored")
    assert mirror.ensure_local(LOGICAL) == target
    assert store.ops == 0
    assert mirror.stats_snapshot()["local_hits"] == 1
    assert not mirror.fetch_loop_running


def test_miss_fetches_and_publishes_with_utime(made, tmp_path: Path) -> None:
    lm = 1_700_000_000_123_456_789
    mirror, store = made(FakeObjectStore({OBJECT_KEY: b"remote-bytes"}, last_modified={OBJECT_KEY: lm}))
    path = mirror.ensure_local(LOGICAL)
    assert path == tmp_path / "mirror" / "v0" / OBJECT_KEY
    assert path.read_bytes() == b"remote-bytes"
    assert os.stat(path).st_mtime_ns == lm
    assert store.reads == [OBJECT_KEY]
    snap = mirror.stats_snapshot()
    assert snap["fetches"] == 1
    assert snap["fetch_bytes"] == len(b"remote-bytes")
    assert snap["fetch_elapsed_total"] >= 0.0
    assert mirror.last_miss_reason() is None
    assert list((tmp_path / "mirror" / ".tmp").iterdir()) == []
    assert mirror.fetch_loop_running
    # the second lookup is a plain local hit
    assert mirror.ensure_local(LOGICAL) == path
    assert store.reads == [OBJECT_KEY]


def test_fetch_without_last_modified_uses_fetch_time(made) -> None:
    mirror, _ = made(FakeObjectStore({OBJECT_KEY: b"x"}))
    before = time.time_ns()
    path = mirror.ensure_local(LOGICAL)
    assert os.stat(path).st_mtime_ns >= before - 1_000_000_000


# ---------------------------------------------------------------------- negative memo


def test_not_found_counts_and_memoizes(made, tmp_path: Path) -> None:
    clock = _Clock()
    mirror, store = made(clock=clock, negative_ttl_seconds=60.0)
    assert mirror.ensure_local(LOGICAL) is None
    assert mirror.last_miss_reason() == REASON_NOT_FOUND
    assert store.reads == [OBJECT_KEY]
    assert mirror.ensure_local(LOGICAL) is None
    assert store.ops == 1  # within the TTL: no store call
    snap = mirror.stats_snapshot()
    assert snap["remote_miss"] == 1
    assert snap["negative_memo_hits"] == 1
    assert mirror.last_miss_reason() == REASON_NOT_FOUND

    clock.now += 61.0  # expired -> round trip again
    assert mirror.ensure_local(LOGICAL) is None
    assert store.ops == 2

    mirror.clear_memos()
    assert mirror.ensure_local(LOGICAL) is None
    assert store.ops == 3

    assert mirror.set_version("v1") is True
    assert mirror.ensure_local(LOGICAL) is None
    assert store.ops == 4


def test_negative_memo_disabled_and_bounded(made) -> None:
    mirror, store = made(negative_ttl_seconds=0.0)
    mirror.ensure_local(LOGICAL)
    mirror.ensure_local(LOGICAL)
    assert store.ops == 2

    bounded, store2 = made(negative_memo_max=1)
    other = "asset/jp-assets/startapp/music/jacket/j002.png"
    bounded.ensure_local(LOGICAL)
    bounded.ensure_local(other)  # clear-when-full drops LOGICAL
    bounded.ensure_local(LOGICAL)
    assert store2.ops == 3
    bounded.ensure_local(LOGICAL)
    assert store2.ops == 3


# ---------------------------------------------------------------------- errors + fallback


def test_unavailable_counts_and_logs_once(made, caplog: pytest.LogCaptureFixture) -> None:
    mirror, store = made(FakeObjectStore(fail=StorageUnavailable("down")), breaker_failures=100)
    with caplog.at_level(logging.WARNING, logger="src.assets.mirror"):
        assert mirror.ensure_local(LOGICAL) is None
        assert mirror.ensure_local(LOGICAL) is None
    assert mirror.last_miss_reason() == REASON_FETCH_ERROR
    assert mirror.stats_snapshot()["fetch_errors"] == 2
    assert store.ops == 2  # unavailability is never memoized
    assert sum("mirror.fetch_error" in r.getMessage() for r in caplog.records) == 1


def test_local_fallback_hits_and_warns_once(made, tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    legacy = _legacy(tmp_path)
    mirror, _ = made()
    with caplog.at_level(logging.WARNING, logger="src.assets.mirror"):
        assert mirror.ensure_local(LOGICAL) == legacy
        assert mirror.ensure_local(LOGICAL) == legacy
    assert mirror.stats_snapshot()["local_fallback_hits"] == 2
    assert sum("mirror.local_fallback" in r.getMessage() for r in caplog.records) == 1
    assert not (tmp_path / "mirror" / "v0" / OBJECT_KEY).exists()  # never copied into the mirror


def test_local_fallback_disabled(made, tmp_path: Path) -> None:
    _legacy(tmp_path)
    mirror, _ = made(local_fallback=False)
    assert mirror.ensure_local(LOGICAL) is None
    assert mirror.stats_snapshot()["local_fallback_hits"] == 0


def test_too_large_is_refused(made, tmp_path: Path) -> None:
    mirror, _ = made(FakeObjectStore({OBJECT_KEY: b"x" * 32}), fetch_max_bytes=8)
    assert mirror.ensure_local(LOGICAL) is None
    assert mirror.stats_snapshot()["too_large"] == 1
    assert mirror.last_miss_reason() == REASON_FETCH_ERROR
    legacy = _legacy(tmp_path)
    assert mirror.ensure_local(LOGICAL) == legacy


def test_store_factory_failure_falls_back(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    legacy = _legacy(tmp_path)

    def factory(region: str):
        raise ValueError("bad provider")

    mirror = AssetMirror(
        base_dir=tmp_path,
        settings=AssetMirrorSettings(),
        store_factory=factory,
        version_source=StaticVersion("v0"),
        stats=MirrorStats(),
    )
    with caplog.at_level(logging.ERROR, logger="src.assets.mirror"):
        assert mirror.ensure_local(LOGICAL) == legacy
        assert mirror.ensure_local(LOGICAL) == legacy
    assert mirror.stats_snapshot()["fetch_errors"] == 2
    assert mirror.last_miss_reason() == REASON_FETCH_ERROR
    assert sum("mirror.store_unavailable" in r.getMessage() for r in caplog.records) == 1
    mirror.close()


def test_publish_failure_falls_back(made, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mirror, _ = made(FakeObjectStore({OBJECT_KEY: b"data"}))

    def boom(*args, **kwargs):
        raise OSError("disk full")

    monkeypatch.setattr(mirror_mod.os, "replace", boom)
    assert mirror.ensure_local(LOGICAL) is None
    assert mirror.stats_snapshot()["fetch_errors"] == 1
    assert mirror.last_miss_reason() == REASON_FETCH_ERROR
    assert list((tmp_path / "mirror" / ".tmp").iterdir()) == []  # the tmp file was removed


def test_fetch_timeout_is_a_transport_failure(made) -> None:
    mirror, _ = made(FakeObjectStore({OBJECT_KEY: b"x"}, delay=1.0), fetch_timeout_seconds=0.05)
    assert mirror.ensure_local(LOGICAL) is None
    assert mirror.stats_snapshot()["fetch_errors"] == 1


def test_result_timeout_cancels_future(made, monkeypatch: pytest.MonkeyPatch) -> None:
    mirror, _ = made(FakeObjectStore({OBJECT_KEY: b"x"}, delay=5.0), fetch_timeout_seconds=5.0)
    cancelled: list[bool] = []
    real = asyncio.run_coroutine_threadsafe

    def patched(coro, loop):
        future = real(coro, loop)
        original_result = future.result
        original_cancel = future.cancel

        def result(timeout=None):
            return original_result(timeout=0.01)

        def cancel():
            cancelled.append(True)
            return original_cancel()

        future.result = result
        future.cancel = cancel
        return future

    monkeypatch.setattr(mirror_mod.asyncio, "run_coroutine_threadsafe", patched)
    assert mirror.ensure_local(LOGICAL) is None
    assert cancelled  # the waiting side cancels the in-flight coroutine


def test_concurrency_slot_timeout(made) -> None:
    mirror, store = made(FakeObjectStore({OBJECT_KEY: b"x"}), fetch_concurrency=1, fetch_timeout_seconds=0.05)
    assert mirror._fetch_slots.acquire(timeout=1)
    try:
        assert mirror.ensure_local(LOGICAL) is None
    finally:
        mirror._fetch_slots.release()
    assert store.ops == 0
    assert mirror.stats_snapshot()["fetch_errors"] == 1


# ---------------------------------------------------------------------- loop refusal (I2)


def test_skipped_on_loop_makes_no_store_call_and_still_falls_back(made, tmp_path: Path) -> None:
    legacy = _legacy(tmp_path)
    mirror, store = made(FakeObjectStore({OBJECT_KEY: b"remote"}))

    async def on_loop() -> Path | None:
        return mirror.ensure_local(LOGICAL)

    assert asyncio.run(on_loop()) == legacy
    assert store.ops == 0
    snap = mirror.stats_snapshot()
    assert snap["skipped_on_loop"] == 1
    assert snap["local_fallback_hits"] == 1
    assert not mirror.fetch_loop_running


# ---------------------------------------------------------------------- breaker


def test_breaker_trips_skips_probes_and_resets(made) -> None:
    clock = _Clock()
    store = FakeObjectStore(fail=StorageUnavailable("down"))
    mirror, _ = made(store, clock=clock, breaker_failures=3, breaker_open_seconds=30.0)
    for _ in range(3):
        assert mirror.ensure_local(LOGICAL) is None
    snap = mirror.stats_snapshot()
    assert snap["breaker_trips"] == 1
    assert snap["breaker_open"] == 1
    assert store.ops == 3

    assert mirror.ensure_local(LOGICAL) is None  # open: skipped without a store call
    assert store.ops == 3
    assert mirror.last_miss_reason() == REASON_BREAKER_OPEN
    assert mirror.stats_snapshot()["breaker_skips"] == 1

    clock.now += 31.0  # half-open: one probe, it fails -> reopen
    assert mirror.ensure_local(LOGICAL) is None
    assert store.ops == 4
    assert mirror.stats_snapshot()["breaker_trips"] == 2
    assert mirror.ensure_local(LOGICAL) is None
    assert store.ops == 4

    clock.now += 31.0  # probe succeeds -> closed
    store.fail = None
    store.objects[OBJECT_KEY] = b"ok"
    assert mirror.ensure_local(LOGICAL) is not None
    assert store.ops == 5
    snap = mirror.stats_snapshot()
    assert snap["breaker_open"] == 0
    assert mirror._consecutive_failures == 0


def test_breaker_allows_exactly_one_half_open_probe(made) -> None:
    clock = _Clock()
    store = FakeObjectStore(fail=StorageUnavailable("down"))
    mirror, _ = made(store, clock=clock, breaker_failures=1, breaker_open_seconds=10.0)
    assert mirror.ensure_local(LOGICAL) is None
    assert store.ops == 1
    clock.now += 11.0
    store.fail = None
    store.delay = 0.3
    store.objects[OBJECT_KEY] = b"probe"
    other = "asset/jp-assets/startapp/music/jacket/j002.png"

    results: list[Path | None] = []
    probe = threading.Thread(target=lambda: results.append(mirror.ensure_local(LOGICAL)))
    probe.start()
    deadline = time.monotonic() + 5
    while not mirror._probe_in_flight and time.monotonic() < deadline:
        time.sleep(0.005)
    assert mirror._probe_in_flight
    assert mirror.ensure_local(other) is None  # a second key while the probe runs is skipped
    probe.join()
    assert len(results) == 1
    assert results[0] is not None
    assert store.reads == [OBJECT_KEY]
    assert mirror.stats_snapshot()["breaker_skips"] == 1


def test_probe_released_when_no_outcome(made) -> None:
    clock = _Clock()
    mirror, store = made(
        FakeObjectStore(fail=StorageUnavailable("down")),
        clock=clock,
        breaker_failures=1,
        fetch_concurrency=1,
        fetch_timeout_seconds=0.05,
    )
    mirror.ensure_local(LOGICAL)
    clock.now += 100.0
    assert mirror._fetch_slots.acquire(timeout=1)
    try:
        assert mirror.ensure_local(LOGICAL) is None  # admitted as probe, but no slot -> probe released
    finally:
        mirror._fetch_slots.release()
    assert mirror._probe_in_flight is False
    assert store.ops == 1


def test_not_found_resets_breaker(made) -> None:
    store = FakeObjectStore(fail=StorageUnavailable("down"))
    mirror, _ = made(store, breaker_failures=5)
    mirror.ensure_local(LOGICAL)
    assert mirror._consecutive_failures == 1
    store.fail = StorageNotFound("gone")
    mirror.ensure_local(LOGICAL)
    assert mirror._consecutive_failures == 0


# ---------------------------------------------------------------------- single-flight + atomic publish


def test_single_flight_eight_threads_one_read(made, tmp_path: Path) -> None:
    mirror, store = made(FakeObjectStore({OBJECT_KEY: b"z" * 4096}, delay=0.3))
    barrier = threading.Barrier(8)
    results: list[Path | None] = []
    lock = threading.Lock()

    def worker() -> None:
        barrier.wait()
        value = mirror.ensure_local(LOGICAL)
        with lock:
            results.append(value)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    expected = tmp_path / "mirror" / "v0" / OBJECT_KEY
    assert results == [expected] * 8
    assert store.reads == [OBJECT_KEY]
    snap = mirror.stats_snapshot()
    assert snap["fetches"] == 1
    assert snap["single_flight_waits"] + snap["local_hits"] == 7


def test_single_flight_follower_falls_back_when_leader_fails(made, tmp_path: Path) -> None:
    legacy = _legacy(tmp_path)
    mirror, store = made(FakeObjectStore(fail=StorageUnavailable("down"), delay=0.3), breaker_failures=100)
    results: list[Path | None] = []
    leader = threading.Thread(target=lambda: results.append(mirror.ensure_local(LOGICAL)))
    leader.start()
    deadline = time.monotonic() + 5
    while not mirror._inflight and time.monotonic() < deadline:
        time.sleep(0.005)
    assert mirror.ensure_local(LOGICAL) == legacy
    leader.join()
    assert results == [legacy]
    assert store.ops == 1
    assert mirror.stats_snapshot()["single_flight_waits"] == 1


def test_atomic_publish_never_exposes_partial_file(made, tmp_path: Path) -> None:
    payload = os.urandom(8 * 1024 * 1024)
    mirror, _ = made(FakeObjectStore({OBJECT_KEY: payload}, delay=0.05))
    final = tmp_path / "mirror" / "v0" / OBJECT_KEY
    observed: list[int] = []
    stop = threading.Event()

    def watcher() -> None:
        while not stop.is_set():
            try:
                observed.append(os.stat(final).st_size)
            except FileNotFoundError:
                pass

    thread = threading.Thread(target=watcher)
    thread.start()
    try:
        assert mirror.ensure_local(LOGICAL) == final
    finally:
        stop.set()
        thread.join()
    assert all(size == len(payload) for size in observed)
    assert final.read_bytes() == payload


def test_clear_memos_releases_waiters(made) -> None:
    mirror, _ = made()
    event = threading.Event()
    mirror._inflight["x"] = event
    mirror.clear_memos()
    assert event.is_set()
    assert mirror._inflight == {}


# ---------------------------------------------------------------------- version


def test_set_version_swaps_map_and_clears_caches(made, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    fake_utils = types.ModuleType("src.sekai.base.utils")
    fake_utils.clear_resolved_path_cache = lambda: calls.append("paths")
    fake_utils._load_asset_image_ref_cached = types.SimpleNamespace(cache_clear=lambda: calls.append("refs"))
    monkeypatch.setitem(sys.modules, "src.sekai.base.utils", fake_utils)

    source = _MutableVersion("v0")
    mirror, _ = made(version=source)
    assert mirror.refresh_version() is False
    assert mirror.set_version("v0") is False
    assert calls == []

    source.value = "20260913.1"
    assert mirror.refresh_version() is True
    assert mirror.version == "20260913.1"
    assert mirror.local_path(LOGICAL)[0] == tmp_path / "mirror" / "20260913.1" / OBJECT_KEY
    assert calls == ["paths", "refs"]
    snap = mirror.stats_snapshot()
    assert snap["version_changes"] == 1
    assert snap["manifest_version"] == "20260913.1"

    assert mirror.set_version("../bad") is True  # sanitised to v0
    assert mirror.version == "v0"


def test_set_version_legacy_utils_cache_and_absent_utils(made, monkeypatch: pytest.MonkeyPatch) -> None:
    cache = {"k": "v"}
    fake_utils = types.ModuleType("src.sekai.base.utils")
    fake_utils._resolved_path_cache = cache
    monkeypatch.setitem(sys.modules, "src.sekai.base.utils", fake_utils)
    mirror, _ = made()
    assert mirror.set_version("v2") is True
    assert cache == {}

    monkeypatch.delitem(sys.modules, "src.sekai.base.utils")
    assert mirror.set_version("v3") is True


def test_set_version_clears_real_utils_caches(made) -> None:
    from src.sekai.base import utils

    utils._resolved_path_cache[("base", "path")] = (Path("a"), Path("b"), "b")
    mirror, _ = made()
    assert mirror.set_version("v9") is True
    assert utils._resolved_path_cache == {}


# ---------------------------------------------------------------------- lifecycle


def test_start_close_idempotent(made, tmp_path: Path) -> None:
    mirror, store = made(FakeObjectStore({OBJECT_KEY: b"x"}))
    mirror.start()
    mirror.start()
    assert mirror.fetch_loop_running
    assert mirror.ensure_local(LOGICAL) is not None
    mirror.close()
    mirror.close()
    assert store.closed
    assert not mirror.fetch_loop_running
    mirror.start()  # no restart after close
    assert not mirror.fetch_loop_running
    _legacy(tmp_path, "asset/jp-assets/startapp/other.png")
    assert mirror.ensure_local("asset/jp-assets/startapp/other.png") == tmp_path / "asset/jp-assets/startapp/other.png"
    assert mirror.last_miss_reason() == REASON_FETCH_ERROR
    with pytest.raises(RuntimeError):
        mirror._ensure_loop()


def test_close_without_loop_and_store_close_errors(made, caplog: pytest.LogCaptureFixture) -> None:
    mirror, _ = made()
    mirror.close()  # no loop ever started

    class BadClose(FakeObjectStore):
        async def close(self) -> None:
            raise RuntimeError("close failed")

    bad, _ = made(BadClose({OBJECT_KEY: b"x"}))
    assert bad.ensure_local(LOGICAL) is not None
    with caplog.at_level(logging.WARNING, logger="src.assets.mirror"):
        bad.close()
    assert any("mirror.store_close_failed" in r.getMessage() for r in caplog.records)


def test_close_logs_when_store_close_times_out(made, monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    mirror, _ = made(FakeObjectStore({OBJECT_KEY: b"x"}))
    assert mirror.ensure_local(LOGICAL) is not None

    async def hang(stores) -> None:
        raise RuntimeError("boom")

    monkeypatch.setattr(mirror, "_close_stores", hang)
    with caplog.at_level(logging.WARNING, logger="src.assets.mirror"):
        mirror.close()
    assert any("mirror.close_stores_failed" in r.getMessage() for r in caplog.records)


def test_store_for_caches_per_region(tmp_path: Path) -> None:
    built: list[str] = []

    def factory(region: str) -> FakeObjectStore:
        built.append(region)
        return FakeObjectStore(name=region)

    mirror = AssetMirror(
        base_dir=tmp_path,
        settings=AssetMirrorSettings(),
        store_factory=factory,
        version_source=StaticVersion("v0"),
        stats=MirrorStats(),
    )
    assert mirror.store_for("jp") is mirror.store_for("jp")
    assert mirror.store_for("en").name == "en"
    assert built == ["jp", "en"]


# ---------------------------------------------------------------------- stats


def test_stats_snapshot_has_every_key(made) -> None:
    mirror, _ = made()
    snap = mirror.stats_snapshot()
    assert set(snap) == STATS_KEYS
    assert snap["enabled"] is True
    assert snap["source"] == "mirror"
    assert snap["bucket"] == "pjsk-assets"
    assert snap["provider"] == "s3"
    assert set(NullMirror().stats_snapshot()) == STATS_KEYS


def test_stats_set_rejects_unknown() -> None:
    stats = MirrorStats()
    with pytest.raises(AttributeError):
        stats.set(nope=1)
    with pytest.raises(AttributeError):
        stats.set(_lock=None)


# ---------------------------------------------------------------------- NullMirror + accessor


def test_null_mirror_is_inert() -> None:
    null = NullMirror(source="mirror", disabled_reason="x")
    assert null.local_path(LOGICAL) is None
    assert null.ensure_local(LOGICAL) is None
    assert null.last_miss_reason() is None
    assert null.set_version("v1") is False
    assert null.refresh_version() is False
    null.start()
    null.close()
    null.clear_memos()
    assert null.version == ""
    snap = null.stats_snapshot()
    assert snap["enabled"] is False
    assert snap["disabled_reason"] == "x"


def _settings(**assets) -> Settings:
    return Settings.model_construct(assets=AssetsSettings(**assets))


def test_source_local_builds_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    def forbidden(*args, **kwargs):
        raise AssertionError("no mirror may be built for source=local")

    monkeypatch.setattr(mirror_mod, "_build_real_mirror", forbidden)
    threads_before = {t.name for t in threading.enumerate()}
    set_asset_mirror(None)
    try:
        from src.settings import settings

        monkeypatch.setattr(settings.assets, "source", "local")
        mirror = get_asset_mirror()
        assert isinstance(mirror, NullMirror)
        assert get_asset_mirror() is mirror
        assert mirror.ensure_local(LOGICAL) is None
    finally:
        set_asset_mirror(None)
    assert "asset-mirror-fetch" not in {t.name for t in threading.enumerate()} - threads_before


def test_build_mirror_from_settings(tmp_path: Path) -> None:
    pytest.importorskip("opendal")
    settings = _settings(
        base_dir=tmp_path,
        source="mirror",
        mirror=AssetMirrorSettings(provider={"scheme": "memory"}, manifest_version="v5"),
    )
    mirror = build_asset_mirror(settings)
    try:
        assert isinstance(mirror, AssetMirror)
        assert mirror.version == "v5"
        assert not mirror.fetch_loop_running  # no thread at construction
        store = mirror.store_for("jp")
        assert store.name == "asset-mirror-jp"
        assert mirror.ensure_local(LOGICAL) is None  # memory operator: NotFound
        assert mirror.stats_snapshot()["remote_miss"] == 1
    finally:
        mirror.close()


def test_construction_failure_returns_null_mirror(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    settings = _settings(
        base_dir=tmp_path,
        source="mirror",
        mirror=AssetMirrorSettings(provider={"scheme": "s3", "bucket": ""}),
    )
    with caplog.at_level(logging.ERROR, logger="src.assets.mirror"):
        mirror = build_asset_mirror(settings)
    assert isinstance(mirror, NullMirror)
    snap = mirror.stats_snapshot()
    assert snap["source"] == "mirror"
    assert "missing bucket" in snap["disabled_reason"]
    assert any("mirror.disabled" in r.getMessage() for r in caplog.records)


def test_opendal_import_failure_returns_null_mirror(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "opendal", None)
    settings = _settings(base_dir=tmp_path, source="mirror")
    mirror = build_asset_mirror(settings)
    assert isinstance(mirror, NullMirror)
    assert "opendal" in mirror.stats_snapshot()["disabled_reason"]


def test_fixture_installs_mirror(asset_mirror) -> None:
    assert get_asset_mirror() is asset_mirror
    asset_mirror.store_for("jp").objects[OBJECT_KEY] = b"fixture"
    assert asset_mirror.ensure_local(LOGICAL).read_bytes() == b"fixture"


def test_import_has_no_side_effects() -> None:
    code = (
        "import sys, threading\n"
        "import src.assets.mirror\n"
        "assert 'opendal' not in sys.modules, 'opendal imported'\n"
        "assert 'src.sekai.base.utils' not in sys.modules\n"
        "assert [t.name for t in threading.enumerate()] == ['MainThread'], threading.enumerate()\n"
    )
    root = Path(__file__).resolve().parents[1]
    result = subprocess.run([sys.executable, "-c", code], cwd=root, capture_output=True, text=True, timeout=60)
    assert result.returncode == 0, result.stderr
