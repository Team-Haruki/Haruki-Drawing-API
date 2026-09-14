from __future__ import annotations

import asyncio
from collections import OrderedDict
import contextvars
from datetime import datetime, timedelta
from functools import lru_cache
import hashlib
import io
import json
import logging
import os
from pathlib import Path
from stat import S_ISREG
import threading
import time
from typing import TYPE_CHECKING, Any, Literal
from uuid import uuid4

if TYPE_CHECKING:
    from PIL import Image

from src.core.debug import current_request_context, snapshot_process_metrics
from src.core.missing_asset_telemetry import (
    MISSING_ASSET_REASONS as MISSING_ASSET_REASONS,
    MISSING_BIRTHDAY_FALLBACK,
    MISSING_CANDIDATES_EXHAUSTED,
    MISSING_EMPTY_PATH,
    MISSING_LOCAL_NOT_FOUND,
    MISSING_VANISHED,
    begin_missing_asset_scope as begin_missing_asset_scope,
    current_missing_asset_count as current_missing_asset_count,
    end_missing_asset_scope as end_missing_asset_scope,
    get_missing_asset_stats,
    record_missing_asset,
    reset_missing_asset_stats as reset_missing_asset_stats,
)
from src.core.pillow_telemetry import (
    PILLOW_TOUCH_IMAGE_DECODE,
    PILLOW_TOUCH_PLACEHOLDER,
    record_pillow_touch,
)
from src.sekai.base.asset_key import AssetKey, candidates, first_candidate
from src.sekai.base.image_info import probe_asset, probe_encoded
from src.sekai.base.image_source import (
    AssetImageRef,
    EncodedImageRef,
    ImageSource as ImageSource,
    MissingImageRef,
    _resolved_existing_cache as _resolved_existing_cache,
    get_pristine_image_asset_path as get_pristine_image_asset_path,
    missing_image_ref,
    resolve_existing_asset_path as resolve_existing_asset_path,
)
from src.sekai.base.paint_types import RasterResample
from src.sekai.base.placeholder import placeholder_variant as _guess_missing_placeholder_variant
from src.settings import (
    ASSETS_BASE_DIR,
    COMPOSED_IMAGE_CACHE_MAX_BYTES,
    COMPOSED_IMAGE_CACHE_SIZE,
    COMPOSED_IMAGE_CACHE_TTL_SECONDS,
    DEFAULT_THREAD_POOL_SIZE,
    IMAGE_CACHE_MAX_BYTES,
    IMAGE_CACHE_SIZE,
    THUMB_CACHE_MAX_BYTES,
    THUMB_CACHE_SIZE,
    TMP_PATH,
)

logger = logging.getLogger(__name__)
_EMPTY_IMAGE_PATH_MESSAGE = "图片路径不能为空(None)"

MissingImageMode = Literal["raise", "placeholder"]
_PRISTINE_ASSET_PATH_ATTR = "_haruki_pristine_asset_path"


def get_encoded_image_ref(data: bytes) -> EncodedImageRef:
    """Wrap encoded image bytes into an :class:`EncodedImageRef` (header probe only)."""
    size, mode = probe_encoded(data)
    return EncodedImageRef(data=data, size=size, mode=mode)


# Painter resizes a decoded PIL image with a bare ``Image.resize(size)``, whose Pillow
# default is BICUBIC. A ref-backed paste must resample identically or the same widget
# tree renders softer just because its source stayed lazy. The resize cache keys on the
# filter too, so this never collides with ``get_img_resized``'s BILINEAR/LANCZOS entries.
PASTE_RESAMPLE = RasterResample.BICUBIC


def _mark_pristine_asset_image(image: Image.Image, full_path: Path) -> Image.Image:
    """Attach path provenance while making in-place edits observable.

    Pillow drops custom attributes on copy/crop/resize. For in-place operations it uses
    copy-on-write and clears ``readonly`` before touching pixels, so IRPainter can safely
    reuse the source path only while both markers are intact.
    """
    setattr(image, _PRISTINE_ASSET_PATH_ATTR, str(full_path))
    image.readonly = 1
    return image


def _timedelta_precision_level(precision: str) -> int | str:
    match precision:
        case "s":
            return 3
        case "m":
            return 2
        case "h":
            return 1
        case "d":
            return 0
    return precision


def get_readable_timedelta(delta: timedelta, precision: str = "m", use_en_unit: bool = False) -> str:
    """将时间段转换为可读字符串。"""

    total_seconds = int(delta.total_seconds())
    if total_seconds < 0:
        return "0秒" if not use_en_unit else "0s"
    days, seconds = divmod(total_seconds, 24 * 3600)
    hours, seconds = divmod(seconds, 3600)
    minutes, seconds = divmod(seconds, 60)
    units = (
        (days, 0, "天", "d"),
        (hours, 1, "小时", "h"),
        (minutes, 2, "分钟", "m"),
        (seconds, 3, "秒", "s"),
    )
    precision_level = _timedelta_precision_level(precision)
    parts: list[str] = []
    for value, level, zh_unit, en_unit in units:
        if value > 0 and (level == 0 or level <= precision_level or not parts):
            parts.append(f"{value}{en_unit if use_en_unit else zh_unit}")
    return "".join(parts)


async def get_img_from_path(
    base_path: Path,
    path: AssetKey | None,
    on_missing: MissingImageMode = "placeholder",
) -> Image.Image:
    """
    通过路径获取图片（候选列表取第一个存在的）
    """
    label = _key_label(path)
    if not candidates(path):
        if on_missing == "placeholder":
            _log_missing_image_once(label, "empty-path")
            return _get_missing_placeholder_image(label)
        _record_missing("empty-path")
        raise ValueError(_EMPTY_IMAGE_PATH_MESSAGE)

    try:
        return await run_in_pool(_load_image_from_path_sync, base_path, path)
    except (FileNotFoundError, OSError) as exc:
        if on_missing == "placeholder":
            _log_missing_image_once(label, exc)
            return _get_missing_placeholder_image(label)
        _record_missing(exc)
        raise


def _open_image_copy(path: Path) -> Image.Image:
    from PIL import Image

    record_pillow_touch(PILLOW_TOUCH_IMAGE_DECODE)
    with Image.open(path) as img:
        img.load()
        return img.copy()


_image_cache_lock = threading.RLock()
# cache key: (path, mtime_ns, file_size, target_w, target_h, resample)
# target (0, 0) means original size (no resize), and then resample is 0 as well;
# the filter is part of the key so a BICUBIC paste and a BILINEAR/LANCZOS
# get_img_resized of the same asset at the same size cannot return each other's pixels.
_ImageCacheKey = tuple[str, int, int, int, int, int]
_image_cache: OrderedDict[_ImageCacheKey, tuple[Image.Image, int]] = OrderedDict()
_image_cache_total_bytes = 0
_image_cache_hits = 0
_image_cache_misses = 0
_image_cache_sets = 0
_image_cache_evictions = 0

# 缩略图专用缓存：路径含 "thumbnail" 的图片路由到此缓存，避免被大图驱逐
_thumb_cache_lock = threading.RLock()
_thumb_cache: OrderedDict[_ImageCacheKey, tuple[Image.Image, int]] = OrderedDict()
_thumb_cache_total_bytes = 0
_thumb_cache_hits = 0
_thumb_cache_misses = 0
_thumb_cache_sets = 0
_thumb_cache_evictions = 0

_missing_placeholder_lock = threading.RLock()
_missing_placeholder_cache: dict[str, Image.Image] = {}
_missing_placeholder_logged: set[str] = set()


# Attribute a raise site stamps on the FileNotFoundError it raises, so the eventual consumer (placeholder log or
# `on_missing="raise"` re-raise) counts the precise §5.3 reason exactly once without changing the error type/text.
_MISSING_REASON_ATTR = "haruki_missing_reason"


def _missing_error(message: str, reason: str) -> FileNotFoundError:
    exc = FileNotFoundError(message)
    setattr(exc, _MISSING_REASON_ATTR, reason)
    return exc


def _missing_reason_of(reason: str | BaseException | None) -> str:
    """Map a miss (the `empty-path` marker string or the caught exception) to its §5.3 counter reason."""
    if isinstance(reason, BaseException):
        return getattr(reason, _MISSING_REASON_ATTR, None) or MISSING_LOCAL_NOT_FOUND
    if reason == "empty-path":
        return MISSING_EMPTY_PATH
    return MISSING_LOCAL_NOT_FOUND


def _record_missing(reason: str | BaseException | None) -> None:
    record_missing_asset(_missing_reason_of(reason))


def record_missing_asset_error(exc: FileNotFoundError, *, candidate_count: int = 1) -> None:
    """Count one miss surfaced by a resolver error (e.g. `resolve_logical_file`) under its §5.3 reason.

    A candidate list of more than one key counts as ``candidates_exhausted``, like `get_asset_image_ref`.
    """
    record_missing_asset(MISSING_CANDIDATES_EXHAUSTED if candidate_count > 1 else _missing_reason_of(exc))


def _log_missing_image_once(
    path: str | None,
    reason: str | BaseException,
    *,
    missing_reason: str | None = None,
) -> None:
    """WARN once per `(path, reason)`, but count EVERY call (plan §5.3)."""
    record_missing_asset(missing_reason or _missing_reason_of(reason))
    if isinstance(reason, BaseException):
        reason_text = f"{reason.__class__.__name__}: {reason}"
    else:
        reason_text = reason

    key = f"{path or '<empty>'}|{reason_text}"
    with _missing_placeholder_lock:
        if key in _missing_placeholder_logged:
            return
        _missing_placeholder_logged.add(key)

    logger.warning("图片素材缺失，已使用问号占位图: %s (%s)", path or "<empty>", reason_text)


def _build_missing_placeholder_image(variant: str) -> Image.Image:
    from .pillow_placeholder import build_placeholder

    return build_placeholder(variant)


def _get_missing_placeholder_image(path: str | None) -> Image.Image:
    return _get_missing_placeholder_variant_image(_guess_missing_placeholder_variant(path))


def _get_missing_placeholder_variant_image(variant: str) -> Image.Image:
    record_pillow_touch(PILLOW_TOUCH_PLACEHOLDER)
    with _missing_placeholder_lock:
        cached = _missing_placeholder_cache.get(variant)
        if cached is None:
            cached = _build_missing_placeholder_image(variant)
            _missing_placeholder_cache[variant] = cached
        return cached.copy()


def _estimate_image_bytes(img: Image.Image) -> int:
    bpp = {
        "1": 1,
        "L": 1,
        "P": 1,
        "LA": 2,
        "RGB": 3,
        "RGBA": 4,
        "CMYK": 4,
        "I": 4,
        "F": 4,
        "I;16": 2,
    }.get(img.mode, len(img.getbands()) or 4)
    return img.width * img.height * bpp


def _is_thumbnail_path(path: str) -> bool:
    return "thumbnail" in path


def _cache_enabled(path: str) -> bool:
    """判断给定路径是否有可用的缓存。"""
    if _is_thumbnail_path(path):
        return THUMB_CACHE_SIZE > 0 and THUMB_CACHE_MAX_BYTES > 0
    return IMAGE_CACHE_SIZE > 0 and IMAGE_CACHE_MAX_BYTES > 0


class _TTLImageCache:
    def __init__(self, max_size: int, max_bytes: int, ttl_seconds: int):
        self._max_size = max_size
        self._max_bytes = max_bytes
        self._ttl_seconds = ttl_seconds
        self._lock = threading.RLock()
        self._cache: OrderedDict[str, tuple[Image.Image, int, float]] = OrderedDict()
        self._total_bytes = 0
        self._hits = 0
        self._misses = 0
        self._sets = 0
        self._evictions = 0
        self._expired = 0

    def _enabled(self) -> bool:
        return self._max_size > 0 and self._max_bytes > 0 and self._ttl_seconds > 0

    def _delete_unlocked(
        self,
        key: str,
        entry: tuple[Image.Image, int, float],
        *,
        count_eviction: bool = False,
    ) -> None:
        image, image_bytes, _ = entry
        self._cache.pop(key, None)
        self._total_bytes -= image_bytes
        if count_eviction:
            self._evictions += 1
        image.close()

    def _prune_expired_unlocked(self, now: float) -> None:
        expired_keys = [key for key, (_, _, expires_at) in self._cache.items() if now >= expires_at]
        for key in expired_keys:
            entry = self._cache.get(key)
            if entry is not None:
                self._expired += 1
                self._delete_unlocked(key, entry)

    def get(self, key: str) -> Image.Image | None:
        if not self._enabled():
            return None
        now = time.monotonic()
        with self._lock:
            entry = self._cache.get(key)
            if entry is None:
                self._misses += 1
                return None
            image, _, expires_at = entry
            if now >= expires_at:
                self._expired += 1
                self._misses += 1
                self._delete_unlocked(key, entry)
                return None
            self._hits += 1
            self._cache.move_to_end(key)
            return image.copy()

    def set(self, key: str, image: Image.Image) -> None:
        if not self._enabled():
            return

        cached_image = image.copy()
        cache_bytes = _estimate_image_bytes(cached_image)
        now = time.monotonic()
        expires_at = now + self._ttl_seconds

        with self._lock:
            self._prune_expired_unlocked(now)

            old_entry = self._cache.get(key)
            if old_entry is not None:
                self._delete_unlocked(key, old_entry)

            self._cache[key] = (cached_image, cache_bytes, expires_at)
            self._total_bytes += cache_bytes
            self._sets += 1

            while self._cache and (len(self._cache) > self._max_size or self._total_bytes > self._max_bytes):
                _, entry = self._cache.popitem(last=False)
                evict_image, evict_bytes, _ = entry
                self._total_bytes -= evict_bytes
                self._evictions += 1
                evict_image.close()

    def stats(self) -> dict[str, Any]:
        with self._lock:
            now = time.monotonic()
            self._prune_expired_unlocked(now)
            total_queries = self._hits + self._misses
            hit_rate = (self._hits / total_queries) if total_queries > 0 else None
            return {
                "enabled": self._enabled(),
                "entries": len(self._cache),
                "max_entries": self._max_size,
                "bytes": self._total_bytes,
                "max_bytes": self._max_bytes,
                "ttl_seconds": self._ttl_seconds,
                "hits": self._hits,
                "misses": self._misses,
                "sets": self._sets,
                "evictions": self._evictions,
                "expired": self._expired,
                "hit_rate": hit_rate,
            }

    def clear(self) -> None:
        with self._lock:
            for image, _, _ in self._cache.values():
                image.close()
            self._cache.clear()
            self._total_bytes = 0
            self._hits = 0
            self._misses = 0
            self._sets = 0
            self._evictions = 0
            self._expired = 0


_composed_image_cache = _TTLImageCache(
    COMPOSED_IMAGE_CACHE_SIZE,
    COMPOSED_IMAGE_CACHE_MAX_BYTES,
    COMPOSED_IMAGE_CACHE_TTL_SECONDS,
)
COMPOSED_IMAGE_DISK_CACHE_DIR = Path("data/utils/composed_image_disk_cache")


class _DiskImageCache:
    def __init__(self, cache_dir: Path, ttl_seconds: int):
        self._cache_dir = cache_dir
        self._ttl_seconds = ttl_seconds
        self._lock = threading.RLock()
        self._hits = 0
        self._misses = 0
        self._sets = 0
        self._expired = 0
        self._errors = 0

    def _enabled(self) -> bool:
        return self._ttl_seconds > 0

    def _path(self, namespace: str, key: str) -> Path:
        safe_namespace = (namespace or "default").strip().replace("\\", "/").strip("/")
        if safe_namespace == "":
            safe_namespace = "default"
        return self._cache_dir / safe_namespace / f"{key}.png"

    def get(self, namespace: str, key: str) -> Image.Image | None:
        if not self._enabled():
            return None

        cache_path = self._path(namespace, key)
        now = time.time()

        with self._lock:
            try:
                stat = cache_path.stat()
            except OSError:
                self._misses += 1
                return None

            if now - stat.st_mtime >= self._ttl_seconds:
                self._misses += 1
                self._expired += 1
                try:
                    cache_path.unlink()
                except OSError:
                    pass
                return None

            try:
                image = _open_image_copy(cache_path)
            except (FileNotFoundError, OSError):
                self._misses += 1
                self._errors += 1
                return None

            self._hits += 1
            try:
                os.utime(cache_path, None)
            except OSError:
                pass
            return image

    def set(self, namespace: str, key: str, image: Image.Image) -> None:
        if not self._enabled():
            return

        cache_path = self._path(namespace, key)
        tmp_path = cache_path.with_suffix(".tmp")
        image_copy = image.copy()

        try:
            with self._lock:
                cache_path.parent.mkdir(parents=True, exist_ok=True)
                image_copy.save(tmp_path, format="PNG")
                os.replace(tmp_path, cache_path)
                self._sets += 1
        except OSError:
            with self._lock:
                self._errors += 1
            try:
                tmp_path.unlink()
            except OSError:
                pass
        finally:
            image_copy.close()

    def cleanup_expired(self) -> int:
        if not self._enabled():
            return 0

        cutoff = time.time() - self._ttl_seconds
        removed = 0
        with self._lock:
            for path in self._cache_dir.rglob("*.png"):
                try:
                    if path.stat().st_mtime < cutoff:
                        path.unlink()
                        removed += 1
                except OSError:
                    pass
            self._expired += removed
        return removed

    def stats(self) -> dict[str, Any]:
        entries = 0
        total_bytes = 0
        if self._cache_dir.is_dir():
            for path in self._cache_dir.rglob("*.png"):
                try:
                    stat = path.stat()
                except OSError:
                    continue
                entries += 1
                total_bytes += stat.st_size

        with self._lock:
            total_queries = self._hits + self._misses
            hit_rate = (self._hits / total_queries) if total_queries > 0 else None
            return {
                "enabled": self._enabled(),
                "entries": entries,
                "bytes": total_bytes,
                "ttl_seconds": self._ttl_seconds,
                "hits": self._hits,
                "misses": self._misses,
                "sets": self._sets,
                "expired": self._expired,
                "errors": self._errors,
                "hit_rate": hit_rate,
            }


_composed_image_disk_cache = _DiskImageCache(
    COMPOSED_IMAGE_DISK_CACHE_DIR,
    COMPOSED_IMAGE_CACHE_TTL_SECONDS,
)


def _normalize_cache_material(value: Any) -> Any:
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, dict):
        return {
            str(key): _normalize_cache_material(child)
            for key, child in sorted(value.items(), key=lambda item: str(item[0]))
        }
    if isinstance(value, list | tuple | set):
        return [_normalize_cache_material(item) for item in value]

    model_dump = getattr(value, "model_dump", None)
    if callable(model_dump):
        return _normalize_cache_material(model_dump(mode="json"))

    return str(value)


@lru_cache(maxsize=1)
def renderer_code_fingerprint() -> str:
    """A hash of the drawing code itself, folded into every rendered-image cache key.

    The composed-image cache has an L2 on disk (``data/utils/composed_image_disk_cache``, TTL one
    week) and in Docker ``data/`` is a MOUNTED VOLUME -- it outlives the container. Nothing in the
    key identified the code that drew the image, so a deploy that changed how a fragment is drawn
    left the new binary happily serving the old pixels off the volume for up to seven days.
    Reproduced: change the entry background colour, restart (memory cleared, volume kept), and the
    pre-change image comes straight back.

    Content, not mtime: rebuilding the image rewrites every mtime, which would throw the whole disk
    cache away on each deploy even when nothing changed. Costs ~15 ms once, at import.

    Degrades to a constant if the source tree is not readable (installed as a zipapp, say) -- that
    puts us back where we started rather than crashing a render.
    """
    root = Path(__file__).resolve().parents[2]  # .../src
    digest = hashlib.sha256()
    try:
        for path in sorted(root.rglob("*.py")):
            digest.update(str(path.relative_to(root)).encode("utf-8"))
            digest.update(path.read_bytes())
    except OSError:
        logger.warning("renderer source fingerprint unavailable; disk-cached images may survive a deploy")
        return "unknown"
    return digest.hexdigest()[:16]


def build_rendered_image_cache_key(
    namespace: str,
    request: Any,
    *,
    asset_signatures: dict[str, Any] | None = None,
    extra: dict[str, Any] | None = None,
) -> str:
    material = {
        "namespace": namespace,
        "code": renderer_code_fingerprint(),
        "request": _normalize_cache_material(request),
        "assets": _normalize_cache_material(asset_signatures or {}),
        "extra": _normalize_cache_material(extra or {}),
    }
    payload = json.dumps(material, ensure_ascii=True, sort_keys=True, separators=(",", ":"))
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


def get_image_asset_signature(base_path: Path, path: AssetKey | None) -> dict[str, Any] | None:
    """Stat signature of an asset; a candidate list gives ``{"candidates": [sig, ...]}`` in candidate order.

    Every candidate is signed (``{"missing": True}`` markers kept), so a candidate that appears or is replaced
    moves the signature even when an earlier one is the file currently chosen.
    """
    if isinstance(path, list):
        items = candidates(path)
        if not items:
            return None
        return {"candidates": [get_image_asset_signature(base_path, item) for item in items]}
    if path is None or path.strip() == "":
        return None

    try:
        _full_path, full_path_str, stat = _resolve_and_stat(base_path, path)
    except (FileNotFoundError, OSError, ValueError):
        return {"source_path": path, "missing": True}

    return {
        "source_path": path,
        "resolved_path": full_path_str,
        "mtime_ns": stat.st_mtime_ns,
        "size": stat.st_size,
    }


_ASSET_SUFFIXES = (".png", ".jpg", ".jpeg", ".webp")


def collect_asset_signatures(base_path: Path, material: Any) -> dict[str, Any]:
    """Stat every asset path mentioned anywhere in ``material`` (the cache key's request payload).

    A cache key that carries only the request goes stale the moment an asset is REPLACED at a path
    the request already names: the request is unchanged, so the key is unchanged, so the old picture
    keeps being served until the entry expires (the TTL here is a week). The nastiest version is not
    an art change but a missing asset -- the page renders a "?" placeholder, the asset later lands on
    disk, and the placeholder is what everyone keeps getting.

    Walking the key material instead of hand-listing the fields is deliberate: honor lists its
    fourteen asset paths by name (``honor/drawer.py``), which is correct today and silently wrong the
    day someone adds a fifteenth. Anything path-shaped in the key gets stat'd, automatically.

    Cost is small next to what the cache is skipping: card/box mentions 1457 distinct assets and
    stats them all in ~3 ms warm, against a ~26 ms key build and a 1-2 s render.
    """
    signatures: dict[str, Any] = {}

    def walk(node: Any) -> None:
        if isinstance(node, str):
            if node.lower().endswith(_ASSET_SUFFIXES) and node not in signatures:
                signatures[node] = get_image_asset_signature(base_path, node)
        elif isinstance(node, dict):
            for value in node.values():
                walk(value)
        elif isinstance(node, (list, tuple)):
            for value in node:
                walk(value)

    walk(material)
    return signatures


def get_composed_image_cached(cache_key: str) -> Image.Image | None:
    return _composed_image_cache.get(cache_key)


def put_composed_image_cache(cache_key: str, image: Image.Image) -> None:
    _composed_image_cache.set(cache_key, image)


def get_composed_image_disk_cached(namespace: str, cache_key: str) -> Image.Image | None:
    return _composed_image_disk_cache.get(namespace, cache_key)


def put_composed_image_disk_cache(namespace: str, cache_key: str, image: Image.Image) -> None:
    _composed_image_disk_cache.set(namespace, cache_key, image)


def cleanup_expired_composed_image_disk_cache() -> int:
    return _composed_image_disk_cache.cleanup_expired()


def _build_shared_cache_stats(
    *,
    enabled: bool,
    entries: int,
    max_entries: int,
    current_bytes: int,
    max_bytes: int,
    hits: int,
    misses: int,
    sets: int,
    evictions: int,
) -> dict[str, Any]:
    total_queries = hits + misses
    hit_rate = (hits / total_queries) if total_queries > 0 else None
    return {
        "enabled": enabled,
        "entries": entries,
        "max_entries": max_entries,
        "bytes": current_bytes,
        "max_bytes": max_bytes,
        "hits": hits,
        "misses": misses,
        "sets": sets,
        "evictions": evictions,
        "hit_rate": hit_rate,
    }


def get_runtime_cache_stats() -> dict[str, Any]:
    with _image_cache_lock:
        image_stats = _build_shared_cache_stats(
            enabled=IMAGE_CACHE_SIZE > 0 and IMAGE_CACHE_MAX_BYTES > 0,
            entries=len(_image_cache),
            max_entries=IMAGE_CACHE_SIZE,
            current_bytes=_image_cache_total_bytes,
            max_bytes=IMAGE_CACHE_MAX_BYTES,
            hits=_image_cache_hits,
            misses=_image_cache_misses,
            sets=_image_cache_sets,
            evictions=_image_cache_evictions,
        )

    with _thumb_cache_lock:
        thumb_stats = _build_shared_cache_stats(
            enabled=THUMB_CACHE_SIZE > 0 and THUMB_CACHE_MAX_BYTES > 0,
            entries=len(_thumb_cache),
            max_entries=THUMB_CACHE_SIZE,
            current_bytes=_thumb_cache_total_bytes,
            max_bytes=THUMB_CACHE_MAX_BYTES,
            hits=_thumb_cache_hits,
            misses=_thumb_cache_misses,
            sets=_thumb_cache_sets,
            evictions=_thumb_cache_evictions,
        )

    from src.sekai.skia_renderer.fragment_cache import get_native_fragment_cache_stats

    composed_stats = _composed_image_cache.stats()
    composed_disk_stats = _composed_image_disk_cache.stats()
    # Imported lazily: the Skia payload cache lives under src.sekai.skia_renderer, which imports
    # this module transitively; the custom-profile pools live next to their renderer.
    from src.sekai.profile.custom_profile.cache import get_custom_profile_cache_stats
    from src.sekai.skia_renderer.canvas import get_native_renderer_cache_stats
    from src.sekai.skia_renderer.payload_cache import get_skia_payload_cache_stats

    return {
        "image_cache": image_stats,
        "thumbnail_cache": thumb_stats,
        "composed_image_cache": composed_stats,
        "native_fragment_cache": get_native_fragment_cache_stats(),
        "composed_image_disk_cache": composed_disk_stats,
        "skia_payload_cache": get_skia_payload_cache_stats(),
        "native_renderer_cache": get_native_renderer_cache_stats(),
        "custom_profile_caches": get_custom_profile_cache_stats(),
        "asset_mirror": _asset_mirror().stats_snapshot(),
        "missing_assets": get_missing_asset_stats(),
    }


def _load_image_cached(
    path: str,
    mtime_ns: int,
    size: int,
    target_w: int = 0,
    target_h: int = 0,
    count_stats: bool = True,
    resample: int = 0,
) -> Image.Image | None:
    """``count_stats=False`` for opportunistic probes (the full-size lookup a resize does on its
    way to a miss). Such a probe must be stats-NEUTRAL: counting only its hits and not its misses
    would inflate the reported hit rate of a cache that served nothing."""
    cache_key = (path, mtime_ns, size, target_w, target_h, resample)
    if _is_thumbnail_path(path):
        lock, cache = _thumb_cache_lock, _thumb_cache
        hit_name, miss_name = "_thumb_cache_hits", "_thumb_cache_misses"
    else:
        lock, cache = _image_cache_lock, _image_cache
        hit_name, miss_name = "_image_cache_hits", "_image_cache_misses"
    with lock:
        entry = cache.get(cache_key)
        if entry is None:
            if count_stats:
                globals()[miss_name] += 1
            return None
        image, _ = entry
        if count_stats:
            globals()[hit_name] += 1
        cache.move_to_end(cache_key)
        return image.copy()


def _record_image_cache_write(is_thumb: bool, current_bytes: int, evictions: int) -> None:
    global _image_cache_total_bytes, _thumb_cache_total_bytes
    global _image_cache_sets, _thumb_cache_sets, _image_cache_evictions, _thumb_cache_evictions

    if is_thumb:
        _thumb_cache_total_bytes = current_bytes
        _thumb_cache_sets += 1
        _thumb_cache_evictions += evictions
    else:
        _image_cache_total_bytes = current_bytes
        _image_cache_sets += 1
        _image_cache_evictions += evictions


def _put_image_cache(
    path: str,
    mtime_ns: int,
    size: int,
    image: Image.Image,
    target_w: int = 0,
    target_h: int = 0,
    resample: int = 0,
) -> None:
    is_thumb = _is_thumbnail_path(path)
    if is_thumb:
        lock, cache = _thumb_cache_lock, _thumb_cache
        max_size, max_bytes = THUMB_CACHE_SIZE, THUMB_CACHE_MAX_BYTES
    else:
        lock, cache = _image_cache_lock, _image_cache
        max_size, max_bytes = IMAGE_CACHE_SIZE, IMAGE_CACHE_MAX_BYTES

    if max_size <= 0 or max_bytes <= 0:
        return

    cache_key = (path, mtime_ns, size, target_w, target_h, resample)
    cache_bytes = _estimate_image_bytes(image)
    with lock:
        current_bytes = _thumb_cache_total_bytes if is_thumb else _image_cache_total_bytes
        old_entry = cache.pop(cache_key, None)
        if old_entry is not None:
            old_image, old_bytes = old_entry
            current_bytes -= old_bytes
            old_image.close()

        cache[cache_key] = (image, cache_bytes)
        current_bytes += cache_bytes

        # 双阈值驱逐：条目数和总字节数都受控
        evictions = 0
        while cache and (len(cache) > max_size or current_bytes > max_bytes):
            _, (evict_image, evict_bytes) = cache.popitem(last=False)
            current_bytes -= evict_bytes
            evictions += 1
            evict_image.close()
        _record_image_cache_write(is_thumb, current_bytes, evictions)


_STATIC_BIRTHDAY_PARTS = ("static_images", "mysekai", "birthday")
_BUCKET_BIRTHDAY_PARTS = ("mysekai", "birthday")
_BUCKET_MODES = frozenset({"startapp", "ondemand"})


def _is_region_assets_segment(part: str) -> bool:
    region, sep, suffix = part.partition("-")
    return bool(sep) and suffix == "assets" and len(region) == 2 and region.isascii() and region.islower()


def _bucket_birthday_root_len(parts: tuple[str, ...], region_index: int) -> int | None:
    """``<cc>-assets/<mode>/mysekai/birthday`` starting at ``region_index``: the root length, else ``None``."""
    end = region_index + 4
    if len(parts) < end or not _is_region_assets_segment(parts[region_index]):
        return None
    if parts[region_index + 1] not in _BUCKET_MODES or parts[region_index + 2 : end] != _BUCKET_BIRTHDAY_PARTS:
        return None
    return end


def _birthday_root_len(parts: tuple[str, ...]) -> int | None:
    """Length of the birthday-root prefix of ``parts`` for the three accepted shapes (plan §5.4/§6.3).

    ``static_images/mysekai/birthday``; the legacy tree ``asset/<cc>-assets/<mode>/mysekai/birthday``; and the
    mirror tree ``<mirror_dir>/<version>/<cc>-assets/<mode>/mysekai/birthday``.
    """
    if parts[:3] == _STATIC_BIRTHDAY_PARTS:
        return 3
    if parts[:1] == ("asset",):
        return _bucket_birthday_root_len(parts, 1)
    mirror_dir = getattr(_asset_mirror(), "mirror_dir", None)
    if not isinstance(mirror_dir, str) or not mirror_dir:
        return None
    mirror_parts = tuple(part for part in mirror_dir.split("/") if part)
    if parts[: len(mirror_parts)] != mirror_parts:
        return None
    return _bucket_birthday_root_len(parts, len(mirror_parts) + 1)


def _birthday_fallback_request(full_path: Path, resolved_base: Path) -> tuple[Path, str, int, tuple[str, ...]] | None:
    """``(birthday_root, chara_name, year, tail_parts)`` when ``full_path`` names a yearly birthday asset."""
    try:
        rel_path = full_path.relative_to(resolved_base)
    except ValueError:
        return None
    parts = rel_path.parts
    root_len = _birthday_root_len(parts)
    if root_len is None or len(parts) < root_len + 2:
        return None
    directory_name = parts[root_len]
    if "_" not in directory_name:
        return None
    chara_name, year_text = directory_name.rsplit("_", 1)
    if not chara_name or not year_text.isdigit():
        return None
    return resolved_base.joinpath(*parts[:root_len]), chara_name, int(year_text), parts[root_len + 1 :]


def _generic_birthday_fallback(resolved_base: Path) -> Path | None:
    path = (
        resolved_base
        / "static_images"
        / "mysekai"
        / "harvest_fixture_icon"
        / "rarity_1"
        / "mdl_site_wood_common_fieldtree01.png"
    )
    return path if path.is_file() else None


def _birthday_fallback_candidates(
    birthday_root: Path,
    chara_name: str,
    tail_parts: tuple[str, ...],
) -> list[tuple[int, Path]]:
    candidates: list[tuple[int, Path]] = []
    prefix = chara_name + "_"
    for entry in birthday_root.iterdir():
        if not entry.is_dir() or not entry.name.startswith(prefix):
            continue
        candidate_year = entry.name[len(prefix) :]
        if not candidate_year.isdigit():
            continue
        candidate_path = entry.joinpath(*tail_parts)
        if candidate_path.is_file():
            candidates.append((int(candidate_year), candidate_path))
    return candidates


def _resolve_birthday_year_fallback(full_path: Path, resolved_base: Path) -> Path | None:
    request = _birthday_fallback_request(full_path, resolved_base)
    if request is None:
        return None
    birthday_root, chara_name, target_year, tail_parts = request
    if not birthday_root.is_dir():
        return _generic_birthday_fallback(resolved_base)
    fallback_candidates = _birthday_fallback_candidates(birthday_root, chara_name, tail_parts)
    if not fallback_candidates:
        return _generic_birthday_fallback(resolved_base)

    same_or_older = [item for item in fallback_candidates if item[0] <= target_year]
    if same_or_older:
        return max(same_or_older, key=lambda item: item[0])[1]
    return min(fallback_candidates, key=lambda item: item[0])[1]


def _load_image_from_path_sync(base_path: Path, path: AssetKey) -> Image.Image:
    full_path, _, stat = _resolve_key_and_stat(base_path, path)
    return _load_image_full_path_sync(full_path, stat=stat)


def _load_image_full_path_sync(full_path: Path, stat: os.stat_result | None = None) -> Image.Image:
    """Decode an already-resolved absolute path through the global image cache.

    ``stat`` lets a caller that has already stat'd the file hand the result down instead of
    paying for a second syscall.
    """
    if not _cache_enabled(str(full_path)):
        return _mark_pristine_asset_image(_open_image_copy(full_path), full_path)

    if stat is None:
        stat = full_path.stat()
    full_path_str = str(full_path)
    cached = _load_image_cached(full_path_str, stat.st_mtime_ns, stat.st_size)
    if cached is not None:
        return _mark_pristine_asset_image(cached, full_path)

    loaded = _open_image_copy(full_path)
    ret = loaded.copy()
    _put_image_cache(full_path_str, stat.st_mtime_ns, stat.st_size, loaded)
    return _mark_pristine_asset_image(ret, full_path)


def resolve_image_source_sync(
    source: ImageSource,
    target_size: tuple[int, int] | None = None,
    resample: int = PASTE_RESAMPLE,
) -> Image.Image:
    """Decode an image source to pixels for the Pillow renderer.

    ``AssetImageRef`` decodes through the global image cache and degrades to the
    missing-image placeholder if the file vanished after the ref was probed
    (mirroring ``get_img_from_path``); ``EncodedImageRef`` decodes its bytes; a PIL
    image passes through untouched. With ``target_size``, an ``AssetImageRef`` goes
    through the global resize cache (other source kinds ignore it — the caller
    resizes). Synchronous — call from pool threads, not the event loop."""
    from PIL import Image

    if isinstance(source, Image.Image):
        return source
    if isinstance(source, AssetImageRef):
        try:
            if target_size is not None:
                return _load_image_resized_full_path_sync(source.path, target_size[0], target_size[1], resample)
            return _load_image_full_path_sync(source.path)
        except (FileNotFoundError, OSError) as exc:
            _log_missing_image_once(str(source.path), exc, missing_reason=MISSING_VANISHED)
            return _get_missing_placeholder_image(str(source.path))
    if isinstance(source, MissingImageRef):
        return _get_missing_placeholder_variant_image(source.variant)
    if isinstance(source, EncodedImageRef):
        record_pillow_touch(PILLOW_TOUCH_IMAGE_DECODE)
        with Image.open(io.BytesIO(source.data)) as img:
            img.load()
            return img.copy()
    raise TypeError(f"unsupported image source: {type(source)!r}")


_PATH_RESOLVE_CACHE_MAX = 32_768
# Key: (base, logical path, mirror version) — the version is "" with the local source (NullMirror), so a
# manifest bump can never serve a mapping of the previous version.
_resolved_path_cache: dict[tuple[str, str, str], tuple[Path, Path, str]] = {}


def _asset_mirror():
    """The process-wide asset mirror (`NullMirror` unless `assets.source == "mirror"`).

    Imported lazily: `src.assets` must stay importable without `src.sekai`, and nothing is built at import.
    """
    from src.assets.mirror import get_asset_mirror

    return get_asset_mirror()


def clear_resolved_path_cache() -> None:
    """Drop every memoized path resolution (a mirror version change, tests)."""
    _resolved_path_cache.clear()


def _resolve_asset_path(base_path: Path, path: str) -> tuple[Path, Path, str]:
    """``(resolved_base, full_path, full_path_str)``, memoized.

    ``Path.resolve()`` is a realpath walk — an ``lstat`` per path COMPONENT — and both the base and
    the asset path get one on every single image. A music list with 696 jackets was spending 33k
    lstat calls here. The resolution is pure path arithmetic over a static asset tree, so it is
    cached; only the ``stat`` (which feeds every cache key, and must see a replaced file) stays live.

    The traversal check runs on the cached side because it is a property of the resolved PATH, not of
    the file. Caching it does mean a symlink planted inside the asset tree AFTER a path was first
    resolved would be followed without a fresh escape check — but writing into the asset dir already
    implies the ability to replace the images themselves, so this buys an attacker nothing new.
    """
    mirror = _asset_mirror()
    key = (str(base_path), path, mirror.version)
    cached = _resolved_path_cache.get(key)
    if cached is not None:
        return cached

    resolved_base = base_path.resolve()
    mapped = mirror.local_path(path)  # None with NullMirror and for non-bucket keys -> today's branch
    if mapped is not None:
        full_path = (resolved_base / mapped[1].mirror_rel).resolve()
    else:
        full_path = (resolved_base / path.lstrip("/")).resolve()
    # The traversal guard is unchanged and applies to both branches (a symlinked mirror dir cannot escape).
    if not full_path.is_relative_to(resolved_base):
        raise ValueError(f"图片路径越界: {path}")

    entry = (resolved_base, full_path, str(full_path))
    if len(_resolved_path_cache) >= _PATH_RESOLVE_CACHE_MAX:
        _resolved_path_cache.clear()
    _resolved_path_cache[key] = entry
    return entry


def _stat_regular_file(full_path: Path) -> os.stat_result | None:
    """``stat`` of ``full_path``, or ``None`` if it is not an existing regular file.

    One syscall where ``is_file()`` followed by ``stat()`` was two — and the stat is needed anyway,
    since its mtime/size are what key every image cache.
    """
    try:
        st = os.stat(full_path)
    except (OSError, ValueError):
        return None
    return st if S_ISREG(st.st_mode) else None


def _resolve_and_stat(
    base_path: Path, path: str, *, birthday_fallback: bool = True
) -> tuple[Path, str, os.stat_result]:
    """解析路径并获取 stat，供 resize 和原始加载共用。

    Order: stat -> mirror `ensure_local` (fetch on a miss; `None` with NullMirror) -> birthday fallback ->
    `FileNotFoundError`. The raised error carries the §5.3 miss reason for the consumer to count.
    ``birthday_fallback=False`` skips the fallback (a candidate list tries every candidate first).
    """
    resolved_base, full_path, full_path_str = _resolve_asset_path(base_path, path)

    st = _stat_regular_file(full_path)
    if st is None:
        mirror = _asset_mirror()
        fetched = mirror.ensure_local(path)
        if fetched is not None:
            fetched_st = _stat_regular_file(fetched)
            if fetched_st is not None:
                return fetched, str(fetched), fetched_st
        miss_reason = mirror.last_miss_reason() or MISSING_LOCAL_NOT_FOUND
        fallback_path = _resolve_birthday_year_fallback(full_path, resolved_base) if birthday_fallback else None
        if fallback_path is None:
            raise _missing_error(f"图片文件不存在: {full_path}", miss_reason)
        record_missing_asset(MISSING_BIRTHDAY_FALLBACK)
        return fallback_path, str(fallback_path), fallback_path.stat()

    return full_path, full_path_str, st


def _key_label(key: AssetKey | None) -> str | None:
    """The string used for logs and placeholder variants: the key itself, or a list's FIRST candidate."""
    if key is None or isinstance(key, str):
        return key
    return first_candidate(key)


def _resolve_key_and_stat(base_path: Path, key: AssetKey) -> tuple[Path, str, os.stat_result]:
    """`_resolve_and_stat` for an `AssetKey`: a string is resolved exactly as before.

    A candidate list tries each candidate in order (stat -> mirror fetch) and the first existing one wins; a
    traversal (`ValueError`) propagates at once. When none exists, the birthday fallback is tried for the
    candidates in order; failing that, the FIRST candidate's `FileNotFoundError` is raised, counted as
    ``candidates_exhausted``.
    """
    if isinstance(key, str):
        return _resolve_and_stat(base_path, key)
    items = candidates(key)
    if not items:
        raise _missing_error(_EMPTY_IMAGE_PATH_MESSAGE, MISSING_EMPTY_PATH)
    first_error: FileNotFoundError | None = None
    for item in items:
        try:
            return _resolve_and_stat(base_path, item, birthday_fallback=False)
        except FileNotFoundError as exc:
            if first_error is None:
                first_error = exc
    for item in items:
        resolved_base, full_path, _ = _resolve_asset_path(base_path, item)
        fallback_path = _resolve_birthday_year_fallback(full_path, resolved_base)
        if fallback_path is not None:
            record_missing_asset(MISSING_BIRTHDAY_FALLBACK)
            return fallback_path, str(fallback_path), fallback_path.stat()
    assert first_error is not None
    setattr(first_error, _MISSING_REASON_ATTR, MISSING_CANDIDATES_EXHAUSTED)
    raise first_error


def first_existing_asset_path(base_path: Path, key: AssetKey | None) -> Path | None:
    """Local path of the first existing candidate of `key` (mirror fetch and birthday fallback included).

    ``None`` when the key is blank or nothing exists; a traversal still raises `ValueError`. Nothing is counted
    here: the caller decides whether an absent asset is a miss.
    """
    if not candidates(key):
        return None
    assert key is not None
    try:
        return _resolve_key_and_stat(base_path, key)[0]
    except FileNotFoundError:
        return None


def resolve_logical_file(base_path: Path, key: str) -> Path:
    """Local path of ANY file (e.g. chart `.txt`/`.css`, not only images) for a logical key.

    Map (mirror) + traversal guard + `ensure_local`; no image probe and no birthday fallback. Raises
    `ValueError` on traversal and `FileNotFoundError` when the file is absent everywhere.
    """
    if key is None or key.strip() == "":
        raise _missing_error(_EMPTY_IMAGE_PATH_MESSAGE, MISSING_EMPTY_PATH)
    _resolved_base, full_path, _ = _resolve_asset_path(base_path, key)
    if _stat_regular_file(full_path) is not None:
        return full_path
    mirror = _asset_mirror()
    fetched = mirror.ensure_local(key)
    if fetched is not None and _stat_regular_file(fetched) is not None:
        return fetched
    raise _missing_error(f"文件不存在: {full_path}", mirror.last_miss_reason() or MISSING_LOCAL_NOT_FOUND)


_local_dir_logged: set[str] = set()
_local_dir_logged_lock = threading.Lock()


def _log_local_dir_once(message: str, key: str) -> None:
    with _local_dir_logged_lock:
        marker = f"{message}|{key}"
        if marker in _local_dir_logged:
            return
        if len(_local_dir_logged) >= 4096:
            _local_dir_logged.clear()
        _local_dir_logged.add(marker)
    if message == "not_local":
        logger.error("chart.note_host_not_local key=%s", key)
    else:
        logger.warning("assets.local_dir_missing key=%s", key)


def resolve_local_dir(base_path: Path, key: str) -> Path:
    """Local DIRECTORY for `key` (e.g. the chart `note_host`); NEVER fetches from the mirror.

    Only a traversal raises (`ValueError`). A key that parses as a mirror (bucket) key logs ERROR once and is
    still joined locally, and a missing directory WARNs once and is still returned: today's call site joins
    unconditionally, and raising here would turn a payload quirk into a brand-new 500 (plan §7).
    """
    resolved_base = base_path.resolve()
    full_path = (resolved_base / key.lstrip("/")).resolve()
    if not full_path.is_relative_to(resolved_base):
        raise ValueError(f"目录路径越界: {key}")
    if _asset_mirror().local_path(key) is not None:
        _log_local_dir_once("not_local", key)
    if not full_path.is_dir():
        _log_local_dir_once("missing", key)
    return full_path


@lru_cache(maxsize=16384)
def _load_asset_image_ref_cached(
    full_path_str: str,
    mtime_ns: int,
    file_size: int,
) -> AssetImageRef:
    full_path = Path(full_path_str)
    size, mode = probe_asset(full_path)
    return AssetImageRef(path=full_path, size=size, mode=mode, mtime_ns=mtime_ns, file_size=file_size)


def _load_asset_image_ref_sync(base_path: Path, path: AssetKey) -> AssetImageRef:
    _, full_path_str, stat = _resolve_key_and_stat(base_path, path)
    return _load_asset_image_ref_cached(full_path_str, stat.st_mtime_ns, stat.st_size)


async def get_asset_image_ref(
    base_path: Path,
    path: AssetKey | None,
    on_missing: MissingImageMode = "placeholder",
) -> AssetImageRef | MissingImageRef:
    """Resolve an asset without decoding its pixels.

    This is intended for renderer-specific paths that emit the source path into an IR.
    Missing assets retain a lazy placeholder recipe; only the chosen renderer creates pixels.
    A candidate list is tried in order inside ONE pool task; when every candidate is missing the log line and
    the placeholder variant use the first candidate.
    """
    label = _key_label(path)
    if not candidates(path):
        if on_missing == "placeholder":
            _log_missing_image_once(label, "empty-path")
            return missing_image_ref(_guess_missing_placeholder_variant(label))
        _record_missing("empty-path")
        raise ValueError("图片路径不能为空(None)")

    try:
        return await run_in_pool(_load_asset_image_ref_sync, base_path, path)
    except (FileNotFoundError, OSError) as exc:
        if on_missing == "placeholder":
            _log_missing_image_once(label, exc)
            return missing_image_ref(_guess_missing_placeholder_variant(label))
        _record_missing(exc)
        raise


async def get_asset_image_refs(base_path: Path, paths: list[AssetKey | None]) -> list[AssetImageRef | MissingImageRef]:
    """Batch header-only probes, retaining the global signature-keyed metadata pool.

    Tiny per-layer executor jobs cost more than a warm stat/header lookup. Independent
    batches still overlap I/O; this never creates a per-request decoded-image cache.
    A candidate-list element is one slot of a batch.
    """

    def load_batch(batch):
        result = []
        for path in batch:
            label = _key_label(path)
            try:
                if not candidates(path):
                    raise _missing_error("empty-path", MISSING_EMPTY_PATH)
                result.append(_load_asset_image_ref_sync(base_path, path))
            except (FileNotFoundError, OSError) as exc:
                _log_missing_image_once(label, exc)
                result.append(missing_image_ref(_guess_missing_placeholder_variant(label)))
        return result

    batches = await asyncio.gather(*(run_in_pool(load_batch, paths[i : i + 16]) for i in range(0, len(paths), 16)))
    return [ref for batch in batches for ref in batch]


def _load_image_resized_sync(
    base_path: Path,
    path: AssetKey,
    target_w: int,
    target_h: int,
    resample: int = RasterResample.BILINEAR,
) -> Image.Image:
    """加载图片并 resize 到目标尺寸，结果缓存（缓存键是解析后的路径，候选列表不改变键形状）。"""
    full_path, _, stat = _resolve_key_and_stat(base_path, path)
    return _load_image_resized_full_path_sync(full_path, target_w, target_h, resample, stat=stat)


def _load_image_resized_full_path_sync(
    full_path: Path,
    target_w: int,
    target_h: int,
    resample: int = RasterResample.BILINEAR,
    *,
    stat: os.stat_result | None = None,
) -> Image.Image:
    """Resize an already-resolved absolute path through the global resize cache.

    ``stat`` lets a caller that already stat'd the file (``_load_image_resized_sync`` does, to
    resolve the path) pass it in rather than paying a second syscall per image — a list render
    resizes hundreds of thumbnails."""
    if stat is None:
        stat = full_path.stat()
    full_path_str = str(full_path)

    if _cache_enabled(full_path_str):
        cached = _load_image_cached(
            full_path_str, stat.st_mtime_ns, stat.st_size, target_w, target_h, resample=resample
        )
        if cached is not None:
            return cached

    # Read-only full-size cache probe (an opportunistic bonus lookup, so it stays out of the
    # hit/miss stats entirely); deliberately NO full-size cache put — resized consumers
    # (e.g. hundreds of list jackets) would thrash the byte budget with full-size
    # entries they never read again.
    loaded = None
    if _cache_enabled(full_path_str):
        loaded = _load_image_cached(full_path_str, stat.st_mtime_ns, stat.st_size, count_stats=False)
    if loaded is None:
        loaded = _open_image_copy(full_path)
    resized = loaded.resize((target_w, target_h), resample)
    loaded.close()

    if _cache_enabled(full_path_str):
        ret = resized.copy()
        _put_image_cache(full_path_str, stat.st_mtime_ns, stat.st_size, resized, target_w, target_h, resample=resample)
        return ret

    return resized


async def get_img_resized(
    base_path: Path,
    path: AssetKey | None,
    target_w: int,
    target_h: int,
    *,
    resample: int = RasterResample.BILINEAR,
    on_missing: MissingImageMode = "placeholder",
) -> Image.Image:
    """加载图片并 resize 到 (target_w, target_h)，利用缓存避免重复 resize。

    如果 target_w 或 target_h 为 0，则退化为 get_img_from_path（不 resize）。
    """
    if target_w <= 0 or target_h <= 0:
        return await get_img_from_path(base_path, path, on_missing)

    label = _key_label(path)
    if not candidates(path):
        if on_missing == "placeholder":
            _log_missing_image_once(label, "empty-path")
            img = _get_missing_placeholder_image(label)
            return img.resize((target_w, target_h), resample)
        _record_missing("empty-path")
        raise ValueError(_EMPTY_IMAGE_PATH_MESSAGE)

    try:
        return await run_in_pool(_load_image_resized_sync, base_path, path, target_w, target_h, resample)
    except (FileNotFoundError, OSError) as exc:
        if on_missing == "placeholder":
            _log_missing_image_once(label, exc)
            img = _get_missing_placeholder_image(label)
            return img.resize((target_w, target_h), resample)
        _record_missing(exc)
        raise


async def get_img_resized_long_edge(
    base_path: Path,
    path: AssetKey | None,
    long_edge: int,
    *,
    resample: int = RasterResample.BILINEAR,
    on_missing: MissingImageMode = "placeholder",
) -> Image.Image:
    """加载图片并按 long-edge 等比缩放，结果缓存在 _image_cache 中。

    先获取原图尺寸，计算出精确的 (target_w, target_h)，再走 get_img_resized
    的 exact-resize 缓存路径。与直接调用 resize_keep_ratio 不同，跨请求均可命中缓存。
    """
    if long_edge <= 0:
        return await get_img_from_path(base_path, path, on_missing)

    # 获取原图以得到宽高（全局缓存命中后无磁盘 I/O）
    orig = await get_img_from_path(base_path, path, on_missing=on_missing)
    orig_w, orig_h = orig.width, orig.height
    orig.close()

    # 与 resize_keep_ratio(mode="long") 逻辑一致
    if orig_w >= orig_h:
        target_w = long_edge
        target_h = max(1, int(orig_h * long_edge / orig_w))
    else:
        target_h = long_edge
        target_w = max(1, int(orig_w * long_edge / orig_h))

    return await get_img_resized(base_path, path, target_w, target_h, resample=resample, on_missing=on_missing)


def _contain_resize(img: Image.Image, max_w: int, max_h: int) -> Image.Image:
    """Resize image to fit within (max_w, max_h) keeping aspect ratio (contain mode)."""
    w, h = img.size
    scale = min(max_w / w, max_h / h)
    new_w, new_h = int(w * scale), int(h * scale)
    if (new_w, new_h) == (w, h):
        return img
    return img.resize((new_w, new_h))


def _load_image_contain_resized_sync(base_path: Path, path: AssetKey, max_w: int, max_h: int) -> Image.Image:
    """加载图片并 contain-resize，结果缓存（key 使用负值 max 尺寸以区分 exact resize）。"""
    full_path, full_path_str, stat = _resolve_key_and_stat(base_path, path)

    # 使用负值区分 contain resize 与 exact resize
    cache_tw, cache_th = -max_w, -max_h
    if _cache_enabled(full_path_str):
        cached = _load_image_cached(full_path_str, stat.st_mtime_ns, stat.st_size, cache_tw, cache_th)
        if cached is not None:
            return cached

    loaded = _open_image_copy(full_path)
    resized = _contain_resize(loaded, max_w, max_h)
    if resized is not loaded:
        loaded.close()

    if _cache_enabled(full_path_str):
        ret = resized.copy()
        _put_image_cache(full_path_str, stat.st_mtime_ns, stat.st_size, resized, cache_tw, cache_th)
        return ret

    return resized


def get_str_display_length(s: str) -> int:
    """
    获取字符串的显示长度，中文字符算两个字符
    """
    length = 0
    for c in s:
        length += 1 if ord(c) < 128 else 2
    return length


def get_readable_datetime(t: datetime, show_original_time=True, use_en_unit=False):
    """
    将时间点转换为可读字符串
    """
    if not use_en_unit:
        day_unit, hour_unit, minute_unit, second_unit = ("天", "小时", "分钟", "秒")
    else:
        day_unit, hour_unit, minute_unit, second_unit = ("d", "h", "m", "s")
    now = datetime.now(t.tzinfo) if t.tzinfo is not None else datetime.now()
    diff = t - now
    text, suffix = "", "后"
    if diff.total_seconds() < 0:
        suffix = "前"
        diff = -diff
    if diff.total_seconds() < 60:
        text = f"{int(diff.total_seconds())}{second_unit}"
    elif diff.total_seconds() < 60 * 60:
        text = f"{int(diff.total_seconds() / 60)}{minute_unit}"
    elif diff.total_seconds() < 60 * 60 * 24:
        text = f"{int(diff.total_seconds() / 60 / 60)}{hour_unit}{int(diff.total_seconds() / 60 % 60)}{minute_unit}"
    else:
        text = f"{diff.days}{day_unit}"
    text += suffix
    if show_original_time:
        text = f"{t.strftime('%Y-%m-%d %H:%M:%S')} ({text})"
    return text


def truncate(s: str | None, limit: int) -> str:
    """
    截断字符串到指定长度，中文字符算两个字符
    """
    if s is None:
        return "<None>"
    s = str(s)
    length = 0
    for i, c in enumerate(s):
        if length >= limit:
            return s[:i] + "..."
        length += 1 if ord(c) < 128 else 2
    return s


def get_float_str(value: float, precision: int = 2) -> str:
    """格式化浮点数"""
    format_str = f"{{0:.{precision}f}}".format(value)
    if "." in format_str:
        format_str = format_str.rstrip("0").rstrip(".")
    return format_str


async def concat_images(images, direction="h"):
    """水平或垂直拼接图片"""
    from PIL import Image

    if not images:
        return None

    # 过滤掉None值
    images = [img for img in images if img is not None]
    if not images:
        return None

    if direction == "h":
        # 水平拼接
        total_width = sum(img.width for img in images)
        max_height = max(img.height for img in images)

        result = Image.new("RGBA", (total_width, max_height), (0, 0, 0, 0))
        x_offset = 0
        for img in images:
            result.paste(img, (x_offset, 0))
            x_offset += img.width
    else:
        # 垂直拼接
        max_width = max(img.width for img in images)
        total_height = sum(img.height for img in images)

        result = Image.new("RGBA", (max_width, total_height), (0, 0, 0, 0))
        y_offset = 0
        for img in images:
            result.paste(img, (0, y_offset))
            y_offset += img.height

    return result


def plt_fig_to_image(fig, transparent=True) -> Image.Image:
    """
    matplot图像转换为PIL.Image对象
    """
    from PIL import Image

    with io.BytesIO() as buf:
        fig.savefig(buf, transparent=transparent, format="png")
        buf.seek(0)
        record_pillow_touch(PILLOW_TOUCH_IMAGE_DECODE)
        with Image.open(buf) as img:
            img.load()
            return img.copy()


def get_chara_nickname(cid: int) -> str:
    return {
        1: "ick",
        2: "saki",
        3: "hnm",
        4: "shiho",
        5: "mnr",
        6: "hrk",
        7: "airi",
        8: "szk",
        9: "khn",
        10: "an",
        11: "akt",
        12: "toya",
        13: "tks",
        14: "emu",
        15: "nene",
        16: "rui",
        17: "knd",
        18: "mfy",
        19: "ena",
        20: "mzk",
        21: "miku",
        22: "rin",
        23: "len",
        24: "luka",
        25: "meiko",
        26: "kaito",
        27: "miku_light_sound",
        28: "miku_idol",
        29: "miku_street",
        30: "miku_theme_park",
        31: "miku_school_refusal",
        32: "rin",
        33: "rin",
        34: "rin",
        35: "rin",
        36: "rin",
        37: "len",
        38: "len",
        39: "len",
        40: "len",
        41: "len",
        42: "luka",
        43: "luka",
        44: "luka",
        45: "luka",
        46: "luka",
        47: "meiko",
        48: "meiko",
        49: "meiko",
        50: "meiko",
        51: "meiko",
        52: "kaito",
        53: "kaito",
        54: "kaito",
        55: "kaito",
        56: "kaito",
    }.get(cid)


# ======================= 临时文件 ======================= #

TEMP_FILE_DIR = ASSETS_BASE_DIR / TMP_PATH
_tmp_files_to_remove: list[tuple[str, datetime]] = []
_tmp_files_lock = threading.Lock()


def cleanup_expired_tmp_files() -> int:
    """清理已过期的临时文件，返回清理的文件数量"""
    now = datetime.now()
    removed = 0
    with _tmp_files_lock:
        still_pending: list[tuple[str, datetime]] = []
        for path, expire_at in _tmp_files_to_remove:
            if now >= expire_at:
                try:
                    if os.path.exists(path):
                        os.remove(path)
                    removed += 1
                except OSError:
                    pass
            else:
                still_pending.append((path, expire_at))
        _tmp_files_to_remove.clear()
        _tmp_files_to_remove.extend(still_pending)
    return removed


def rand_filename(ext: str) -> str:
    """
    rand_filename

    生成随机的文件名

    :param ext: 文件扩展名
    :type ext: str
    :return: 随机文件名
    :rtype: str
    """
    if ext.startswith("."):
        ext = ext[1:]
    return f"{uuid4()}.{ext}"


def create_folder(folder_path) -> str:
    """
    创建文件夹，返回文件夹路径
    """
    folder_path = str(folder_path)
    os.makedirs(folder_path, exist_ok=True)
    return folder_path


def create_parent_folder(file_path) -> str:
    """
    创建文件所在的文件夹，返回文件路径
    """
    parent_folder = os.path.dirname(file_path)
    create_folder(parent_folder)
    return file_path


def remove_file(file_path):
    """
    remove_file

    删除file_path指定的文件

    :param file_path: 说明
    """
    if os.path.exists(file_path):
        os.remove(file_path)


# ============================ 异步和任务 ============================ #

from concurrent.futures import ThreadPoolExecutor

_default_pool_executor = ThreadPoolExecutor(max_workers=DEFAULT_THREAD_POOL_SIZE)
_SLOW_POOL_TASK_SECONDS = 0.2


async def run_in_pool(func, *args, pool=None):
    if pool is None:
        global _default_pool_executor
        pool = _default_pool_executor
    request_ctx = current_request_context()
    context = contextvars.copy_context()
    started = time.perf_counter()
    try:
        return await asyncio.get_running_loop().run_in_executor(pool, context.run, func, *args)
    finally:
        elapsed = time.perf_counter() - started
        if elapsed >= _SLOW_POOL_TASK_SECONDS:
            logger.log(
                logging.WARNING if elapsed >= 1.0 else logging.INFO,
                "pool.task id=%s path=%s method=%s func=%s elapsed=%.3fs metrics=%s",
                request_ctx["request_id"],
                request_ctx["path"],
                request_ctx["method"],
                getattr(func, "__name__", repr(func)),
                elapsed,
                snapshot_process_metrics(include_asyncio=False),
            )


def clear_runtime_memory_caches() -> None:
    """Clear every process-level in-memory drawing cache through one public entry point."""
    global _image_cache_total_bytes, _image_cache_hits, _image_cache_misses, _image_cache_sets
    global _image_cache_evictions
    global _thumb_cache_total_bytes, _thumb_cache_hits, _thumb_cache_misses, _thumb_cache_sets
    global _thumb_cache_evictions

    with _image_cache_lock:
        for img, _ in _image_cache.values():
            img.close()
        _image_cache.clear()
        _image_cache_total_bytes = 0
        _image_cache_hits = 0
        _image_cache_misses = 0
        _image_cache_sets = 0
        _image_cache_evictions = 0

    with _thumb_cache_lock:
        for img, _ in _thumb_cache.values():
            img.close()
        _thumb_cache.clear()
        _thumb_cache_total_bytes = 0
        _thumb_cache_hits = 0
        _thumb_cache_misses = 0
        _thumb_cache_sets = 0
        _thumb_cache_evictions = 0

    with _missing_placeholder_lock:
        for img in _missing_placeholder_cache.values():
            img.close()
        _missing_placeholder_cache.clear()
        _missing_placeholder_logged.clear()

    _load_asset_image_ref_cached.cache_clear()
    clear_resolved_path_cache()
    _asset_mirror().clear_memos()
    _resolved_existing_cache.clear()
    _composed_image_cache.clear()

    from src.sekai.base.image_info import _asset_alpha_bounds

    _asset_alpha_bounds.cache_clear()

    from src.sekai.profile.custom_profile.cache import clear_custom_profile_caches
    from src.sekai.skia_renderer.canvas import clear_native_renderer_caches
    from src.sekai.skia_renderer.fragment_cache import clear_native_fragment_cache
    from src.sekai.skia_renderer.payload_cache import clear_skia_payload_cache

    clear_skia_payload_cache()
    clear_native_fragment_cache()
    clear_native_renderer_caches()
    clear_custom_profile_caches()


def shutdown_utils() -> None:
    """关闭 utils 模块持有的全局资源（线程池、图片缓存、临时文件）"""
    _default_pool_executor.shutdown(wait=False)
    cleanup_expired_tmp_files()
    clear_runtime_memory_caches()
