"""Identity of process-loaded renderer code, native pixels, fonts and local templates.

The epoch has two parts:

- A process-lifetime part: Python source, the native renderer, fonts, custom-profile
  material and pixel-affecting settings. These are loaded once by the renderer, so
  replacing them requires a restart and they are scanned once.
- The local static tree (``assets.base_dir / assets.result_asset_path``). Image
  loads key their caches on ``(path, mtime_ns, size)``, so a file replaced on disk
  is picked up without a restart. The epoch follows: at most every
  ``_STATIC_RECHECK_SECONDS`` a cheap stat walk is compared with the previous one
  and the tree is re-hashed only when it changed. The digest is content-based, so
  nodes holding identical files agree regardless of mtimes.

Only files that can affect pixels count towards the static part (see
``_static_entries``): image/stylesheet extensions outside scratch directories.
Collaborators can therefore keep candidates, manifests and audio next to the real
sprites without invalidating every render cache entry. Remote game assets are
covered by Cloud's Asset-Revision instead.
"""

from __future__ import annotations

from functools import lru_cache
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import threading
import time
from typing import Any

_PIXEL_ENV = (
    "HARUKI_SKIA_PNG_ENCODER",
    "HARUKI_SKIA_TEXT_HINTING",
    "HARUKI_SKIA_TEXT_GAMMA",
    "HARUKI_SKIA_RASTER_CACHE_OVERSAMPLE",
    "HARUKI_BG_TEST_HOUR",
)
_PIXEL_SETTINGS = (
    "export_image_format",
    "jpg_quality",
    "jpg_subsampling",
    "use_skia_plot",
    "custom_profile_max_elements",
    "custom_profile_max_scale",
    "custom_profile_max_text_size",
    "custom_profile_max_text_length",
    "custom_profile_max_layer_pixels",
    "custom_profile_max_scene_mb",
)

# Extensions the renderer can read from the static tree: decoded images, plus the
# chart stylesheets. Manifests, archives and audio never reach a canvas.
_STATIC_PIXEL_SUFFIXES = frozenset({".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp", ".svg", ".css"})
# Scratch material lives in directories named ``*-candidate`` (or hidden / ``_``-prefixed).
_SCRATCH_DIR_SUFFIX = "-candidate"
_STATIC_RECHECK_SECONDS = 10.0


def _file(digest: Any, label: str, path: Path) -> None:
    digest.update(label.encode())
    digest.update(b"\0")
    if not path.is_file():
        digest.update(b"missing\0")
        return
    content = hashlib.sha256()
    with path.open("rb") as stream:
        while chunk := stream.read(1024 * 1024):
            content.update(chunk)
    digest.update(content.digest())


def _tree(digest: Any, label: str, root: Path) -> None:
    digest.update(label.encode())
    if not root.exists():
        digest.update(b"missing\0")
        return
    if root.is_file():
        _file(digest, label, root)
        return
    for path in sorted(root.rglob("*")):
        if path.is_file():
            _file(digest, str(path.relative_to(root)), path)


def _is_scratch_dir(name: str) -> bool:
    return name.startswith((".", "_")) or name.endswith(_SCRATCH_DIR_SUFFIX)


def _static_entries(root: Path) -> list[tuple[str, Path, int, int]]:
    """Pixel-relevant files under ``root`` as ``(relative, path, size, mtime_ns)``, sorted."""
    entries: list[tuple[str, Path, int, int]] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if not _is_scratch_dir(name)]
        for name in filenames:
            if name.startswith(".") or Path(name).suffix.lower() not in _STATIC_PIXEL_SUFFIXES:
                continue
            path = Path(dirpath) / name
            try:
                stat = path.stat()
            except OSError:
                continue
            entries.append((path.relative_to(root).as_posix(), path, stat.st_size, stat.st_mtime_ns))
    entries.sort()
    return entries


def _static_marker(entries: list[tuple[str, Path, int, int]]) -> str:
    marker = hashlib.sha256()
    for relative, _path, size, mtime_ns in entries:
        marker.update(f"{relative}\0{size}\0{mtime_ns}\n".encode())
    return marker.hexdigest()


def _static_digest(entries: list[tuple[str, Path, int, int]]) -> str:
    digest = hashlib.sha256(b"static")
    for relative, path, _size, _mtime_ns in entries:
        _file(digest, relative, path)
    return digest.hexdigest()


@lru_cache(maxsize=1)
def _process_epoch() -> str | None:
    from src.sekai.base.utils import renderer_code_fingerprint
    from src.settings import settings

    source = renderer_code_fingerprint()
    if source == "unknown":
        return None
    spec = importlib.util.find_spec("haruki_skia_renderer")
    if spec is None or spec.origin is None:
        return None
    origin = Path(spec.origin)
    binaries = sorted(origin.parent.glob("*.so")) if origin.suffix == ".py" else [origin]
    if not binaries:
        return None
    digest = hashlib.sha256(source.encode())
    try:
        for binary in binaries:
            if not binary.is_file():
                return None
            _file(digest, binary.name, binary)
        for role in ("default", "bold", "heavy", "emoji"):
            name = getattr(settings.font, role)
            # Match native resolution candidates, including absent alternatives.
            for suffix in (".otf", ".ttf", ".ttc", ""):
                _file(digest, f"font:{role}:{suffix}", settings.font.dir / (name + suffix))
        for setting in (
            "custom_profile_assets_dir",
            "custom_profile_fonts_dir",
            "custom_profile_tmp_font_metadata",
            "custom_profile_shape_sprite_dir",
            "custom_profile_unity_ui_sprite_dir",
        ):
            path = getattr(settings.drawing, setting)
            # Region-templated game assets are covered by Cloud's asset revision.
            if path is not None and "{region}" not in str(path):
                _tree(digest, setting, path)
    except OSError:
        return None
    material = {
        "drawing": {name: getattr(settings.drawing, name) for name in _PIXEL_SETTINGS},
        "native": {name: os.environ.get(name, "") for name in _PIXEL_ENV},
    }
    digest.update(json.dumps(material, sort_keys=True, separators=(",", ":")).encode())
    return digest.hexdigest()


class _StaticEpoch:
    """Content digest of the static tree, refreshed when a cheap stat walk changes."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._checked_at = float("-inf")
        self._marker: str | None = None
        self._digest: str | None = None

    def clear(self) -> None:
        with self._lock:
            self._checked_at = float("-inf")
            self._marker = None
            self._digest = None

    def get(self, root: Path) -> str | None:
        now = time.monotonic()
        with self._lock:
            if now - self._checked_at < _STATIC_RECHECK_SECONDS and self._digest is not None:
                return self._digest
            try:
                if not root.exists():
                    entries: list[tuple[str, Path, int, int]] = []
                else:
                    entries = _static_entries(root)
                marker = _static_marker(entries)
                if marker != self._marker or self._digest is None:
                    self._digest = _static_digest(entries) if root.exists() else hashlib.sha256(b"missing").hexdigest()
                    self._marker = marker
            except OSError:
                return None
            self._checked_at = now
            return self._digest


_static_epoch = _StaticEpoch()


def renderer_epoch() -> str | None:
    from src.settings import settings

    process = _process_epoch()
    if process is None:
        return None
    static = _static_epoch.get(settings.assets.base_dir / settings.assets.result_asset_path)
    if static is None:
        return None
    return hashlib.sha256(f"{process}\0{static}".encode()).hexdigest()


def _cache_clear() -> None:
    _process_epoch.cache_clear()
    _static_epoch.clear()


renderer_epoch.cache_clear = _cache_clear  # type: ignore[attr-defined]
