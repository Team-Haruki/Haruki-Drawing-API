"""Image metadata service with a native implementation and an isolated legacy fallback."""

from functools import lru_cache
from importlib import import_module
from pathlib import Path


def _native():
    try:
        native = import_module("haruki_skia_renderer")
    except ImportError:
        return None
    return native if getattr(native, "IMAGE_INFO_CAPABILITY", 0) >= 2 else None


def probe_asset(path: Path) -> tuple[tuple[int, int], str]:
    native = _native()
    if native is None:
        from .pillow_image_info import probe_asset as legacy_probe

        return legacy_probe(path)
    try:
        result = native.asset_image_info(str(path.parent), path.name)
    except ValueError as exc:
        raise OSError(str(exc)) from exc
    except (AttributeError, RuntimeError, OSError):
        from .pillow_image_info import probe_asset as legacy_probe

        return legacy_probe(path)
    return (int(result["width"]), int(result["height"])), str(result["mode"])


def probe_encoded(data: bytes) -> tuple[tuple[int, int], str]:
    native = _native()
    if native is None:
        from .pillow_image_info import probe_encoded as legacy_probe

        return legacy_probe(data)
    try:
        result = native.encoded_image_info(data)
    except ValueError as exc:
        raise OSError(str(exc)) from exc
    except (AttributeError, RuntimeError, OSError):
        from .pillow_image_info import probe_encoded as legacy_probe

        return legacy_probe(data)
    return (int(result["width"]), int(result["height"])), str(result["mode"])


@lru_cache(maxsize=2048)
def _asset_alpha_bounds(path: Path, mtime_ns: int, size: int, scan):
    # Keep only geometry, never the decoded image. Re-stat even a reused AssetImageRef.
    return scan(str(path.parent), path.name)


def probe_alpha_bounds(source) -> tuple[int, int, int, int] | None:
    """Scan alpha natively; only the rectangle crosses the renderer boundary."""
    from .image_source import AssetImageRef, EncodedImageRef

    native = _native()
    if native is not None and getattr(native, "ALPHA_BOUNDS_CAPABILITY", 0) >= 1:
        try:
            if isinstance(source, AssetImageRef):
                try:
                    stat = source.path.stat()
                except OSError:
                    # Keep the native missing/denied-file error instead of turning a
                    # removed file into a Pillow placeholder through the legacy adapter.
                    bounds = native.asset_alpha_bounds(str(source.path.parent), source.path.name)
                else:
                    bounds = _asset_alpha_bounds(source.path, stat.st_mtime_ns, stat.st_size, native.asset_alpha_bounds)
            elif isinstance(source, EncodedImageRef):
                bounds = native.encoded_alpha_bounds(source.data)
            else:
                raise TypeError("alpha bounds requires an asset or encoded reference")
            return tuple(bounds) if bounds is not None else None
        except ValueError as exc:
            raise OSError(str(exc)) from exc
        except (AttributeError, RuntimeError, OSError):
            pass
    from .pillow_image_info import probe_alpha_bounds as legacy_probe

    return legacy_probe(source)


def probe_foreground_bounds(source, *, detect_width: int = 700) -> tuple[int, int, int, int] | None:
    """Locate foreground against per-row edge colors, retaining only crop metadata."""
    from .image_source import AssetImageRef, EncodedImageRef, MissingImageRef, is_pillow_image

    if not 1 <= detect_width <= 4096:
        raise ValueError("detect_width must be between 1 and 4096")
    native = _native()
    if native is not None and getattr(native, "FOREGROUND_BOUNDS_CAPABILITY", 0) >= 1 and not is_pillow_image(source):
        try:
            if isinstance(source, MissingImageRef):
                from src.sekai.skia_renderer.placeholder import render_placeholder

                source = render_placeholder(source)
            if isinstance(source, AssetImageRef):
                bounds = native.asset_foreground_bounds(str(source.path.parent), source.path.name, detect_width)
            elif isinstance(source, EncodedImageRef):
                bounds = native.encoded_foreground_bounds(source.data, detect_width)
            else:
                raise TypeError("foreground bounds requires an image source")
            return tuple(bounds) if bounds is not None else None
        except ValueError as exc:
            raise OSError(str(exc)) from exc
        except (AttributeError, RuntimeError, OSError):
            pass
    from .pillow_image_info import probe_foreground_bounds as legacy_probe

    return legacy_probe(source, detect_width=detect_width)
