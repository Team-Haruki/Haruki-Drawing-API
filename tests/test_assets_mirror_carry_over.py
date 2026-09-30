"""Revision carry-over: a new asset revision reuses the previous revision's files instead of re-fetching them."""

from __future__ import annotations

import errno
import hashlib
import json
import os
from pathlib import Path
import time

import pytest

from src.assets import request_context
from src.assets.mirror import REASON_NOT_FOUND, MirrorStats
from src.assets.request_context import begin_asset_revision, end_asset_revision
from src.assets.revision_index import (
    SIDECAR_NAME,
    RegionPointer,
    RevisionIndex,
    combined_revision,
    parse_region,
    pointer_key,
    shards_revision,
)
from src.assets.sweeper import MirrorSweeper
from tests.storage_fakes import FakeObjectStore
from tests.test_assets_mirror import LOGICAL, OBJECT_KEY, _make

JACKET_SHARD = "jp-assets/startapp/music/"
THUMB_SHARD = "jp-assets/startapp/thumbnail/"
CN_SHARD = "cn-assets/startapp/thumbnail/"
MTIME = 1_700_000_000_000_000_000


@pytest.fixture(autouse=True)
def reset_latest(monkeypatch):
    monkeypatch.setattr(request_context, "_latest", "")


def _digest(char: str) -> str:
    return char * 64


def _pointer(region: str, shards: dict[str, str]) -> dict:
    return {
        "version": 1,
        "region": region,
        "revision": shards_revision(shards.items()),
        "complete": True,
        "published_at": "2026-10-01T00:00:00Z",
        "shards": [{"prefix": prefix, "key": f"k/{sha}.json", "sha256": sha} for prefix, sha in shards.items()],
    }


def _publish(store: FakeObjectStore, pointers: dict[str, dict[str, str]]) -> str:
    """Write every region's `current.json` and return Cloud's combined revision for them."""
    parsed = {}
    for region, shards in pointers.items():
        body = _pointer(region, shards)
        store.objects[pointer_key(region)] = json.dumps(body).encode()
        parsed[region] = parse_region(body, region)
    return combined_revision(parsed)


def _store() -> FakeObjectStore:
    return FakeObjectStore(objects={OBJECT_KEY: b"jacket"}, last_modified={OBJECT_KEY: MTIME}, bucket="pjsk-assets")


def _wait_for(path: Path) -> None:
    deadline = time.monotonic() + 5.0
    while not path.exists():
        assert time.monotonic() < deadline, f"{path} never appeared"
        time.sleep(0.01)


def _ensure(mirror, revision: str, logical: str = LOGICAL) -> Path | None:
    token = begin_asset_revision(revision)
    try:
        return mirror.ensure_local(logical)
    finally:
        end_asset_revision(token)


def _first_revision(tmp_path: Path, mirror, store: FakeObjectStore, pointers) -> tuple[str, Path]:
    revision = _publish(store, pointers)
    path = _ensure(mirror, revision)
    assert path == tmp_path / "mirror" / revision / OBJECT_KEY
    _wait_for(tmp_path / "mirror" / revision / SIDECAR_NAME)  # recorded in the background on the first miss
    return revision, path


def _object_calls(store: FakeObjectStore) -> tuple[int, int]:
    return store.reads.count(OBJECT_KEY), store.stats.count(OBJECT_KEY)


# ---------------------------------------------------------------------- revision_index


def test_region_revision_and_combined_revision_follow_cloud() -> None:
    body = _pointer("jp", {THUMB_SHARD: _digest("b"), JACKET_SHARD: _digest("a")})
    pointer = parse_region(body, "jp")
    assert pointer.shards == ((JACKET_SHARD, _digest("a")), (THUMB_SHARD, _digest("b")))
    assert pointer.shard_for(OBJECT_KEY) == (JACKET_SHARD, _digest("a"))
    assert pointer.shard_for("jp-assets/ondemand/event/x.png") is None
    # Haruki-Cloud Manager.revision: sha256 over the sorted "region:revision" lines.
    expected = hashlib.sha256(f"jp:{pointer.revision}".encode()).hexdigest()
    assert combined_revision({"jp": pointer}) == expected


def test_shard_for_prefers_the_longest_prefix() -> None:
    pointer = RegionPointer(revision=_digest("0"), shards=(("jp-assets/", _digest("a")), (JACKET_SHARD, _digest("b"))))
    assert pointer.shard_for(OBJECT_KEY) == (JACKET_SHARD, _digest("b"))


@pytest.mark.parametrize(
    "mutate",
    [
        lambda body: body.update(revision=_digest("f")),
        lambda body: body.update(complete=False),
        lambda body: body.update(region="en"),
        lambda body: body["shards"][0].update(prefix="en-assets/x/"),
        lambda body: body["shards"][0].update(sha256="nope"),
        lambda body: body.update(shards=[]),
    ],
)
def test_parse_region_refuses_what_cloud_refuses(mutate) -> None:
    body = _pointer("jp", {JACKET_SHARD: _digest("a")})
    mutate(body)
    with pytest.raises(ValueError, match="asset index"):
        parse_region(body, "jp")


def test_sidecar_round_trip_rechecks_the_revision() -> None:
    regions = {"jp": parse_region(_pointer("jp", {JACKET_SHARD: _digest("a")}), "jp")}
    index = RevisionIndex.build(regions)
    assert RevisionIndex.from_json(index.to_json(), index.revision) == index
    with pytest.raises(ValueError, match="asset index"):
        RevisionIndex.from_json(index.to_json(), _digest("e"))


# ---------------------------------------------------------------------- carry-over by inventory


def test_other_region_publish_links_without_any_remote_call(tmp_path: Path) -> None:
    mirror, store = _make(tmp_path, _store(), local_fallback=False)
    try:
        old, old_path = _first_revision(
            tmp_path, mirror, store, {"jp": {JACKET_SHARD: _digest("a")}, "cn": {CN_SHARD: _digest("c")}}
        )
        new = _publish(store, {"jp": {JACKET_SHARD: _digest("a")}, "cn": {CN_SHARD: _digest("d")}})
        assert new != old
        before = _object_calls(store)

        path = _ensure(mirror, new)

        assert path == tmp_path / "mirror" / new / OBJECT_KEY
        assert _object_calls(store) == before  # neither a stat nor a read of the object
        assert os.stat(path).st_ino == os.stat(old_path).st_ino
        assert os.stat(path).st_mtime_ns == MTIME
        snap = mirror.stats_snapshot()
        assert snap["carried_by_index"] == 1
        assert snap["fetches"] == 1
        assert (tmp_path / "mirror" / new / SIDECAR_NAME).is_file()
    finally:
        mirror.close()


def test_same_region_unchanged_shard_links_without_remote_call(tmp_path: Path) -> None:
    mirror, store = _make(tmp_path, _store(), local_fallback=False)
    try:
        _first_revision(tmp_path, mirror, store, {"jp": {JACKET_SHARD: _digest("a"), THUMB_SHARD: _digest("b")}})
        new = _publish(store, {"jp": {JACKET_SHARD: _digest("a"), THUMB_SHARD: _digest("9")}})
        before = _object_calls(store)
        assert _ensure(mirror, new).read_bytes() == b"jacket"
        assert _object_calls(store) == before
        assert mirror.stats_snapshot()["carried_by_index"] == 1
    finally:
        mirror.close()


# ---------------------------------------------------------------------- carry-over by remote stat


def test_changed_shard_with_same_size_and_mtime_links_after_one_stat(tmp_path: Path) -> None:
    mirror, store = _make(tmp_path, _store(), local_fallback=False)
    try:
        _first_revision(tmp_path, mirror, store, {"jp": {JACKET_SHARD: _digest("a")}})
        new = _publish(store, {"jp": {JACKET_SHARD: _digest("b")}})
        reads, stats = _object_calls(store)
        assert _ensure(mirror, new).read_bytes() == b"jacket"
        assert _object_calls(store) == (reads, stats + 1)
        snap = mirror.stats_snapshot()
        assert (snap["carried_by_index"], snap["carried_by_stat"], snap["fetches"]) == (0, 1, 1)
    finally:
        mirror.close()


def test_changed_object_is_fetched_again(tmp_path: Path) -> None:
    mirror, store = _make(tmp_path, _store(), local_fallback=False)
    try:
        _, old_path = _first_revision(tmp_path, mirror, store, {"jp": {JACKET_SHARD: _digest("a")}})
        new = _publish(store, {"jp": {JACKET_SHARD: _digest("b")}})
        store.objects[OBJECT_KEY] = b"JACKET"  # same size, new upload
        store.last_modified[OBJECT_KEY] = MTIME + 1_000_000_000
        path = _ensure(mirror, new)
        assert path.read_bytes() == b"JACKET"
        assert old_path.read_bytes() == b"jacket"
        assert os.stat(path).st_ino != os.stat(old_path).st_ino
        snap = mirror.stats_snapshot()
        assert (snap["carried_by_stat"], snap["fetches"]) == (0, 2)
    finally:
        mirror.close()


def test_unknown_remote_mtime_never_carries(tmp_path: Path) -> None:
    store = FakeObjectStore(objects={OBJECT_KEY: b"jacket"}, bucket="pjsk-assets")
    mirror, _ = _make(tmp_path, store, local_fallback=False)
    try:
        assert _ensure(mirror, "v1") is not None
        assert _ensure(mirror, "v2") is not None
        assert mirror.stats_snapshot()["fetches"] == 2
    finally:
        mirror.close()


def test_directory_without_inventory_is_verified_by_stat(tmp_path: Path) -> None:
    """`v0` (no Cloud revision) and directories from before this change have no sidecar."""
    mirror, store = _make(tmp_path, _store(), local_fallback=False)
    try:
        assert _ensure(mirror, "v0") is not None
        new = _publish(store, {"jp": {JACKET_SHARD: _digest("a")}})
        reads, stats = _object_calls(store)
        assert _ensure(mirror, new) == tmp_path / "mirror" / new / OBJECT_KEY
        assert _object_calls(store) == (reads, stats + 1)
        assert mirror.stats_snapshot()["carried_by_stat"] == 1
    finally:
        mirror.close()


def test_unresolvable_revision_falls_back_to_stat(tmp_path: Path) -> None:
    """Cloud's revision differs from the bucket's pointers (Cloud lagging, or a stale region)."""
    mirror, store = _make(tmp_path, _store(), local_fallback=False)
    try:
        _first_revision(tmp_path, mirror, store, {"jp": {JACKET_SHARD: _digest("a")}})
        _publish(store, {"jp": {JACKET_SHARD: _digest("a")}, "cn": {CN_SHARD: _digest("c")}})
        assert _ensure(mirror, _digest("7")) is not None
        snap = mirror.stats_snapshot()
        assert (snap["carried_by_index"], snap["carried_by_stat"]) == (0, 1)
        assert snap["revision_index_failures"] == 1
        assert not (tmp_path / "mirror" / _digest("7") / SIDECAR_NAME).exists()
    finally:
        mirror.close()


# ---------------------------------------------------------------------- negative memo, safety, fallbacks


def test_object_gone_remotely_is_a_memoized_not_found(tmp_path: Path) -> None:
    mirror, store = _make(tmp_path, _store(), local_fallback=False)
    try:
        _first_revision(tmp_path, mirror, store, {"jp": {JACKET_SHARD: _digest("a")}})
        new = _publish(store, {"jp": {JACKET_SHARD: _digest("b")}})
        del store.objects[OBJECT_KEY]
        token = begin_asset_revision(new)
        try:
            assert mirror.ensure_local(LOGICAL) is None
            assert mirror.last_miss_reason() == REASON_NOT_FOUND
            calls = _object_calls(store)
            assert mirror.ensure_local(LOGICAL) is None  # negative memo: no stat, no link
            assert _object_calls(store) == calls
        finally:
            end_asset_revision(token)
        snap = mirror.stats_snapshot()
        assert (snap["remote_miss"], snap["negative_memo_hits"], snap["carried_by_stat"]) == (1, 1, 0)
        assert not (tmp_path / "mirror" / new / OBJECT_KEY).exists()
    finally:
        mirror.close()


def test_linked_file_survives_removal_of_the_old_revision(tmp_path: Path) -> None:
    mirror, store = _make(tmp_path, _store(), local_fallback=False)
    try:
        old, _ = _first_revision(tmp_path, mirror, store, {"jp": {JACKET_SHARD: _digest("a")}})
        new = _publish(store, {"jp": {JACKET_SHARD: _digest("a")}, "cn": {CN_SHARD: _digest("c")}})
        token = begin_asset_revision(new)
        try:
            path = mirror.ensure_local(LOGICAL)
            with path.open("rb") as handle:
                sweeper = MirrorSweeper(
                    root=tmp_path / "mirror",
                    current_version=lambda: new,
                    max_bytes=0,
                    max_entries=0,
                    versions_keep=0,
                    tmp_max_age_seconds=3600,
                    stats=MirrorStats(),
                )
                assert sweeper.sweep_once().versions_removed == 1
                assert not (tmp_path / "mirror" / old).exists()
                assert handle.read() == b"jacket"
            assert path.read_bytes() == b"jacket"
        finally:
            end_asset_revision(token)
    finally:
        mirror.close()


def test_link_failure_copies_with_the_same_mtime(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mirror, store = _make(tmp_path, _store(), local_fallback=False)
    try:
        _, old_path = _first_revision(tmp_path, mirror, store, {"jp": {JACKET_SHARD: _digest("a")}})
        new = _publish(store, {"jp": {JACKET_SHARD: _digest("a")}, "cn": {CN_SHARD: _digest("c")}})

        def cross_device(src, dst, **kwargs):
            raise OSError(errno.EXDEV, "Invalid cross-device link")

        monkeypatch.setattr(os, "link", cross_device)
        path = _ensure(mirror, new)
        assert path.read_bytes() == b"jacket"
        assert os.stat(path).st_ino != os.stat(old_path).st_ino
        assert os.stat(path).st_mtime_ns == MTIME
        assert mirror.stats_snapshot()["carry_copies"] == 1
        assert not list((tmp_path / "mirror" / ".tmp").glob("*.tmp"))
    finally:
        mirror.close()


def test_vanished_source_is_fetched(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    mirror, store = _make(tmp_path, _store(), local_fallback=False)
    try:
        _first_revision(tmp_path, mirror, store, {"jp": {JACKET_SHARD: _digest("a")}})
        new = _publish(store, {"jp": {JACKET_SHARD: _digest("a")}, "cn": {CN_SHARD: _digest("c")}})

        def retired(src, dst, **kwargs):
            raise FileNotFoundError(errno.ENOENT, "retired by the sweeper", str(src))

        monkeypatch.setattr(os, "link", retired)
        assert _ensure(mirror, new).read_bytes() == b"jacket"
        snap = mirror.stats_snapshot()
        assert (snap["carried_by_index"], snap["carry_copies"], snap["fetches"]) == (0, 0, 2)
    finally:
        mirror.close()


def test_carry_over_can_be_disabled(tmp_path: Path) -> None:
    mirror, store = _make(tmp_path, _store(), local_fallback=False, revision_carry_over=False)
    try:
        first = _publish(store, {"jp": {JACKET_SHARD: _digest("a")}})
        assert _ensure(mirror, first) is not None
        second = _publish(store, {"jp": {JACKET_SHARD: _digest("a")}, "cn": {CN_SHARD: _digest("c")}})
        assert _ensure(mirror, second) is not None
        snap = mirror.stats_snapshot()
        assert (snap["fetches"], snap["carried_by_index"], snap["carried_by_stat"]) == (2, 0, 0)
        assert not (tmp_path / "mirror" / first / SIDECAR_NAME).exists()
    finally:
        mirror.close()


def test_sweeper_never_evicts_or_counts_the_inventory(tmp_path: Path) -> None:
    revision = _digest("a")
    current = tmp_path / revision
    (current / "jp-assets").mkdir(parents=True)
    (current / "jp-assets" / "a.png").write_bytes(b"12345")
    (current / SIDECAR_NAME).write_bytes(b"{}" * 100)
    result = MirrorSweeper(
        root=tmp_path,
        current_version=lambda: revision,
        max_bytes=1,
        max_entries=0,
        versions_keep=0,
        tmp_max_age_seconds=3600,
        stats=MirrorStats(),
    ).sweep_once()
    assert (result.evicted_entries, result.entries, result.bytes) == (1, 0, 0)
    assert (current / SIDECAR_NAME).is_file()
