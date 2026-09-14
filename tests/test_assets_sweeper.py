"""`MirrorSweeper`: version dirs, stale `.tmp`, byte/entry caps by atime, empty-dir pruning, symlink safety."""

from __future__ import annotations

import hashlib
import logging
import os
from pathlib import Path, PurePosixPath

import pytest

from src.assets import sweeper as sweeper_mod
from src.assets.mirror import MirrorStats
from src.assets.sweeper import MirrorSweeper, SweepResult

NOW = 1_800_000_000.0


def _write(path: Path, size: int, *, atime: float | None = None, mtime: float | None = None) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(b"x" * size)
    if atime is not None or mtime is not None:
        st = path.stat()
        os.utime(path, (atime if atime is not None else st.st_atime, mtime if mtime is not None else st.st_mtime))
    return path


def _age_dir(path: Path, mtime: float) -> None:
    os.utime(path, (mtime, mtime))


def _sweeper(root: Path, stats: MirrorStats | None = None, **overrides) -> MirrorSweeper:
    options = {
        "root": root,
        "current_version": lambda: "v3",
        "max_bytes": 0,
        "max_entries": 0,
        "versions_keep": 2,
        "tmp_max_age_seconds": 3600,
        "stats": stats or MirrorStats(),
        "clock": lambda: NOW,
    }
    options.update(overrides)
    return MirrorSweeper(**options)


@pytest.fixture
def root(tmp_path: Path) -> Path:
    mirror = tmp_path / "mirror"
    mirror.mkdir()
    return mirror


def test_missing_root_is_a_noop(tmp_path: Path) -> None:
    stats = MirrorStats()
    result = _sweeper(tmp_path / "absent", stats).sweep_once()
    assert result == SweepResult(0, 0, 0, 0, 0)
    assert _sweeper(tmp_path / "absent").root == tmp_path / "absent"
    assert stats.sweeps == 0


def test_version_dirs_beyond_keep_are_removed_oldest_first(root: Path, caplog: pytest.LogCaptureFixture) -> None:
    for index, name in enumerate(["v0", "v1", "v2", "v3"]):
        _write(root / name / "jp-assets" / "startapp" / "a.png", 4)
        _age_dir(root / name, NOW - 1000 + index)
    (root / "stray.txt").write_text("not a version dir")
    stats = MirrorStats()
    with caplog.at_level(logging.INFO, logger="src.assets.sweeper"):
        result = _sweeper(root, stats, versions_keep=1).sweep_once()
    assert result.versions_removed == 2
    assert sorted(p.name for p in root.iterdir()) == ["stray.txt", "v2", "v3"]
    removed = [r.message for r in caplog.records if "version_removed" in r.message]
    assert removed[0].endswith("version=v0")
    assert removed[1].endswith("version=v1")
    assert stats.versions_removed == 2
    assert stats.sweeps == 1
    assert result.entries == 1
    assert result.bytes == 4
    assert stats.dir_entries == 1
    assert stats.dir_bytes == 4


def test_current_version_is_kept_even_when_oldest(root: Path) -> None:
    for index, name in enumerate(["v3", "v1", "v2"]):
        (root / name).mkdir()
        _age_dir(root / name, NOW - 1000 + index)
    result = _sweeper(root, versions_keep=0).sweep_once()
    assert result.versions_removed == 2
    assert [p.name for p in root.iterdir()] == ["v3"]


def test_stale_tmp_removed_young_tmp_kept(root: Path) -> None:
    tmp = root / ".tmp"
    old = _write(tmp / "aaaa.1.2.tmp", 3, mtime=NOW - 7200)
    young = _write(tmp / "bbbb.1.2.tmp", 3, mtime=NOW - 10)
    (tmp / "subdir").mkdir()
    _sweeper(root).sweep_once()
    assert not old.exists()
    assert young.exists()
    assert (tmp / "subdir").is_dir()


def test_byte_cap_evicts_oldest_atime_first(root: Path) -> None:
    current = root / "v3" / "jp-assets" / "startapp"
    oldest = _write(current / "a" / "oldest.png", 40, atime=NOW - 300, mtime=NOW - 999)
    middle = _write(current / "b" / "middle.png", 40, atime=NOW - 200, mtime=NOW - 999)
    newest = _write(current / "c" / "newest.png", 40, atime=NOW - 100, mtime=NOW - 999)
    stats = MirrorStats()
    result = _sweeper(root, stats, max_bytes=90).sweep_once()
    assert not oldest.exists()
    assert middle.exists()
    assert newest.exists()
    assert result == SweepResult(0, 1, 40, 2, 80)
    assert stats.evicted_entries == 1
    assert stats.evicted_bytes == 40
    assert stats.dir_entries == 2
    assert stats.dir_bytes == 80
    # mtime is part of the asset identity and is never touched
    assert middle.stat().st_mtime == pytest.approx(NOW - 999)
    # the emptied sub-directory is pruned, the version dir itself is kept
    assert not (current / "a").exists()
    assert (root / "v3").is_dir()


def test_entry_cap_evicts_until_under(root: Path) -> None:
    current = root / "v3"
    files = [_write(current / f"f{i}.png", 1, atime=NOW - 100 + i) for i in range(5)]
    result = _sweeper(root, max_entries=2).sweep_once()
    assert [f.exists() for f in files] == [False, False, False, True, True]
    assert result.evicted_entries == 3
    assert result.entries == 2


def test_both_caps_must_hold(root: Path) -> None:
    current = root / "v3"
    files = [_write(current / f"f{i}.png", 10, atime=NOW - 100 + i) for i in range(4)]
    result = _sweeper(root, max_entries=3, max_bytes=15).sweep_once()
    assert [f.exists() for f in files] == [False, False, False, True]
    assert result.entries == 1
    assert result.bytes == 10


def test_no_caps_means_no_eviction_but_empty_dirs_pruned(root: Path) -> None:
    current = root / "v3"
    kept = _write(current / "keep" / "a.png", 10)
    (current / "empty" / "nested").mkdir(parents=True)
    result = _sweeper(root).sweep_once()
    assert kept.exists()
    assert not (current / "empty").exists()
    assert result == SweepResult(0, 0, 0, 1, 10)


def test_file_with_young_inflight_tmp_is_skipped(root: Path) -> None:
    current = root / "v3"
    busy = _write(current / "jp-assets" / "startapp" / "busy.png", 10, atime=NOW - 500)
    idle = _write(current / "jp-assets" / "startapp" / "idle.png", 10, atime=NOW - 400)
    digest = hashlib.sha256(str(PurePosixPath("mirror/v3/jp-assets/startapp/busy.png")).encode()).hexdigest()[:16]
    _write(root / ".tmp" / f"{digest}.10.20.tmp", 1, mtime=NOW - 5)
    result = _sweeper(root, max_entries=1).sweep_once()
    assert busy.exists()
    assert not idle.exists()
    assert result.evicted_entries == 1


def test_mirror_dir_override_drives_tmp_digest(root: Path) -> None:
    current = root / "v3"
    busy = _write(current / "k.png", 10, atime=NOW - 500)
    digest = hashlib.sha256(b"deep/mirror/v3/k.png").hexdigest()[:16]
    _write(root / ".tmp" / f"{digest}.1.1.tmp", 1, mtime=NOW - 5)
    _sweeper(root, max_entries=0, max_bytes=1, mirror_dir="deep/mirror").sweep_once()
    assert busy.exists()


def test_never_touches_anything_outside_root(tmp_path: Path, root: Path) -> None:
    outside = tmp_path / "outside"
    victim = _write(outside / "victim.png", 100, atime=NOW - 10_000)
    victim_dir = outside / "dir"
    _write(victim_dir / "inner.png", 100, atime=NOW - 10_000)
    current = root / "v3"
    current.mkdir()
    # symlinks inside the current version (file + dir), an old version that is a symlink, and a version dir
    # holding a symlink that is removed as a whole
    (current / "link.png").symlink_to(victim)
    (current / "linkdir").symlink_to(victim_dir, target_is_directory=True)
    (root / "v0").symlink_to(outside, target_is_directory=True)
    old = root / "v1"
    old.mkdir()
    (old / "escape").symlink_to(victim_dir, target_is_directory=True)
    (old / "file.png").symlink_to(victim)
    for name in ("v1", "v2"):
        (root / name).mkdir(exist_ok=True)
        _age_dir(root / name, NOW - 5000)
    (root / ".tmp").mkdir()
    (root / ".tmp" / "stale.tmp").symlink_to(victim)
    os.utime(root / ".tmp" / "stale.tmp", (NOW - 9999, NOW - 9999), follow_symlinks=False)

    result = _sweeper(root, versions_keep=0, max_bytes=1, max_entries=0, tmp_max_age_seconds=1).sweep_once()

    assert victim.exists()
    assert victim.read_bytes() == b"x" * 100
    assert (victim_dir / "inner.png").exists()
    assert not old.exists()
    assert (root / "v0").is_symlink()  # a symlinked "version" is not a directory entry to recurse into
    assert result.entries == 0


def test_current_dir_symlink_is_not_walked(tmp_path: Path, root: Path) -> None:
    outside = tmp_path / "outside"
    victim = _write(outside / "victim.png", 50)
    (root / "v3").symlink_to(outside, target_is_directory=True)
    result = _sweeper(root, max_bytes=1).sweep_once()
    assert victim.exists()
    assert result.entries == 0


def test_root_symlink_is_not_swept(tmp_path: Path) -> None:
    real = tmp_path / "real"
    (real / "v0").mkdir(parents=True)
    link = tmp_path / "link"
    link.symlink_to(real, target_is_directory=True)
    assert _sweeper(link, versions_keep=0).sweep_once() == SweepResult(0, 0, 0, 0, 0)
    assert (real / "v0").is_dir()


def test_sweep_never_raises(root: Path, caplog: pytest.LogCaptureFixture) -> None:
    def broken_version() -> str:
        raise RuntimeError("version source down")

    stats = MirrorStats()
    with caplog.at_level(logging.WARNING, logger="src.assets.sweeper"):
        result = _sweeper(root, stats, current_version=broken_version).sweep_once()
    assert result == SweepResult(0, 0, 0, 0, 0)
    assert "mirror.sweep_failed" in caplog.text
    assert stats.sweeps == 1


def test_stats_failure_is_swallowed(root: Path, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="src.assets.sweeper"):
        result = _sweeper(root, stats=object()).sweep_once()
    assert result.entries == 0
    assert "mirror.sweep_stats_failed" in caplog.text


def test_filesystem_errors_are_tolerated(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    current = root / "v3"
    first = _write(current / "a.png", 10, atime=NOW - 100)
    _write(current / "b.png", 10, atime=NOW - 50)
    (root / "v1").mkdir()
    _write(root / "v1" / "x.png", 1)

    real_unlink = os.unlink
    real_rmdir = os.rmdir

    def flaky_unlink(path, *args, **kwargs):
        if str(path) == str(first):
            raise PermissionError("busy")
        return real_unlink(path, *args, **kwargs)

    def flaky_rmdir(path, *args, **kwargs):
        if str(path) == str(root / "v1"):
            raise OSError("busy")
        return real_rmdir(path, *args, **kwargs)

    monkeypatch.setattr(sweeper_mod.os, "unlink", flaky_unlink)
    monkeypatch.setattr(sweeper_mod.os, "rmdir", flaky_rmdir)
    result = _sweeper(root, versions_keep=0, max_entries=1).sweep_once()
    assert first.exists()  # unlink refused -> skipped, next oldest evicted instead
    assert not (current / "b.png").exists()
    assert result.versions_removed == 0  # rmdir refused
    assert result.evicted_entries == 1


def test_unlink_of_vanished_file_is_not_counted(tmp_path: Path) -> None:
    assert MirrorSweeper._unlink(tmp_path / "gone") is False


def test_remove_tree_of_unreadable_dir_reports_failure(tmp_path: Path) -> None:
    assert _sweeper(tmp_path)._remove_tree(tmp_path / "missing") is False


def test_scandir_errors_during_collection_are_skipped(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    current = root / "v3"
    _write(current / "ok" / "a.png", 5)
    _write(current / "bad" / "b.png", 5)
    real_scandir = os.scandir

    def flaky_scandir(path="."):
        if str(path).endswith("bad"):
            raise PermissionError("denied")
        return real_scandir(path)

    monkeypatch.setattr(sweeper_mod.os, "scandir", flaky_scandir)
    files = _sweeper(root)._collect_files(current)
    assert [Path(p).name for _, _, p in files] == ["a.png"]


class _BrokenEntry:
    def __init__(self, name: str, path: str) -> None:
        self.name = name
        self.path = path

    def is_dir(self, *, follow_symlinks: bool = True) -> bool:
        raise OSError("stat failed")

    def is_file(self, *, follow_symlinks: bool = True) -> bool:
        raise OSError("stat failed")


class _FakeScandir:
    def __init__(self, entries) -> None:
        self._entries = entries

    def __enter__(self):
        return iter(self._entries)

    def __exit__(self, *exc) -> None:
        return None


def test_entry_stat_errors_are_skipped(root: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (root / ".tmp").mkdir()
    broken = _BrokenEntry("x", str(root / "x"))
    monkeypatch.setattr(sweeper_mod.os, "scandir", lambda _path=".": _FakeScandir([broken]))
    sweeper = _sweeper(root)
    assert sweeper._remove_old_versions("v3") == 0
    assert sweeper._sweep_tmp() == set()
    assert sweeper._collect_files(root) == []


def test_remove_tree_treats_unstatable_child_as_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    target = tmp_path / "v0"
    target.mkdir()
    broken = _BrokenEntry("child", str(target / "child"))
    real_scandir = os.scandir

    def fake_scandir(path="."):
        if Path(path) == target:
            return _FakeScandir([broken])
        return real_scandir(path)

    monkeypatch.setattr(sweeper_mod.os, "scandir", fake_scandir)
    assert _sweeper(tmp_path)._remove_tree(target) is True
    assert not target.exists()
