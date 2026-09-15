"""Mirror sweeper: bound the on-disk asset mirror (plan §4.4).

One `sweep_once()` does, in order:

1. delete version directories other than the current one beyond `versions_keep` (oldest dir mtime first);
2. delete `<root>/.tmp/*` older than `tmp_max_age_seconds`;
3. when `max_bytes > 0` or `max_entries > 0`, walk the current version directory and unlink the oldest
   `st_atime_ns` first until it is under both caps (atime is coarse under `relatime`; mtime is never touched
   because it is part of the asset identity). A file whose in-flight `.tmp` publish is younger than 60 s is
   skipped;
4. prune empty directories.

The walk never follows a symlink: a symlink is an entry that is unlinked (never its target) or skipped, so the
sweeper cannot touch anything outside `root`. `sweep_once` is sync (the periodic task runs it on a pool thread)
and never raises.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
import hashlib
import logging
import os
from pathlib import Path, PurePosixPath
import stat as stat_mod
import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from src.assets.mirror import MirrorStats

logger = logging.getLogger("src.assets.sweeper")

_TMP_DIR = ".tmp"
_INFLIGHT_TMP_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class SweepResult:
    versions_removed: int
    evicted_entries: int
    evicted_bytes: int
    entries: int
    bytes: int


def _lstat(path: Path | str) -> os.stat_result | None:
    try:
        return os.lstat(path)
    except OSError:
        return None


class MirrorSweeper:
    """Enforce the version, `.tmp` and byte/entry caps of one mirror root."""

    def __init__(
        self,
        *,
        root: Path,
        current_version: Callable[[], str],
        max_bytes: int,
        max_entries: int,
        versions_keep: int,
        tmp_max_age_seconds: int,
        stats: MirrorStats,
        mirror_dir: str | None = None,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._root = Path(root)
        self._current_version = current_version
        self._max_bytes = max(0, int(max_bytes))
        self._max_entries = max(0, int(max_entries))
        self._versions_keep = max(0, int(versions_keep))
        self._tmp_max_age = max(0.0, float(tmp_max_age_seconds))
        self._stats = stats
        # The `.tmp` name digest is sha256 of `<mirror_dir>/<version>/<object_key>` (see `AssetMirror._publish`).
        self._mirror_dir = mirror_dir if mirror_dir is not None else self._root.name
        self._clock = clock

    @property
    def root(self) -> Path:
        return self._root

    def sweep_once(self) -> SweepResult:
        """Run one sweep; any failure is logged and yields the partial result. Never raises."""
        versions_removed = evicted_entries = evicted_bytes = entries = total_bytes = 0
        try:
            root_st = _lstat(self._root)
            if root_st is None or not stat_mod.S_ISDIR(root_st.st_mode):
                return SweepResult(0, 0, 0, 0, 0)
            current = str(self._current_version())
            versions_removed = self._remove_old_versions(current)
            young_tmp = self._sweep_tmp()
            current_dir = self._root / current
            cur_st = _lstat(current_dir)
            if cur_st is not None and stat_mod.S_ISDIR(cur_st.st_mode) and current not in ("", ".", "..", _TMP_DIR):
                files = self._collect_files(current_dir)
                entries = len(files)
                total_bytes = sum(size for _, size, _ in files)
                if self._max_bytes > 0 or self._max_entries > 0:
                    evicted_entries, evicted_bytes = self._evict(current, current_dir, files, young_tmp)
                    entries -= evicted_entries
                    total_bytes -= evicted_bytes
                self._prune_empty_dirs(current_dir)
        except Exception:
            logger.warning("mirror.sweep_failed root=%s", self._root, exc_info=True)
        result = SweepResult(versions_removed, evicted_entries, evicted_bytes, entries, total_bytes)
        self._record(result)
        return result

    # ------------------------------------------------------------------ steps
    def _remove_old_versions(self, current: str) -> int:
        candidates: list[tuple[int, str, Path]] = []
        with os.scandir(self._root) as it:
            for entry in it:
                if entry.name in (current, _TMP_DIR) or entry.name.startswith("."):
                    continue
                try:
                    if not entry.is_dir(follow_symlinks=False):
                        continue
                    mtime = entry.stat(follow_symlinks=False).st_mtime_ns
                except OSError:
                    continue
                candidates.append((mtime, entry.name, Path(entry.path)))
        candidates.sort(reverse=True)  # newest first; the first `versions_keep` survive
        removed = 0
        for _, name, path in reversed(candidates[self._versions_keep :]):  # oldest first
            if self._remove_tree(path):
                removed += 1
                logger.info("mirror.version_removed root=%s version=%s", self._root, name)
        return removed

    def _sweep_tmp(self) -> set[str]:
        """Delete stale `.tmp` files; return the digests of publishes still in flight (young tmp files)."""
        tmp_dir = self._root / _TMP_DIR
        st = _lstat(tmp_dir)
        young: set[str] = set()
        if st is None or not stat_mod.S_ISDIR(st.st_mode):
            return young
        now = self._clock()
        with os.scandir(tmp_dir) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False):
                        continue
                    age = now - entry.stat(follow_symlinks=False).st_mtime
                except OSError:
                    continue
                if age > self._tmp_max_age:
                    self._unlink(entry.path)
                    continue
                if age < _INFLIGHT_TMP_SECONDS:
                    young.add(entry.name.split(".", 1)[0])
        return young

    def _collect_files(self, directory: Path) -> list[tuple[int, int, str]]:
        """`(atime_ns, size, path)` for every regular file under `directory`, symlinks not followed."""
        files: list[tuple[int, int, str]] = []
        stack = [str(directory)]
        while stack:
            current = stack.pop()
            try:
                with os.scandir(current) as it:
                    for entry in it:
                        try:
                            if entry.is_dir(follow_symlinks=False):
                                stack.append(entry.path)
                            elif entry.is_file(follow_symlinks=False):
                                st = entry.stat(follow_symlinks=False)
                                files.append((st.st_atime_ns, st.st_size, entry.path))
                        except OSError:
                            continue
            except OSError:
                continue
        return files

    def _evict(
        self, current: str, current_dir: Path, files: list[tuple[int, int, str]], young_tmp: set[str]
    ) -> tuple[int, int]:
        entries = len(files)
        total = sum(size for _, size, _ in files)
        evicted_entries = evicted_bytes = 0
        for _, size, path in sorted(files):
            over_bytes = self._max_bytes > 0 and total > self._max_bytes
            over_entries = self._max_entries > 0 and entries > self._max_entries
            if not (over_bytes or over_entries):
                break
            if young_tmp and self._tmp_digest(current, current_dir, path) in young_tmp:
                continue
            if self._unlink(path):
                entries -= 1
                total -= size
                evicted_entries += 1
                evicted_bytes += size
        return evicted_entries, evicted_bytes

    def _tmp_digest(self, current: str, current_dir: Path, path: str) -> str:
        rel = Path(path).relative_to(current_dir).as_posix()
        mirror_rel = PurePosixPath(self._mirror_dir) / current / rel
        return hashlib.sha256(str(mirror_rel).encode("utf-8")).hexdigest()[:16]

    @staticmethod
    def _prune_empty_dirs(directory: Path) -> None:
        """Remove empty sub-directories bottom-up (the version directory itself is kept).

        `os.rmdir` refuses a non-empty directory and a symlink, so neither is ever touched.
        """
        for dirpath, _dirnames, _filenames in os.walk(directory, topdown=False, followlinks=False):
            if Path(dirpath) == directory:
                continue
            try:
                os.rmdir(dirpath)
            except OSError:
                pass

    # ------------------------------------------------------------------ primitives
    def _remove_tree(self, path: Path) -> bool:
        """Delete `path` recursively without following any symlink; True when the directory is gone."""
        try:
            with os.scandir(path) as it:
                children = list(it)
        except OSError:
            return False
        for entry in children:
            try:
                is_dir = entry.is_dir(follow_symlinks=False)
            except OSError:
                is_dir = False
            if is_dir:
                self._remove_tree(Path(entry.path))
            else:
                self._unlink(entry.path)
        try:
            os.rmdir(path)
        except OSError:
            return False
        return True

    @staticmethod
    def _unlink(path: str | Path) -> bool:
        try:
            os.unlink(path)
        except FileNotFoundError:
            return False
        except OSError:
            logger.debug("mirror.sweep_unlink_failed path=%s", path, exc_info=True)
            return False
        return True

    def _record(self, result: SweepResult) -> None:
        try:
            stats = self._stats
            with stats._lock:
                stats.sweeps += 1
                stats.versions_removed += result.versions_removed
                stats.evicted_entries += result.evicted_entries
                stats.evicted_bytes += result.evicted_bytes
                stats.dir_entries = result.entries
                stats.dir_bytes = result.bytes
        except Exception:
            logger.warning("mirror.sweep_stats_failed", exc_info=True)
