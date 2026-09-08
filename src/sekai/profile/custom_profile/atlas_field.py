"""Static TMP atlas alpha fields in the existing bounded sprite/atlas pool."""

from collections.abc import Callable
import importlib
from pathlib import Path

from .cache import MISSING, SPRITE_ATLAS_CACHE, file_signature
from .gray_field import MAX_FIELD_PIXELS, GrayField
from .limits import ensure_raster_size


def load_atlas_alpha(path: Path, *, max_pixels: int, legacy_decode: Callable) -> GrayField:
    # Paths originate in extracted, operator-configured TMP metadata. Its atlas
    # directory is the confinement root, which need not be under ASSETS_BASE_DIR.
    limit = min(max_pixels, MAX_FIELD_PIXELS)
    signature = file_signature(path)  # Deletion must never hit a stale entry.
    key = (str(path), *signature, "atlas_alpha_gray8")
    cached = SPRITE_ATLAS_CACHE.get(key)
    if cached is not MISSING:
        ensure_raster_size(cached.size, max_pixels=limit, label="TMP atlas alpha")
        return cached
    try:
        native = importlib.import_module("haruki_skia_renderer")
        if getattr(native, "ALPHA_FIELD_CAPABILITY", 0) < 1:
            raise ImportError("native atlas alpha API is unavailable")
        field = GrayField(*native.asset_alpha_field(str(path.parent), path.name, limit))
    except (ImportError, AttributeError, RuntimeError):
        from .pillow_fields import from_pillow

        # Never store legacy pixels under the native key. Every recovery, including
        # a legacy cache hit, must retain its Pillow telemetry and import boundary.
        image = legacy_decode(path)
        ensure_raster_size(image.size, max_pixels=limit, label="TMP atlas alpha")
        return from_pillow(image)
    ensure_raster_size(field.size, max_pixels=limit, label="TMP atlas alpha")
    if file_signature(path) == signature:
        SPRITE_ATLAS_CACHE.set(key, field)
    return field
