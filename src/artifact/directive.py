"""Render cache directive: the `X-Haruki-*` request headers Cloud sends in artifact mode (plan §8.1/§8.2).

Invariant I4: when `X-Haruki-Artifact` is absent or `0`, no other `X-Haruki-*` header is read at all.
When it is `1`, every directive header is validated strictly and a violation raises `DirectiveError`,
which the debug middleware turns into a 400 before the route runs.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import re

HEADER_ARTIFACT = "X-Haruki-Artifact"
HEADER_CACHE_KEY = "X-Haruki-Cache-Key"
HEADER_CACHE_TTL = "X-Haruki-Cache-TTL"
HEADER_KEY_VERSION = "X-Haruki-Cache-Key-Version"
HEADER_API_PATH = "X-Haruki-Api-Path"
HEADER_CACHE_STORE = "X-Haruki-Cache-Store"
HEADER_CACHE_GROUP = "X-Haruki-Cache-Group"
HEADER_USER_ID = "X-Haruki-User-Id"

DEFAULT_GROUP = "pjsk"
DEFAULT_USER_ID = "public"
KEY_VERSION_MAX = 10_000
API_PATH_MAX_CHARS = 128

_CACHE_KEY_RE = re.compile(r"[0-9a-f]{16,128}")
_UINT_RE = re.compile(r"[0-9]{1,19}")
_API_PATH_RE = re.compile(r"[A-Za-z0-9._-]+(?:/[A-Za-z0-9._-]+){0,15}")
_GROUP_RE = re.compile(r"[A-Za-z0-9._-]{1,64}")
_USER_ID_RE = re.compile(r"[A-Za-z0-9._-]{1,128}")


@dataclass(frozen=True, slots=True)
class RenderCacheDirective:
    cache_key: str
    key_version: int
    ttl_seconds: int
    store: bool
    group: str
    api_path: str
    user_id: str


class DirectiveError(ValueError):
    """An invalid directive header. `reason` is a stable slug, not prose."""

    def __init__(self, header: str, reason: str) -> None:
        super().__init__(f"invalid {header}: {reason}")
        self.header = header
        self.reason = reason


def _get(headers: Mapping[str, str], name: str) -> str | None:
    value = headers.get(name.lower())
    if value is None:
        return None
    return value.strip()


def _required(headers: Mapping[str, str], name: str) -> str:
    value = _get(headers, name)
    if not value:
        raise DirectiveError(name, "missing")
    return value


def _bounded_int(headers: Mapping[str, str], name: str, maximum: int) -> int:
    raw = _required(headers, name)
    if _UINT_RE.fullmatch(raw) is None:
        raise DirectiveError(name, "malformed")
    value = int(raw)
    if value > maximum:
        raise DirectiveError(name, "too_large")
    return value


def _api_path(headers: Mapping[str, str]) -> str:
    raw = _required(headers, HEADER_API_PATH)
    path = raw.removeprefix("/")
    if len(path) > API_PATH_MAX_CHARS:
        raise DirectiveError(HEADER_API_PATH, "too_long")
    if _API_PATH_RE.fullmatch(path) is None:
        raise DirectiveError(HEADER_API_PATH, "malformed")
    if any(segment in {".", ".."} for segment in path.split("/")):
        raise DirectiveError(HEADER_API_PATH, "dot_segment")
    return path


def _optional_token(headers: Mapping[str, str], name: str, pattern: re.Pattern[str], default: str) -> str:
    value = _get(headers, name)
    if not value:
        return default
    if pattern.fullmatch(value) is None:
        raise DirectiveError(name, "malformed")
    return value


def _store(headers: Mapping[str, str]) -> bool:
    value = _get(headers, HEADER_CACHE_STORE)
    if value is None or value == "":
        return True
    if value not in {"0", "1"}:
        raise DirectiveError(HEADER_CACHE_STORE, "malformed")
    return value == "1"


def is_artifact_requested(headers: Mapping[str, str]) -> bool:
    """True only for `X-Haruki-Artifact: 1`; reads that one header and nothing else."""
    return _get(headers, HEADER_ARTIFACT) == "1"


def parse_render_cache_directive(headers: Mapping[str, str], *, ttl_max: int) -> RenderCacheDirective | None:
    """Return the directive, `None` in bytes mode, or raise `DirectiveError` for an invalid header.

    `headers` must be keyed by lowercase names (Starlette's `Headers` is case-insensitive either way).
    """
    artifact = _get(headers, HEADER_ARTIFACT)
    if artifact is None or artifact == "0":
        return None
    if artifact != "1":
        raise DirectiveError(HEADER_ARTIFACT, "malformed")

    cache_key = _required(headers, HEADER_CACHE_KEY)
    if _CACHE_KEY_RE.fullmatch(cache_key) is None:
        raise DirectiveError(HEADER_CACHE_KEY, "malformed")
    ttl_seconds = _bounded_int(headers, HEADER_CACHE_TTL, max(0, int(ttl_max)))
    key_version = _bounded_int(headers, HEADER_KEY_VERSION, KEY_VERSION_MAX)
    api_path = _api_path(headers)
    store = _store(headers)
    group = _optional_token(headers, HEADER_CACHE_GROUP, _GROUP_RE, DEFAULT_GROUP)
    user_id = _optional_token(headers, HEADER_USER_ID, _USER_ID_RE, DEFAULT_USER_ID)
    return RenderCacheDirective(
        cache_key=cache_key,
        key_version=key_version,
        ttl_seconds=ttl_seconds,
        store=store,
        group=group,
        api_path=api_path,
        user_id=user_id,
    )
