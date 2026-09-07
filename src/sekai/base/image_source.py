"""Immutable image references shared by renderers; importing this module never loads Pillow."""

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path


@dataclass(frozen=True, slots=True)
class AssetImageRef:
    """Header-only image reference for renderers that can load assets themselves.

    ``mtime_ns``/``file_size`` capture the file identity at probe time so cache keys
    derived from the ref (e.g. ``deterministic_hash`` of painter ops) invalidate when
    the asset is hot-reloaded with the same dimensions.
    """

    path: Path
    size: tuple[int, int]
    mode: str
    mtime_ns: int = 0
    file_size: int = 0

    @property
    def width(self) -> int:
        return self.size[0]

    @property
    def height(self) -> int:
        return self.size[1]

    @property
    def readonly(self) -> int:
        return 1

    @property
    def _haruki_pristine_asset_path(self) -> str:
        return str(self.path)


@dataclass(frozen=True, slots=True)
class EncodedImageRef:
    """Encoded (PNG/JPEG/...) image bytes with header-probed dimensions.

    The Skia path ships ``data`` straight to Rust as an encoded mem image; the Pillow
    fallback decodes on demand in ``Painter._impl_paste*``. Use for images that arrive
    already encoded (base64 payloads, downloads) to skip the Python-side decode."""

    data: bytes
    size: tuple[int, int]
    mode: str

    @property
    def width(self) -> int:
        return self.size[0]

    @property
    def height(self) -> int:
        return self.size[1]


@dataclass(frozen=True, slots=True)
class MissingImageRef:
    """Lazy placeholder recipe, rasterized by the selected backend at replay time."""

    variant: str = "square"

    @property
    def size(self) -> tuple[int, int]:
        from .placeholder import SIZES

        return SIZES.get(self.variant, SIZES["square"])

    @property
    def width(self) -> int:
        return self.size[0]

    @property
    def height(self) -> int:
        return self.size[1]

    @property
    def mode(self) -> str:
        return "RGBA"


@lru_cache(maxsize=6)
def missing_image_ref(variant: str) -> MissingImageRef:
    """Intern the six immutable recipes; this stores no decoded or encoded pixels."""
    return MissingImageRef(variant)


def get_pristine_image_asset_path(image: object) -> Path | None:
    """Return the backing file only when ``image`` still matches its loaded pixels."""
    path = getattr(image, "_haruki_pristine_asset_path", None)
    if not isinstance(path, str) or not path or getattr(image, "readonly", 0) != 1:
        return None
    return Path(path)


_resolved_existing_cache: dict[str, Path] = {}


def resolve_existing_asset_path(path: Path) -> Path | None:
    """``path.resolve(strict=True)`` memoized, or ``None`` if it does not exist.

    For the IR builder, which must map an absolute asset path back to a path relative to the assets
    root — once per image NODE, so a 696-jacket music list paid 13k lstat calls for it. The paths it
    is handed are already resolved (they come from ``_resolve_and_stat``), so the walk is redundant
    normalization; but it is kept on the first sighting rather than dropped, because symlink
    normalization is what makes the caller's later ``relative_to`` escape check meaningful.

    Only successes are cached. A missing file re-resolves every time — that is the rare path, and
    caching a negative would keep an asset invisible after it lands on disk.
    """
    key = str(path)
    cached = _resolved_existing_cache.get(key)
    if cached is not None:
        if cached.is_file():
            return cached
        _resolved_existing_cache.pop(key, None)
    try:
        resolved = path.resolve(strict=True)
    except (OSError, ValueError):
        return None
    if not resolved.is_file():
        return None
    if len(_resolved_existing_cache) >= 32_768:
        _resolved_existing_cache.clear()
    _resolved_existing_cache[key] = resolved
    return resolved


def is_pillow_image(value: object) -> bool:
    """Recognize caller-owned legacy pixels without importing Pillow to inspect refs."""
    import sys

    module = sys.modules.get("PIL.Image")
    image_type = getattr(module, "Image", ())
    return isinstance(value, image_type)


from typing import Protocol


class ImageSource(Protocol):
    """Read-only layout information shared by lazy refs and legacy pixel images."""

    @property
    def size(self) -> tuple[int, int]: ...

    @property
    def width(self) -> int: ...

    @property
    def height(self) -> int: ...

    @property
    def mode(self) -> str: ...
