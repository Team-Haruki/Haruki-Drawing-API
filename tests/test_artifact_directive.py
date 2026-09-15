"""Directive parsing (plan §8.1/§8.2): I4 zero-read when absent, every regex and cap boundary."""

from __future__ import annotations

from collections.abc import Iterator, Mapping

import pytest

from src.artifact.directive import (
    DEFAULT_GROUP,
    DEFAULT_USER_ID,
    DirectiveError,
    RenderCacheDirective,
    is_artifact_requested,
    parse_render_cache_directive,
)

TTL_MAX = 30 * 86400
KEY = "0123456789abcdef"


class OnlyArtifactHeader(Mapping[str, str]):
    """Raises on any key other than `x-haruki-artifact` (I4 is testable)."""

    def __init__(self, value: str | None) -> None:
        self._value = value

    def __getitem__(self, key: str) -> str:
        if key != "x-haruki-artifact":
            raise AssertionError(f"read forbidden header {key!r}")
        if self._value is None:
            raise KeyError(key)
        return self._value

    def __iter__(self) -> Iterator[str]:
        raise AssertionError("iteration forbidden")

    def __len__(self) -> int:
        raise AssertionError("len forbidden")


def _headers(**overrides: str | None) -> dict[str, str]:
    base: dict[str, str | None] = {
        "x-haruki-artifact": "1",
        "x-haruki-cache-key": KEY,
        "x-haruki-cache-ttl": "3600",
        "x-haruki-cache-key-version": "3",
        "x-haruki-api-path": "/api/pjsk/honor",
    }
    for name, value in overrides.items():
        base[name.replace("_", "-")] = value
    return {k: v for k, v in base.items() if v is not None}


def _parse(**overrides: str | None) -> RenderCacheDirective | None:
    return parse_render_cache_directive(_headers(**overrides), ttl_max=TTL_MAX)


def _reject(header: str, reason: str, **overrides: str | None) -> None:
    with pytest.raises(DirectiveError) as info:
        _parse(**overrides)
    assert info.value.header == header
    assert info.value.reason == reason
    assert str(info.value) == f"invalid {header}: {reason}"


@pytest.mark.parametrize("value", [None, "0", " 0 "])
def test_absent_or_zero_reads_no_other_header(value: str | None) -> None:
    assert parse_render_cache_directive(OnlyArtifactHeader(value), ttl_max=TTL_MAX) is None
    assert is_artifact_requested(OnlyArtifactHeader(value)) is False


def test_is_artifact_requested_only_for_one() -> None:
    assert is_artifact_requested(OnlyArtifactHeader("1")) is True
    assert is_artifact_requested(OnlyArtifactHeader("true")) is False


@pytest.mark.parametrize("value", ["2", "true", "yes", "01", ""])
def test_other_artifact_values_are_rejected(value: str) -> None:
    _reject("X-Haruki-Artifact", "malformed", x_haruki_artifact=value)


def test_valid_directive_with_defaults() -> None:
    directive = _parse()
    assert directive == RenderCacheDirective(
        cache_key=KEY,
        key_version=3,
        ttl_seconds=3600,
        store=True,
        group=DEFAULT_GROUP,
        api_path="api/pjsk/honor",
        user_id=DEFAULT_USER_ID,
    )
    assert directive.group == "pjsk"
    assert directive.user_id == "public"


def test_explicit_optional_headers() -> None:
    directive = _parse(
        x_haruki_cache_store="0",
        x_haruki_cache_group="image.cache-1_x",
        x_haruki_user_id="user.42-a_b",
    )
    assert directive is not None
    assert directive.store is False
    assert directive.group == "image.cache-1_x"
    assert directive.user_id == "user.42-a_b"
    assert _parse(x_haruki_cache_store="1").store is True


def test_empty_optional_headers_fall_back_to_defaults() -> None:
    directive = _parse(x_haruki_cache_store="", x_haruki_cache_group=" ", x_haruki_user_id="")
    assert (directive.store, directive.group, directive.user_id) == (True, "pjsk", "public")


def test_frozen_dataclass() -> None:
    directive = _parse()
    with pytest.raises(AttributeError):
        directive.cache_key = "x"  # type: ignore[misc]


# ---- X-Haruki-Cache-Key ----


@pytest.mark.parametrize("key", ["a" * 16, "0" * 128, "0123456789abcdef" * 8])
def test_cache_key_accepted_boundaries(key: str) -> None:
    assert _parse(x_haruki_cache_key=key).cache_key == key


def test_cache_key_missing() -> None:
    _reject("X-Haruki-Cache-Key", "missing", x_haruki_cache_key=None)
    _reject("X-Haruki-Cache-Key", "missing", x_haruki_cache_key="")


@pytest.mark.parametrize("key", ["a" * 15, "0" * 129, "ABCDEF0123456789", "g" * 16, KEY + "\n" + "0", KEY + "-"])
def test_cache_key_malformed(key: str) -> None:
    _reject("X-Haruki-Cache-Key", "malformed", x_haruki_cache_key=key)


# ---- X-Haruki-Cache-TTL ----


@pytest.mark.parametrize(("ttl", "expected"), [("0", 0), (str(TTL_MAX), TTL_MAX), ("1", 1)])
def test_ttl_boundaries(ttl: str, expected: int) -> None:
    assert _parse(x_haruki_cache_ttl=ttl).ttl_seconds == expected


def test_ttl_over_cap() -> None:
    _reject("X-Haruki-Cache-TTL", "too_large", x_haruki_cache_ttl=str(TTL_MAX + 1))
    _reject("X-Haruki-Cache-TTL", "too_large", x_haruki_cache_ttl="9" * 19)


@pytest.mark.parametrize("ttl", ["-1", "+5", "1.5", "1_0", "abc", "9" * 20, "٣"])
def test_ttl_malformed(ttl: str) -> None:
    _reject("X-Haruki-Cache-TTL", "malformed", x_haruki_cache_ttl=ttl)


def test_ttl_missing() -> None:
    _reject("X-Haruki-Cache-TTL", "missing", x_haruki_cache_ttl=None)


def test_ttl_cap_argument_is_respected() -> None:
    headers = _headers(x_haruki_cache_ttl="61")
    with pytest.raises(DirectiveError) as info:
        parse_render_cache_directive(headers, ttl_max=60)
    assert info.value.reason == "too_large"
    assert parse_render_cache_directive(_headers(x_haruki_cache_ttl="60"), ttl_max=60).ttl_seconds == 60
    # a negative cap never lets a positive TTL through
    with pytest.raises(DirectiveError):
        parse_render_cache_directive(_headers(x_haruki_cache_ttl="1"), ttl_max=-5)


# ---- X-Haruki-Cache-Key-Version ----


def test_key_version_boundaries() -> None:
    assert _parse(x_haruki_cache_key_version="0").key_version == 0
    assert _parse(x_haruki_cache_key_version="10000").key_version == 10000
    _reject("X-Haruki-Cache-Key-Version", "too_large", x_haruki_cache_key_version="10001")
    _reject("X-Haruki-Cache-Key-Version", "malformed", x_haruki_cache_key_version="-1")
    _reject("X-Haruki-Cache-Key-Version", "missing", x_haruki_cache_key_version=None)


# ---- X-Haruki-Api-Path ----


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("/api/pjsk/honor", "api/pjsk/honor"),
        ("api/pjsk/honor", "api/pjsk/honor"),
        ("a", "a"),
        ("/" + "/".join(["s"] * 16), "/".join(["s"] * 16)),
        ("/" + "a" * 128, "a" * 128),
        ("v1.2/some_path-x", "v1.2/some_path-x"),
        ("...", "..."),
        ("a/.hidden/b", "a/.hidden/b"),
    ],
)
def test_api_path_accepted(raw: str, expected: str) -> None:
    assert _parse(x_haruki_api_path=raw).api_path == expected


@pytest.mark.parametrize(
    "raw",
    [
        "//api/pjsk",  # only ONE leading slash is stripped
        "/api//pjsk",
        "/api/pjsk/",
        "/".join(["s"] * 17),
        "api/pj sk",
        "api\\pjsk",
        "api/pjsk?x=1",
        "api/%2e%2e",
        "/",  # strips to "", which the regex rejects
    ],
)
def test_api_path_malformed(raw: str) -> None:
    _reject("X-Haruki-Api-Path", "malformed", x_haruki_api_path=raw)


def test_api_path_too_long() -> None:
    _reject("X-Haruki-Api-Path", "too_long", x_haruki_api_path="a" * 129)
    _reject("X-Haruki-Api-Path", "too_long", x_haruki_api_path="/" + "a" * 129)


@pytest.mark.parametrize("raw", [".", "..", "/..", "api/../x", "api/./x", "api/pjsk/..", "./api"])
def test_api_path_dot_segments(raw: str) -> None:
    _reject("X-Haruki-Api-Path", "dot_segment", x_haruki_api_path=raw)


def test_api_path_missing() -> None:
    _reject("X-Haruki-Api-Path", "missing", x_haruki_api_path=None)


# ---- optional headers ----


@pytest.mark.parametrize("value", ["2", "true", "00", "-1"])
def test_store_malformed(value: str) -> None:
    _reject("X-Haruki-Cache-Store", "malformed", x_haruki_cache_store=value)


def test_group_boundaries() -> None:
    assert _parse(x_haruki_cache_group="g" * 64).group == "g" * 64
    _reject("X-Haruki-Cache-Group", "malformed", x_haruki_cache_group="g" * 65)
    _reject("X-Haruki-Cache-Group", "malformed", x_haruki_cache_group="a/b")
    _reject("X-Haruki-Cache-Group", "malformed", x_haruki_cache_group="a b")


def test_user_id_boundaries() -> None:
    assert _parse(x_haruki_user_id="u" * 128).user_id == "u" * 128
    _reject("X-Haruki-User-Id", "malformed", x_haruki_user_id="u" * 129)
    _reject("X-Haruki-User-Id", "malformed", x_haruki_user_id="a/b")
    _reject("X-Haruki-User-Id", "malformed", x_haruki_user_id="用户")


def test_header_values_are_stripped() -> None:
    directive = _parse(x_haruki_artifact=" 1 ", x_haruki_cache_key=f" {KEY} ", x_haruki_cache_ttl=" 5 ")
    assert directive.cache_key == KEY
    assert directive.ttl_seconds == 5


def test_starlette_headers_are_accepted() -> None:
    from starlette.datastructures import Headers

    headers = Headers(headers={name.title(): value for name, value in _headers().items()})
    assert parse_render_cache_directive(headers, ttl_max=TTL_MAX).cache_key == KEY
