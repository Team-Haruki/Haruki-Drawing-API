"""Region-aware custom-profile resource paths, shared by both backends.

Every ``<cc>-assets/<startapp|ondemand>/...`` path is also a bucket key: a node on ``assets.source=mirror``
keeps no rsynced ``<cc>-assets`` tree, so such a candidate that is not on disk resolves through the asset
mirror exactly like every other endpoint's assets (``custom_profile/`` fonts, backgrounds and shapes as well
as the card, honor and stamp images a request references).
"""

from collections.abc import Iterable
import logging
from pathlib import Path

logger = logging.getLogger(__name__)

REGION_CODES = {"cn", "jp", "tw", "en", "kr"}
REGION_ASSET_MODES = {"startapp", "ondemand"}


def _require_path(name: str, path: Path | None, *, is_file: bool = False) -> Path:
    if path is None:
        raise RuntimeError(f"drawing.{name} is not configured")
    if not path.exists():
        raise FileNotFoundError(f"drawing.{name} does not exist: {path}")
    if is_file and not path.is_file():
        raise RuntimeError(f"drawing.{name} must be a file: {path}")
    if not is_file and not path.is_dir():
        raise RuntimeError(f"drawing.{name} must be a directory: {path}")
    return path


def _expand_region_path(path: Path, region: str) -> Path:
    raw = str(path)
    if "{region}" not in raw:
        return path
    return Path(raw.replace("{region}", region))


def _region_path_candidates(path: Path, region: str) -> list[Path]:
    expanded = _expand_region_path(path, region)
    if expanded != path:
        return [expanded]

    candidates: list[Path] = []
    parts = list(path.parts)
    for index, part in enumerate(parts):
        if part in REGION_CODES:
            replaced = parts.copy()
            replaced[index] = region
            candidates.append(Path(*replaced))
        if part.endswith("-assets") and part[: -len("-assets")] in REGION_CODES:
            replaced = parts.copy()
            replaced[index] = f"{region}-assets"
            candidates.append(Path(*replaced))
    candidates.append(path)

    seen: set[Path] = set()
    result: list[Path] = []
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        result.append(candidate)
    return result


def _require_region_path(name: str, path: Path | None, region: str, *, is_file: bool = False) -> Path:
    if path is None:
        raise RuntimeError(f"drawing.{name} is not configured")
    candidates = _region_path_candidates(path, region)
    for candidate in candidates:
        if candidate.exists():
            return _require_path(name, candidate, is_file=is_file)
    if not is_file and bucket_asset_key(candidates[0]) is not None and asset_mirror_enabled():
        # Absent bucket-backed directory: every file under it is fetched on demand (asset_file).
        return candidates[0]
    return _require_path(name, candidates[0], is_file=is_file)


def _optional_region_file(name: str, path: Path | None, region: str) -> Path | None:
    if path is None:
        return None
    candidates = _region_path_candidates(path, region)
    for candidate in candidates:
        if candidate.exists():
            if not candidate.is_file():
                raise RuntimeError(f"drawing.{name} must be a file: {candidate}")
            return candidate
    if candidates:
        logger.warning("optional drawing.%s missing: %s", name, candidates[0])
        return None
    return None


def bucket_asset_key(path: Path | str) -> str | None:
    """Logical ``asset/<cc>-assets/<mode>/<rel>`` key of a local candidate path, or ``None`` if it is not one.

    Any ``<cc>-assets/<startapp|ondemand>/`` segment marks a bucket asset, whichever data root the candidate
    was built under; the mirror's key map validates the rest (and refuses traversal).
    """
    parts = Path(path).parts
    for index in range(len(parts) - 2):
        package = parts[index]
        if (
            package.endswith("-assets")
            and package.removesuffix("-assets") in REGION_CODES
            and parts[index + 1] in REGION_ASSET_MODES
        ):
            return "/".join(("asset", *parts[index:]))
    return None


def asset_mirror_enabled() -> bool:
    from src.assets.mirror import NullMirror, get_asset_mirror

    return not isinstance(get_asset_mirror(), NullMirror)


def asset_file(path: Path) -> Path | None:
    """``path`` when it exists locally, else its bucket copy through the asset mirror (``None`` on a miss).

    With ``assets.source=local`` the mirror is a ``NullMirror`` and this is exactly ``path.exists()``.
    """
    if path.exists():
        return path
    key = bucket_asset_key(path)
    return None if key is None else mirror_asset_file(key)


def mirror_asset_file(key: str) -> Path | None:
    """Local copy of bucket ``key`` (fetched on a miss); ``None`` with no mirror or when the bucket lacks it."""
    from src.assets.mirror import get_asset_mirror

    fetched = get_asset_mirror().ensure_local(key)
    return fetched if fetched is not None and fetched.is_file() else None


def first_asset_file(paths: Iterable[Path]) -> Path | None:
    """First candidate that exists locally or in the bucket, in candidate order.

    Order is kept per candidate (not "all local, then all remote") so precedence never depends on which
    files a node happens to hold: a full local tree and an empty one pick the same file. Candidates built
    under different data roots share one bucket key, which is asked for only once.
    """
    tried: set[str] = set()
    for path in paths:
        if path.exists():
            return path
        key = bucket_asset_key(path)
        if key is None or key in tried:
            continue
        tried.add(key)
        if (found := mirror_asset_file(key)) is not None:
            return found
    return None
