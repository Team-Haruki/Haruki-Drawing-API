"""Process-level caches for the custom profile renderer.

Every request used to build a fresh ``PNGRenderer`` whose glyph/SDF/sprite/font caches were
instance attributes, so the entire cold cost (fontTools re-parsing ~104ms per glyph, ``load_font``
reopening font files hundreds of times, TMP metadata re-parsed from JSON) was paid again on every
render. The pools here survive across requests; the renderer keeps its per-request dicts as a
lock-free L1 in front of them.

Design rules (docs/custom-profile-skia-feasibility.md Phase 0):
- Keys carry file signatures ``(mtime_ns, size)`` for every file the cached value was derived
  from, so an asset replaced in place by the asset updater invalidates the entry (CLAUDE.md
  cache-key rule). The TMP metadata cache records a signature for *every* file touched during the
  parse — metadata.json alone does not cover the character/glyph tables it references.
- Locks only guard dict access, never computation. Concurrent misses on one key duplicate work
  and last-write-wins; values are immutable so this is harmless.
- Values are shared across threads without copying, so they must be effectively immutable
  (frozen dataclasses, fully-materialized PIL images, numpy arrays with ``writeable=False``).
- PIL ``FreeTypeFont`` objects are cached per-thread, never shared: Pillow serializes access with
  a per-object critical section, measured at a 4-5x throughput loss on the free-threaded build
  when one object is shared across the pool (see ``skia_renderer.ir_builder.get_pil_font``).

This module must stay light to import (no numpy/cv2/fontTools/renderer imports): it is lazily
imported by ``src.sekai.base.utils.get_runtime_cache_stats`` for /cache/stats.
"""

from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable
import os
from pathlib import Path
import threading
from typing import Any

from src.settings import (
    CUSTOM_PROFILE_GLYPH_CACHE_MAX_BYTES,
    CUSTOM_PROFILE_GLYPH_CACHE_SIZE,
    CUSTOM_PROFILE_SPRITE_CACHE_MAX_BYTES,
    CUSTOM_PROFILE_SPRITE_CACHE_SIZE,
)

FileSignature = tuple[int, int]
# Recorded for probed-but-absent paths: a table/font that *arrives* later must invalidate the
# entry just like a replaced one (the "placeholder cached until TTL" failure mode in CLAUDE.md).
MISSING_FILE = (-1, -1)

#: Sentinel distinguishing "not cached" from a cached ``None``. BoundedCache supports caching
#: ``None``, but the glyph pools deliberately store only SUCCESSFUL renders: their None verdicts
#: come from broad except blocks, and a transient failure cached under an unchanged file
#: signature would poison the glyph for the process lifetime (negatives stay in the renderer's
#: per-request L1 instead).
MISSING: Any = object()


def file_signature(path: Path | str) -> FileSignature:
    """``(st_mtime_ns, st_size)`` of ``path``; raises ``OSError`` when it does not exist."""
    st = os.stat(path)
    return (st.st_mtime_ns, st.st_size)


def optional_file_signature(path: Path | str) -> FileSignature:
    """Like :func:`file_signature` but maps a missing path to :data:`MISSING_FILE`."""
    try:
        return file_signature(path)
    except OSError:
        return MISSING_FILE


class BoundedCache:
    """LRU cache bounded by entry count and estimated bytes, with /cache/stats counters.

    Not ``_TTLImageCache``: no TTL (invalidation is by file signature in the key), no
    copy-on-get (values are shared immutables), no ``close()`` of evicted values.
    """

    def __init__(self, name: str, max_entries: int, max_bytes: int, estimate: Callable[[Any], int]) -> None:
        self.name = name
        self.max_entries = max_entries
        self.max_bytes = max_bytes
        self._estimate = estimate
        self._lock = threading.RLock()
        self._data: OrderedDict[Any, tuple[Any, int]] = OrderedDict()
        self._bytes = 0
        self._hits = 0
        self._misses = 0
        self._sets = 0
        self._evictions = 0

    @property
    def enabled(self) -> bool:
        return self.max_entries > 0 and self.max_bytes > 0

    def get(self, key: Any) -> Any:
        """The cached value, or :data:`MISSING`. A cached ``None`` is a hit."""
        if not self.enabled:
            return MISSING
        with self._lock:
            if key in self._data:
                value, _ = self._data[key]
                self._data.move_to_end(key)
                self._hits += 1
                return value
            self._misses += 1
            return MISSING

    def set(self, key: Any, value: Any) -> None:
        if not self.enabled:
            return
        size = max(0, int(self._estimate(value)))
        if size > self.max_bytes:
            return  # would evict the whole pool to hold one entry
        with self._lock:
            old = self._data.pop(key, None)
            if old is not None:
                self._bytes -= old[1]
            self._data[key] = (value, size)
            self._bytes += size
            self._sets += 1
            while self._data and (len(self._data) > self.max_entries or self._bytes > self.max_bytes):
                _, (_, evicted_size) = self._data.popitem(last=False)
                self._bytes -= evicted_size
                self._evictions += 1

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
            self._bytes = 0

    def stats(self) -> dict[str, Any]:
        # Same key shape as src.sekai.base.utils._build_shared_cache_stats (not imported to keep
        # this module lazily importable from there without a cycle).
        with self._lock:
            hits, misses = self._hits, self._misses
            total = hits + misses
            return {
                "enabled": self.enabled,
                "entries": len(self._data),
                "max_entries": self.max_entries,
                "bytes": self._bytes,
                "max_bytes": self.max_bytes,
                "hits": hits,
                "misses": misses,
                "sets": self._sets,
                "evictions": self._evictions,
                "hit_rate": (hits / total) if total > 0 else None,
            }


def _glyph_sdf_bytes(value: Any) -> int:
    """``TMPDynamicGlyphSDF | None``: the L-mode field dominates (~12KB typical glyph)."""
    if value is None:
        return 64
    field = value.field
    return field.width * field.height + 256


def _contours_bytes(value: Any) -> int:
    """``tuple[list[np.ndarray], module] | None`` from ``tmp_vector_glyph_contours``."""
    if value is None:
        return 64
    packed, _np = value
    return sum(int(arr.nbytes) for arr in packed) + 128


def _image_bytes(value: Any) -> int:
    """``PIL.Image.Image | None`` (RGBA sprites, L-mode atlas alphas)."""
    if value is None:
        return 64
    return value.width * value.height * len(value.mode)


GLYPH_SDF_CACHE = BoundedCache(
    "glyph_sdf", CUSTOM_PROFILE_GLYPH_CACHE_SIZE, CUSTOM_PROFILE_GLYPH_CACHE_MAX_BYTES, _glyph_sdf_bytes
)
GLYPH_CONTOUR_CACHE = BoundedCache(
    "glyph_contours", CUSTOM_PROFILE_GLYPH_CACHE_SIZE, CUSTOM_PROFILE_GLYPH_CACHE_MAX_BYTES, _contours_bytes
)
SPRITE_ATLAS_CACHE = BoundedCache(
    "sprite_atlas", CUSTOM_PROFILE_SPRITE_CACHE_SIZE, CUSTOM_PROFILE_SPRITE_CACHE_MAX_BYTES, _image_bytes
)


# ---- TMP metadata table cache -----------------------------------------------------------------
#
# Caches the *parsed asset tables* from TMPFontLibrary.load, never the library object: the
# library carries per-instance mutable state (lazy TTFont handles, metric memos) that is not
# thread-safe to share. Each request builds a fresh library around the shared tables.
#
# The value's signature list covers every path the parse touched (recorded via the ``record``
# callback threaded through TMPFontLibrary._load_assets), including probed-but-missing
# character/glyph tables and source-font candidates, plus the atlases directory (its glob result
# is baked into the tables; a directory's mtime_ns moves when entries are added or removed).
# A hit re-stats the list (~tens of µs); any mismatch reparses.

_TMP_METADATA_CACHE_MAX = 8
_tmp_metadata_lock = threading.RLock()
_tmp_metadata_cache: OrderedDict[
    tuple[str, str],
    tuple[Any, Any, list[tuple[str, FileSignature]]],
] = OrderedDict()
_tmp_metadata_hits = 0
_tmp_metadata_misses = 0
_tmp_metadata_sets = 0


def get_tmp_font_tables(
    metadata_path: Path,
    source_metadata_path: Path | None,
    loader: Callable[[Callable[[Path | str], None]], tuple[Any, Any]],
) -> tuple[Any, Any]:
    """Shared parsed TMP font tables for ``(metadata_path, source_metadata_path)``.

    ``loader(record)`` performs the actual parse, calling ``record(path)`` for every file it
    reads or probes; it returns ``(assets, source_assets_or_None)``. The parse runs outside the
    lock — concurrent misses duplicate work, last write wins.
    """
    global _tmp_metadata_hits, _tmp_metadata_misses, _tmp_metadata_sets
    key = (str(metadata_path), str(source_metadata_path) if source_metadata_path is not None else "")
    with _tmp_metadata_lock:
        entry = _tmp_metadata_cache.get(key)
        if entry is not None:
            _tmp_metadata_cache.move_to_end(key)
    if entry is not None:
        assets, source_assets, signatures = entry
        if all(optional_file_signature(path) == sig for path, sig in signatures):
            with _tmp_metadata_lock:
                _tmp_metadata_hits += 1
            return assets, source_assets

    signatures: list[tuple[str, FileSignature]] = []
    seen: set[str] = set()

    def record(path: Path | str) -> None:
        text = str(path)
        if text not in seen:
            seen.add(text)
            signatures.append((text, optional_file_signature(path)))

    assets, source_assets = loader(record)
    with _tmp_metadata_lock:
        _tmp_metadata_misses += 1
        _tmp_metadata_sets += 1
        _tmp_metadata_cache[key] = (assets, source_assets, signatures)
        _tmp_metadata_cache.move_to_end(key)
        while len(_tmp_metadata_cache) > _TMP_METADATA_CACHE_MAX:
            _tmp_metadata_cache.popitem(last=False)
    return assets, source_assets


# ---- per-thread render font cache -------------------------------------------------------------
#
# The pattern (and the measurement forbidding a shared cache) is get_pil_font in
# skia_renderer.ir_builder; not reused directly because resolution semantics differ: custom
# profile fonts are absolute paths that must raise on a missing file (no extension probing, no
# load_default fallback), and the key needs the file signature (the font dirs live under the
# asset-updater's tree).

_RENDER_FONT_CACHE_MAX = 256
_render_font_tls = threading.local()


def _thread_render_font_cache() -> OrderedDict[tuple[str, int, int, int], Any]:
    cache = getattr(_render_font_tls, "cache", None)
    if cache is None:
        cache = OrderedDict()
        _render_font_tls.cache = cache
    return cache


def get_render_font(path: Path, size: float) -> Any:
    """Bounded per-thread replacement for ``ImageFont.truetype(str(path), max(1, round(size)))``."""
    from PIL import ImageFont

    px = max(1, round(size))
    try:
        mtime_ns, fsize = file_signature(path)
    except OSError:
        # Missing file: keep load_font's historical behavior (truetype raises), cache nothing.
        return ImageFont.truetype(str(path), px)
    key = (str(path), mtime_ns, fsize, px)
    cache = _thread_render_font_cache()  # no lock: owned by this thread
    font = cache.get(key)
    if font is not None:
        cache.move_to_end(key)
        return font
    font = ImageFont.truetype(str(path), px)
    cache[key] = font
    while len(cache) > _RENDER_FONT_CACHE_MAX:
        cache.popitem(last=False)
    return font


# ---- stats / lifecycle ------------------------------------------------------------------------


def get_custom_profile_cache_stats() -> dict[str, Any]:
    """Per-pool stats for /cache/stats (the ``custom_profile_caches`` key)."""
    with _tmp_metadata_lock:
        meta_hits, meta_misses = _tmp_metadata_hits, _tmp_metadata_misses
        meta_total = meta_hits + meta_misses
        tmp_metadata = {
            "enabled": True,
            "entries": len(_tmp_metadata_cache),
            "max_entries": _TMP_METADATA_CACHE_MAX,
            "bytes": 0,  # parsed tables; not separately estimated
            "max_bytes": 0,
            "hits": meta_hits,
            "misses": meta_misses,
            "sets": _tmp_metadata_sets,
            "evictions": 0,
            "hit_rate": (meta_hits / meta_total) if meta_total > 0 else None,
        }
    return {
        "tmp_metadata": tmp_metadata,
        "glyph_sdf": GLYPH_SDF_CACHE.stats(),
        "glyph_contours": GLYPH_CONTOUR_CACHE.stats(),
        "sprite_atlas": SPRITE_ATLAS_CACHE.stats(),
    }


def clear_custom_profile_caches() -> None:
    """Drop every process-level pool (tests; per-thread font caches clear on thread death)."""
    global _tmp_metadata_hits, _tmp_metadata_misses, _tmp_metadata_sets
    GLYPH_SDF_CACHE.clear()
    GLYPH_CONTOUR_CACHE.clear()
    SPRITE_ATLAS_CACHE.clear()
    with _tmp_metadata_lock:
        _tmp_metadata_cache.clear()
        _tmp_metadata_hits = 0
        _tmp_metadata_misses = 0
        _tmp_metadata_sets = 0
    cache = getattr(_render_font_tls, "cache", None)
    if cache is not None:
        cache.clear()
