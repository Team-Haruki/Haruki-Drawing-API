"""Manifest-version sources for the mirror path segment (E5, addendum B7).

Precedence: `assets.mirror.manifest_version_file` (first line, polled) -> `assets.manifest_version`
(`HARUKI_ASSETS__MANIFEST_VERSION`) -> `assets.mirror.manifest_version`
(`HARUKI_ASSETS__MIRROR__MANIFEST_VERSION`) -> `"v0"`. Drawing only consumes the value; producing it is an
Asset-Updater follow-up outside this programme.
"""

from __future__ import annotations

from collections.abc import Callable
import logging
from pathlib import Path
import threading
import time
from typing import TYPE_CHECKING, Protocol, runtime_checkable

from src.assets.keymap import DEFAULT_VERSION, sanitize_version

if TYPE_CHECKING:
    from src.settings import AssetsSettings

logger = logging.getLogger("src.assets.version")


@runtime_checkable
class ManifestVersionSource(Protocol):
    def current(self) -> str: ...


def _valid_or_none(value: object) -> str | None:
    if not isinstance(value, str) or not value.strip():
        return None
    sanitized = sanitize_version(value)
    if sanitized == DEFAULT_VERSION and value.strip() != DEFAULT_VERSION:
        return None
    return sanitized


class StaticVersion:
    """`assets.manifest_version` -> `assets.mirror.manifest_version` -> `"v0"`; invalid values are skipped."""

    __slots__ = ("_value",)

    def __init__(self, *candidates: str | None) -> None:
        self._value = DEFAULT_VERSION
        for candidate in candidates:
            if (valid := _valid_or_none(candidate)) is not None:
                self._value = valid
                break

    @classmethod
    def from_settings(cls, assets: AssetsSettings) -> StaticVersion:
        return cls(assets.manifest_version, assets.mirror.manifest_version)

    def current(self) -> str:
        return self._value


class FileVersion:
    """First line of a per-node file, re-read at most every `poll_seconds`; falls back to `fallback`.

    A missing, empty, unreadable or invalid file yields the fallback value (logged once per distinct problem).
    """

    __slots__ = ("_clock", "_fallback", "_last_poll", "_lock", "_logged", "_path", "_poll_seconds", "_value")

    def __init__(
        self,
        path: Path | str,
        *,
        fallback: ManifestVersionSource,
        poll_seconds: float = 30.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._path = Path(path)
        self._fallback = fallback
        self._poll_seconds = max(0.0, float(poll_seconds))
        self._clock = clock
        self._lock = threading.Lock()
        self._last_poll: float | None = None
        self._value: str | None = None
        self._logged: str | None = None

    @property
    def path(self) -> Path:
        return self._path

    def current(self) -> str:
        now = self._clock()
        with self._lock:
            if self._last_poll is None or now - self._last_poll >= self._poll_seconds:
                self._last_poll = now
                self._value = self._read()
            value = self._value
        return value if value is not None else self._fallback.current()

    def _read(self) -> str | None:
        try:
            with self._path.open(encoding="utf-8") as handle:
                first_line = handle.readline()
        except FileNotFoundError:
            self._log_once("missing", logging.INFO)
            return None
        except (OSError, UnicodeDecodeError) as exc:
            self._log_once(f"unreadable:{type(exc).__name__}", logging.WARNING)
            return None
        valid = _valid_or_none(first_line)
        if valid is None:
            self._log_once(f"invalid:{first_line.strip()[:80]!r}", logging.WARNING)
            return None
        self._logged = None
        return valid

    def _log_once(self, problem: str, level: int) -> None:
        if self._logged == problem:
            return
        self._logged = problem
        logger.log(level, "asset_mirror.manifest_version_file path=%s problem=%s (using fallback)", self._path, problem)


def build_version_source(assets: AssetsSettings) -> ManifestVersionSource:
    """Compose the B7 precedence chain from settings."""
    static = StaticVersion.from_settings(assets)
    mirror = assets.mirror
    if mirror.manifest_version_file is None:
        return static
    return FileVersion(mirror.manifest_version_file, fallback=static, poll_seconds=mirror.manifest_version_poll_seconds)
