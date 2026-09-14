"""Manifest-version sources and the addendum B7 precedence chain."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest

from src.assets.version import FileVersion, ManifestVersionSource, StaticVersion, build_version_source
from src.settings import AssetMirrorSettings, AssetsSettings


class _Clock:
    def __init__(self) -> None:
        self.now = 1000.0

    def __call__(self) -> float:
        return self.now


def _assets(**kwargs) -> AssetsSettings:
    mirror = kwargs.pop("mirror", {})
    return AssetsSettings(mirror=AssetMirrorSettings(**mirror), **kwargs)


def test_static_defaults_to_v0() -> None:
    source = build_version_source(_assets())
    assert isinstance(source, StaticVersion)
    assert isinstance(source, ManifestVersionSource)
    assert source.current() == "v0"


def test_static_precedence_env_over_mirror() -> None:
    assert build_version_source(_assets(mirror={"manifest_version": "v3"})).current() == "v3"
    assert build_version_source(_assets(manifest_version="v7", mirror={"manifest_version": "v3"})).current() == "v7"


@pytest.mark.parametrize("bad", ["", "   ", "../x", "a b", "x" * 65])
def test_static_skips_invalid_candidates(bad: str) -> None:
    assert StaticVersion(bad, "v3").current() == "v3"
    assert StaticVersion(bad, bad).current() == "v0"
    assert StaticVersion(None, None).current() == "v0"
    assert StaticVersion().current() == "v0"


def test_static_accepts_explicit_v0_first() -> None:
    assert StaticVersion("v0", "v3").current() == "v0"


def test_file_has_top_precedence(tmp_path: Path) -> None:
    version_file = tmp_path / "manifest_version"
    version_file.write_text("20260913.2\nignored second line\n", encoding="utf-8")
    source = build_version_source(
        _assets(manifest_version="v7", mirror={"manifest_version": "v3", "manifest_version_file": version_file})
    )
    assert isinstance(source, FileVersion)
    assert source.path == version_file
    assert source.current() == "20260913.2"


@pytest.mark.parametrize(
    ("contents", "env", "mirror", "expected"),
    [
        (None, "v7", "v3", "v7"),  # missing file -> HARUKI_ASSETS__MANIFEST_VERSION
        ("", "v7", "v3", "v7"),  # empty file -> env
        ("\n", None, "v3", "v3"),  # blank first line -> mirror.manifest_version
        ("bad version\n", None, "v0", "v0"),  # invalid -> v0
        ("../etc\n", "v7", "v3", "v7"),
    ],
)
def test_file_falls_back_along_the_chain(
    tmp_path: Path, contents: str | None, env: str | None, mirror: str, expected: str
) -> None:
    version_file = tmp_path / "manifest_version"
    if contents is not None:
        version_file.write_text(contents, encoding="utf-8")
    source = build_version_source(
        _assets(manifest_version=env, mirror={"manifest_version": mirror, "manifest_version_file": version_file})
    )
    assert source.current() == expected


def test_file_is_polled_not_read_per_call(tmp_path: Path) -> None:
    version_file = tmp_path / "manifest_version"
    version_file.write_text("v1\n", encoding="utf-8")
    clock = _Clock()
    source = FileVersion(version_file, fallback=StaticVersion("v0"), poll_seconds=30, clock=clock)
    assert source.current() == "v1"

    version_file.write_text("v2\n", encoding="utf-8")
    clock.now += 29.9
    assert source.current() == "v1"  # within the poll window: cached
    clock.now += 0.1
    assert source.current() == "v2"  # window elapsed: re-read

    version_file.unlink()
    clock.now += 30
    assert source.current() == "v0"  # file gone -> fallback
    version_file.write_text("v3", encoding="utf-8")
    clock.now += 30
    assert source.current() == "v3"


def test_poll_zero_reads_every_call(tmp_path: Path) -> None:
    version_file = tmp_path / "v"
    version_file.write_text("v1", encoding="utf-8")
    source = FileVersion(version_file, fallback=StaticVersion(), poll_seconds=-5)
    assert source.current() == "v1"
    version_file.write_text("v2", encoding="utf-8")
    assert source.current() == "v2"


def test_file_problems_logged_once(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    version_file = tmp_path / "v"
    clock = _Clock()
    source = FileVersion(str(version_file), fallback=StaticVersion("v5"), poll_seconds=1, clock=clock)
    with caplog.at_level(logging.INFO, logger="src.assets.version"):
        for _ in range(3):
            assert source.current() == "v5"
            clock.now += 1
        version_file.write_text("no good\n", encoding="utf-8")
        for _ in range(3):
            assert source.current() == "v5"
            clock.now += 1
        version_file.write_text("v9\n", encoding="utf-8")
        assert source.current() == "v9"
        clock.now += 1
        version_file.unlink()
        assert source.current() == "v5"  # problem recurs after recovery -> logged again
    messages = [r.getMessage() for r in caplog.records]
    assert sum("problem=missing" in m for m in messages) == 2
    assert sum("problem=invalid" in m for m in messages) == 1
    assert all(r.levelno >= logging.INFO for r in caplog.records)


def test_unreadable_file_uses_fallback(tmp_path: Path, caplog: pytest.LogCaptureFixture) -> None:
    directory = tmp_path / "is_a_dir"
    directory.mkdir()
    undecodable = tmp_path / "bin"
    undecodable.write_bytes(b"\xff\xfe\xfa\n")
    with caplog.at_level(logging.WARNING, logger="src.assets.version"):
        assert FileVersion(directory, fallback=StaticVersion("v4")).current() == "v4"
        assert FileVersion(undecodable, fallback=StaticVersion("v4")).current() == "v4"
    assert sum("unreadable" in r.getMessage() for r in caplog.records) == 2


def test_fallback_is_consulted_live(tmp_path: Path) -> None:
    class _Mutable:
        value = "v1"

        def current(self) -> str:
            return self.value

    fallback = _Mutable()
    source = FileVersion(tmp_path / "absent", fallback=fallback, poll_seconds=3600)
    assert source.current() == "v1"
    fallback.value = "v2"
    assert source.current() == "v2"
