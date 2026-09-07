"""Region-aware custom-profile resource paths, shared by both backends."""

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

REGION_CODES = {"cn", "jp", "tw", "en", "kr"}


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
