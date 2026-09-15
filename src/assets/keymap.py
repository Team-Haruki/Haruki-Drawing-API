"""Logical asset key -> bucket object key and versioned mirror layout (C3, E5).

This module is THE single `asset/`-strip point of this repo (enforced by `tests/test_single_strip_point.py`).
Cloud keeps sending `asset/<region>-assets/<mode>/<rel>`; the bucket object key drops only the leading
`asset/` (provider `root` is "" on the assets slot, addendum A9(7)), so the key is
`<region>-assets/<mode>/<rel>`. The on-disk mirror lives under ONE base:
`<ASSETS_BASE_DIR>/<mirror_dir>/<version>/<object_key>`.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import PurePosixPath
import re
import threading

from src.storage.protocols import validate_object_key

logger = logging.getLogger("src.assets.keymap")

DEFAULT_VERSION = "v0"

_LOGICAL = re.compile(r"^asset/(?P<region>[a-z]{2})-assets/(?P<mode>startapp|ondemand)/(?P<rel>[^\0]+)$")
# A key that *looks* like a bucket asset (prefix matches) but fails the Rust key syntax is "malformed" and
# is WARNed once; anything else that does not match (static_images/, fonts, custom_profile/, tmp/, ...) is
# simply not a bucket asset and returns None silently.
_ASSET_PREFIX = re.compile(r"^asset/[a-z]{2}-assets/(?:startapp|ondemand)/")
_VERSION = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
_WARNED_MAX = 4096


def sanitize_version(version: object) -> str:
    """Return `version` stripped when it matches `[A-Za-z0-9._-]{1,64}` (and is not `.`/`..`), else `"v0"`."""
    if not isinstance(version, str):
        return DEFAULT_VERSION
    candidate = version.strip()
    if not _VERSION.match(candidate) or candidate in (".", ".."):
        return DEFAULT_VERSION
    return candidate


@dataclass(frozen=True, slots=True)
class MappedAsset:
    region: str
    mode: str  # "startapp" | "ondemand"
    rel: str  # e.g. "music/music_score/0001_01/expert.txt"
    object_key: str  # f"{region}-assets/{mode}/{rel}" -- provider root is "" (addendum A9(7))
    mirror_rel: PurePosixPath  # f"{mirror_dir}/{version}/{region}-assets/{mode}/{rel}", relative to ASSETS_BASE_DIR
    legacy_rel: PurePosixPath  # f"asset/{region}-assets/{mode}/{rel}" -- today's on-disk path


class AssetKeyMap:
    """Immutable mapping for one (mirror_dir, version) pair; a version bump builds a new map."""

    __slots__ = ("_mirror_base", "_warn_lock", "_warned", "mirror_dir", "version")

    def __init__(self, *, mirror_dir: str, version: str) -> None:
        normalised = str(mirror_dir).replace("\\", "/").strip()
        path = PurePosixPath(normalised)
        if not normalised or path.is_absolute() or ".." in path.parts:
            raise ValueError(f"mirror_dir must be a non-empty relative path without '..': {mirror_dir!r}")
        self.mirror_dir = path.as_posix()
        self.version = sanitize_version(version)
        self._mirror_base = PurePosixPath(self.mirror_dir) / self.version
        self._warned: set[str] = set()
        self._warn_lock = threading.Lock()

    def map(self, logical: str) -> MappedAsset | None:
        """Map a logical key; `None` means "not a bucket asset -> resolve locally, never fetch"."""
        if not isinstance(logical, str):
            return None
        match = _LOGICAL.match(logical)
        if match is None:
            if _ASSET_PREFIX.match(logical):
                self._warn_malformed(logical)
            return None
        object_key = logical.removeprefix("asset/")  # the single strip point (C3); see module docstring
        try:
            validate_object_key(object_key)
        except ValueError:
            self._warn_malformed(logical)
            return None
        return MappedAsset(
            region=match["region"],
            mode=match["mode"],
            rel=match["rel"],
            object_key=object_key,
            mirror_rel=self._mirror_base / object_key,
            legacy_rel=PurePosixPath(logical),
        )

    def _warn_malformed(self, logical: str) -> None:
        with self._warn_lock:
            if logical in self._warned:
                return
            if len(self._warned) >= _WARNED_MAX:
                self._warned.clear()
            self._warned.add(logical)
        logger.warning("asset_mirror.malformed_key key=%r (not fetched; resolved locally)", logical)
