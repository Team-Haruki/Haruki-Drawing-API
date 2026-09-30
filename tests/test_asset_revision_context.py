"""Concurrent requests keep independent mirror paths and protect in-flight generations."""

import asyncio

import pytest

from src.assets import request_context
from src.assets.mirror import MirrorStats
from src.assets.request_context import begin_asset_revision, end_asset_revision
from src.assets.sweeper import MirrorSweeper
from src.core import debug
from tests.storage_fakes import FakeObjectStore
from tests.test_assets_mirror import LOGICAL, OBJECT_KEY, _make


@pytest.fixture(autouse=True)
def reset_latest(monkeypatch):
    monkeypatch.setattr(request_context, "_latest", "")


def test_same_object_new_revision_fetches_new_bytes(tmp_path):
    mirror, store = _make(tmp_path, FakeObjectStore(objects={OBJECT_KEY: b"old"}), local_fallback=False)
    first = begin_asset_revision("a" * 64)
    try:
        path_a = mirror.ensure_local(LOGICAL)
        assert path_a.read_bytes() == b"old"
        store.objects[OBJECT_KEY] = b"new"
        second = begin_asset_revision("b" * 64)
        try:
            path_b = mirror.ensure_local(LOGICAL)
            assert path_b != path_a
            assert path_b.read_bytes() == b"new"
        finally:
            end_asset_revision(second)
        assert mirror.ensure_local(LOGICAL) == path_a
        assert path_a.read_bytes() == b"old"
    finally:
        end_asset_revision(first)
        mirror.close()
    assert request_context.active_asset_revisions() == set()
    assert mirror.version == "v0"


def test_concurrent_revisions_do_not_mutate_global_map(tmp_path):
    mirror, _ = _make(tmp_path)

    async def request(revision):
        token = begin_asset_revision(revision)
        try:
            await asyncio.sleep(0)
            assert mirror.version == revision
            assert revision in str(mirror.local_path(LOGICAL)[0])
        finally:
            end_asset_revision(token)

    async def run():
        await asyncio.gather(request("a" * 64), request("b" * 64))

    asyncio.run(run())
    assert mirror.version == "v0"
    mirror.close()


def test_sweeper_preserves_active_old_revision_then_reclaims_it(tmp_path):
    old = "a" * 64
    path = tmp_path / old / "image.png"
    path.parent.mkdir()
    path.write_bytes(b"image")
    sweeper = MirrorSweeper(
        root=tmp_path,
        current_version=lambda: "b" * 64,
        max_bytes=0,
        max_entries=0,
        versions_keep=0,
        tmp_max_age_seconds=100,
        stats=MirrorStats(),
    )
    token = begin_asset_revision(old)
    try:
        assert sweeper.sweep_once().versions_removed == 0
        assert path.exists()
    finally:
        end_asset_revision(token)
    newer = begin_asset_revision("b" * 64)
    end_asset_revision(newer)
    assert sweeper.sweep_once().versions_removed == 1
    assert not path.exists()


def test_debug_cleanup_releases_revision():
    tokens = debug.push_request_context("r", "/x", "POST")
    end_asset_revision(tokens.asset_revision)
    tokens.asset_revision = begin_asset_revision("a" * 64)
    assert request_context.current_asset_revision() == "a" * 64
    debug.pop_request_context(tokens)
    assert request_context.current_asset_revision() == ""
    assert request_context.active_asset_revisions() == set()


def test_new_request_after_retirement_rebuilds_original_namespace(tmp_path, monkeypatch):
    old = "a" * 64
    original = tmp_path / old
    original.mkdir()
    (original / "old.png").write_bytes(b"old")
    sweeper = MirrorSweeper(
        root=tmp_path,
        current_version=lambda: "b" * 64,
        max_bytes=0,
        max_entries=0,
        versions_keep=0,
        tmp_max_age_seconds=100,
        stats=MirrorStats(),
    )
    remove = sweeper._remove_tree

    def recreate_then_delete(retired):
        token = begin_asset_revision(old)
        try:
            original.mkdir()
            (original / "new.png").write_bytes(b"new")
            return remove(retired)
        finally:
            end_asset_revision(token)

    monkeypatch.setattr(sweeper, "_remove_tree", recreate_then_delete)
    newer = begin_asset_revision("b" * 64)
    end_asset_revision(newer)
    assert sweeper.sweep_once().versions_removed == 1
    assert (original / "new.png").read_bytes() == b"new"


def test_sweeper_defers_current_file_eviction_while_request_can_decode(tmp_path):
    revision = "a" * 64
    path = tmp_path / revision / "image.png"
    path.parent.mkdir()
    path.write_bytes(b"image")
    sweeper = MirrorSweeper(
        root=tmp_path,
        current_version=lambda: revision,
        max_bytes=1,
        max_entries=1,
        versions_keep=0,
        tmp_max_age_seconds=100,
        stats=MirrorStats(),
    )
    token = begin_asset_revision(revision)
    try:
        assert sweeper.sweep_once().evicted_entries == 0
        assert path.exists()
    finally:
        end_asset_revision(token)
    assert sweeper.sweep_once().evicted_entries == 1
    assert not path.exists()


def test_retired_namespace_symlink_is_never_followed(tmp_path):
    outside = tmp_path / "outside"
    outside.mkdir()
    payload = outside / "safe.png"
    payload.write_bytes(b"safe")
    root = tmp_path / "mirror"
    root.mkdir()
    (root / ".retired-symlink").symlink_to(outside, target_is_directory=True)
    sweeper = MirrorSweeper(
        root=root,
        current_version=lambda: "a" * 64,
        max_bytes=1,
        max_entries=1,
        versions_keep=0,
        tmp_max_age_seconds=100,
        stats=MirrorStats(),
    )
    sweeper.sweep_once()
    assert payload.read_bytes() == b"safe"


def test_cancelled_request_keeps_executing_pool_thread_namespace_leased(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    import threading

    from src.sekai.base.utils import run_in_pool

    revision = "a" * 64
    path = tmp_path / revision / "image.png"
    path.parent.mkdir()
    path.write_bytes(b"image")
    entered, release, finished = threading.Event(), threading.Event(), threading.Event()
    sweeper = MirrorSweeper(
        root=tmp_path,
        current_version=lambda: "b" * 64,
        max_bytes=0,
        max_entries=0,
        versions_keep=0,
        tmp_max_age_seconds=100,
        stats=MirrorStats(),
    )

    def thread_work():
        entered.set()
        try:
            assert release.wait(5)
            assert path.read_bytes() == b"image"
        finally:
            finished.set()

    async def run(pool):
        token = begin_asset_revision(revision)
        task = asyncio.create_task(run_in_pool(thread_work, pool=pool))
        assert await asyncio.to_thread(entered.wait, 5)
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
        end_asset_revision(token)
        try:
            assert sweeper.sweep_once().versions_removed == 0
            assert path.exists()
        finally:
            release.set()
            assert await asyncio.to_thread(finished.wait, 5)

    with ThreadPoolExecutor(max_workers=1) as pool:
        asyncio.run(run(pool))
    newer = begin_asset_revision("b" * 64)
    end_asset_revision(newer)
    assert sweeper.sweep_once().versions_removed == 1


def test_sweeper_keeps_empty_parent_until_active_asset_publish_finishes(tmp_path):
    revision = "a" * 64
    final = tmp_path / revision / "assets" / "image.png"
    final.parent.mkdir(parents=True)
    temporary = tmp_path / "upload.tmp"
    temporary.write_bytes(b"image")
    sweeper = MirrorSweeper(
        root=tmp_path,
        current_version=lambda: revision,
        max_bytes=0,
        max_entries=0,
        versions_keep=0,
        tmp_max_age_seconds=100,
        stats=MirrorStats(),
    )
    token = begin_asset_revision(revision)
    try:
        sweeper.sweep_once()
        temporary.replace(final)
        assert final.read_bytes() == b"image"
    finally:
        end_asset_revision(token)


def test_background_pool_sweep_does_not_lease_its_own_deletion_candidates(tmp_path):
    from src.sekai.base.utils import run_in_pool

    path = tmp_path / "old" / "image.png"
    path.parent.mkdir()
    path.write_bytes(b"image")
    sweeper = MirrorSweeper(
        root=tmp_path,
        current_version=lambda: "new",
        max_bytes=0,
        max_entries=0,
        versions_keep=0,
        tmp_max_age_seconds=100,
        stats=MirrorStats(),
    )
    assert not request_context.has_asset_revision_scope()
    assert asyncio.run(run_in_pool(sweeper.sweep_once)).versions_removed == 1
    assert not path.exists()


def test_old_remote_mtime_does_not_make_inflight_publish_scratch_stale(tmp_path, monkeypatch):
    import os
    import time

    mirror, _ = _make(tmp_path, FakeObjectStore(objects={OBJECT_KEY: b"new"}), local_fallback=False)
    root = tmp_path / "mirror"
    sweeper = MirrorSweeper(
        root=root,
        current_version=lambda: "v0",
        max_bytes=0,
        max_entries=0,
        versions_keep=0,
        tmp_max_age_seconds=3600,
        stats=MirrorStats(),
    )
    replace = os.replace

    def sweep_before_publish(source, destination):
        # Reproduce the window after the store's old Last-Modified was copied to tmp.
        old = time.time_ns() - 10 * 86400 * 1_000_000_000
        os.utime(source, ns=(old, old))
        sweeper.sweep_once()
        replace(source, destination)

    monkeypatch.setattr(os, "replace", sweep_before_publish)
    token = begin_asset_revision("v0")
    try:
        path = mirror.ensure_local(LOGICAL)
        assert path.read_bytes() == b"new"
    finally:
        end_asset_revision(token)
        mirror.close()


def test_legacy_local_fallback_marks_result_incomplete_for_new_remote_revision(tmp_path):
    from src.core.missing_asset_telemetry import current_missing_asset_count
    from src.core.utils import encoded_image_payload_to_bytes_response
    from tests.test_artifact_exit import _payload

    mirror, _ = _make(tmp_path, FakeObjectStore(), local_fallback=True)
    legacy = tmp_path / LOGICAL
    legacy.parent.mkdir(parents=True)
    legacy.write_bytes(b"old-local-pixels")
    tokens = debug.push_request_context("fallback", "/x", "POST")
    token = begin_asset_revision("a" * 64)
    try:
        assert mirror.ensure_local(LOGICAL) == legacy
        assert current_missing_asset_count() == 1
        response = encoded_image_payload_to_bytes_response(_payload())
        assert response.headers["x-haruki-cache-store"] == "0"
    finally:
        end_asset_revision(token)
        debug.pop_request_context(tokens)
        mirror.close()


def test_latest_revision_cannot_retire_after_sweeper_enumeration(tmp_path):
    path = tmp_path / "new"
    path.mkdir()
    token = begin_asset_revision("new")
    end_asset_revision(token)
    assert not request_context.retire_asset_revision("new", path, tmp_path / "retired")
    assert path.is_dir()
