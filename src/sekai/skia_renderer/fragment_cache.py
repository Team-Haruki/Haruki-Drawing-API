"""Bounded native raster fragments for explicitly cacheable shared Canvas subtrees.

These are sub-pages, never responses: request watermarks remain in the parent scene.
Direct subtree keys include the complete lowered drawing. Canvas fast lookups use the
caller's explicit composed-cache key (request/layout/time inputs), canvas size and renderer
context; both paths validate asset/font signatures and renderer code.
Native decoding materializes immutable premultiplied RGBA once inside this pool.
PNG remains the transport for older extensions or oversized fragments; neither path uses Pillow.
"""

from __future__ import annotations

from dataclasses import dataclass
import hashlib
import json
from pathlib import Path

from src.sekai.base.image_source import EncodedImageRef, NativeRasterImageRef
from src.sekai.base.utils import collect_asset_signatures, get_image_asset_signature, renderer_code_fingerprint
from src.settings import COMPOSED_IMAGE_CACHE_MAX_BYTES, COMPOSED_IMAGE_CACHE_SIZE, COMPOSED_IMAGE_CACHE_TTL_SECONDS

from .payload_cache import _SkiaPayloadCache

_cache = _SkiaPayloadCache(COMPOSED_IMAGE_CACHE_SIZE, COMPOSED_IMAGE_CACHE_MAX_BYTES, COMPOSED_IMAGE_CACHE_TTL_SECONDS)


@dataclass(frozen=True)
class _Fragment:
    image: EncodedImageRef | NativeRasterImageRef
    dependencies: tuple[tuple[str, str, dict | None], ...]
    bg_hour: float | None = None


def get_native_fragment_cached(key: str, *, bg_hour: float | None = None, signatures: dict | None = None):
    entry = _cache.get(key)
    if entry is None or (entry.bg_hour is not None and entry.bg_hour != bg_hour):
        return None
    for root, path, signature in entry.dependencies:
        identity = (root, path)
        if signatures is None:
            current = get_image_asset_signature(Path(root), path)
        else:
            if identity not in signatures:
                signatures[identity] = get_image_asset_signature(Path(root), path)
            current = signatures[identity]
        if current != signature:
            return None
    return entry.image


def canvas_fragment_lookup_key(cache_key: str, size: tuple[int, int], options: dict) -> str:
    # The explicit Canvas cache key has the same contract as Painter's composed cache:
    # request/layout inputs and time-dependent terms must be included by its caller.
    material = [cache_key, size, options, renderer_code_fingerprint()]
    return hashlib.sha256(json.dumps(material, sort_keys=True).encode()).hexdigest()


def clear_native_fragment_cache() -> None:
    _cache.clear()


def get_native_fragment_cache_stats() -> dict:
    return _cache.stats()


def fragment_scene_and_key(
    subtree, cache_key: str, *, isolate: bool = False, raster_size=None, sampling="nearest"
) -> tuple[dict, str, tuple]:
    width, height = raster_size or subtree.size
    scene = {
        "version": 2,
        "assets_base_dir": subtree.assets_base_dir,
        "fonts": subtree.fonts,
        "canvas": {"width": width, "height": height},
        "export_format": "png",
        "root": {"type": "Group", "offset": [0, 0], "size": [width, height], "children": list(subtree.nodes)},
    }
    if isolate:
        # Preserve RasterSubscene's strict image preparation and sampling behavior. A plain
        # root Image can use the target raster cache and introduce an extra resampling pass.
        scene["root"] = {
            "type": "RasterSubscene",
            "natural_size": list(subtree.size),
            "pos": [0, 0],
            "dst_size": [width, height],
            "sampling": sampling,
            "children": list(subtree.nodes),
        }
    signatures = collect_asset_signatures(Path(subtree.assets_base_dir), scene)
    # Roles may omit extensions. Stat every resolution candidate, including missing ones,
    # so adding/replacing a font cannot keep an old fragment alive.
    font_dir = Path(subtree.fonts["dir"])
    fonts = [v for k, v in subtree.fonts.items() if k not in {"dir", "extra"} and v]
    fonts.extend(subtree.fonts.get("extra", {}).values())
    font_dependencies = tuple(
        (str(candidate.parent), candidate.name, get_image_asset_signature(candidate.parent, candidate.name))
        for name in fonts
        for suffix in (".otf", ".ttf", ".ttc", "")
        for candidate in (font_dir / (str(name) + suffix),)
    )
    font_signatures = {str(Path(root) / path): signature for root, path, signature in font_dependencies}
    memory_signatures = {key: hashlib.sha256(value).hexdigest() for key, value in subtree.mem_images.items()}
    placeholder_dependencies = tuple(
        dependency for value in subtree.mem_images.values() for dependency in value.dependencies
    )
    material = [
        cache_key,
        renderer_code_fingerprint(),
        scene,
        signatures,
        font_signatures,
        memory_signatures,
        placeholder_dependencies,
    ]
    key = hashlib.sha256(json.dumps(material, sort_keys=True, separators=(",", ":")).encode()).hexdigest()
    dependencies = (
        tuple((subtree.assets_base_dir, path, signature) for path, signature in signatures.items())
        + font_dependencies
        + placeholder_dependencies
    )
    return scene, key, dependencies


def render_cached_native_fragment(
    subtree,
    cache_key: str,
    *,
    lookup_key: str | None = None,
    bg_hour: float | None = None,
    isolate: bool = False,
    raster_size=None,
    sampling="nearest",
) -> EncodedImageRef | NativeRasterImageRef | None:
    # Never hide memory-backed legacy work or alias transient caller-owned rasters.
    if not _cache._enabled():
        return None
    from .placeholder import NativePlaceholderBytes

    if not subtree.asset_backed and not all(
        isinstance(value, NativePlaceholderBytes) for value in subtree.mem_images.values()
    ):
        return None
    scene, key, dependencies = fragment_scene_and_key(
        subtree, cache_key, isolate=isolate, raster_size=raster_size, sampling=sampling
    )
    key = lookup_key or key
    cached = get_native_fragment_cached(key, bg_hour=bg_hour)
    if cached is not None:
        return cached
    from .canvas import load_native_renderer

    native = load_native_renderer()
    result = native.render_scene(json.dumps(scene, separators=(",", ":")).encode(), subtree.mem_images)
    # Preserve font-health reporting on the ordinary path when the configuration is broken.
    if result.get("native_metrics", {}).get("font_fallbacks"):
        return None
    size = raster_size or subtree.size
    fragment = EncodedImageRef(bytes(result["image_bytes"]), size, "RGBA")
    # Decode once on insertion, not once per render. Keep the actual premultiplied
    # pixels produced by Skia's PNG decoder: unpremul round-trips change alpha edges.
    decode = getattr(native, "decode_fragment_rgba", None)
    if decode is not None:
        pixels = decode(fragment.data)
        if pixels is not None:
            fragment = NativeRasterImageRef(pixels, size)
    _cache.set(
        key,
        _Fragment(fragment, dependencies, bg_hour if _has_triangle(scene) else None),
        max(len(fragment.data), size[0] * size[1] * 4) + len(repr(dependencies)),
    )
    return fragment


def _has_triangle(value) -> bool:
    if isinstance(value, dict):
        return value.get("type") == "TriangleBg" or any(_has_triangle(v) for v in value.values())
    if isinstance(value, (list, tuple)):
        return any(_has_triangle(v) for v in value)
    return False
