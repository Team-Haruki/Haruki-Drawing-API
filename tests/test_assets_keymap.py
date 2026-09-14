"""`AssetKeyMap`: the single `asset/`-strip point (C3, addendum A9(7)) and the versioned layout (E5)."""

from __future__ import annotations

import logging
from pathlib import PurePosixPath

import pytest

from src.assets import keymap as keymap_module
from src.assets.keymap import DEFAULT_VERSION, AssetKeyMap, MappedAsset, sanitize_version


def _map(version: str = "v0", mirror_dir: str = "mirror") -> AssetKeyMap:
    return AssetKeyMap(mirror_dir=mirror_dir, version=version)


def test_basic_mapping_table() -> None:
    mapped = _map().map("asset/jp-assets/startapp/x.png")
    assert mapped == MappedAsset(
        region="jp",
        mode="startapp",
        rel="x.png",
        object_key="jp-assets/startapp/x.png",
        mirror_rel=PurePosixPath("mirror/v0/jp-assets/startapp/x.png"),
        legacy_rel=PurePosixPath("asset/jp-assets/startapp/x.png"),
    )
    assert str(mapped.mirror_rel) == "mirror/v0/jp-assets/startapp/x.png"
    assert str(mapped.legacy_rel) == "asset/jp-assets/startapp/x.png"


@pytest.mark.parametrize(
    ("logical", "region", "mode", "rel"),
    [
        ("asset/jp-assets/startapp/x.png", "jp", "startapp", "x.png"),
        (
            "asset/cn-assets/ondemand/music/music_score/0001_01/expert.txt",
            "cn",
            "ondemand",
            "music/music_score/0001_01/expert.txt",
        ),
        (
            "asset/tw-assets/startapp/thumbnail/chara/res001_no001_normal.png",
            "tw",
            "startapp",
            "thumbnail/chara/res001_no001_normal.png",
        ),
        ("asset/en-assets/ondemand/a..b.png", "en", "ondemand", "a..b.png"),
    ],
)
def test_object_key_strips_only_leading_asset_prefix(logical: str, region: str, mode: str, rel: str) -> None:
    mapped = _map("20260913.1", "cache/mirror").map(logical)
    assert mapped is not None
    assert (mapped.region, mapped.mode, mapped.rel) == (region, mode, rel)
    assert mapped.object_key == f"{region}-assets/{mode}/{rel}"
    assert mapped.object_key == logical[len("asset/") :]  # oracle only; tests/ is not a scanned scope
    assert mapped.mirror_rel == PurePosixPath("cache/mirror/20260913.1") / mapped.object_key
    assert mapped.legacy_rel == PurePosixPath(logical)


@pytest.mark.parametrize(
    "logical",
    [
        "static_images/card/frame.png",
        "fonts/SourceHanSansSC-Bold.otf",
        "font/xxx.ttf",
        "custom_profile/shape/1.png",
        "tmp/abc.png",
        "/asset/jp-assets/startapp/x.png",
        "/data/asset/jp-assets/startapp/x.png",
        "C:\\data\\asset\\jp-assets\\startapp\\x.png",
        "jp-assets/startapp/x.png",
        "asset/jpn-assets/startapp/x.png",
        "asset/JP-assets/startapp/x.png",
        "asset/jp-assets/other/x.png",
        "",
    ],
)
def test_non_bucket_keys_return_none_silently(logical: str, caplog: pytest.LogCaptureFixture) -> None:
    with caplog.at_level(logging.WARNING, logger="src.assets.keymap"):
        assert _map().map(logical) is None
    assert not caplog.records


def test_non_string_returns_none() -> None:
    assert _map().map(None) is None  # type: ignore[arg-type]
    assert _map().map(PurePosixPath("asset/jp-assets/startapp/x.png")) is None  # type: ignore[arg-type]


@pytest.mark.parametrize(
    "logical",
    [
        "asset/jp-assets/startapp/../../../etc/passwd",
        "asset/jp-assets/startapp/a/../b.png",
        "asset/jp-assets/startapp/..",
        "asset/jp-assets/startapp/a\\b.png",
        "asset/jp-assets/startapp//abs.png",
        "asset/jp-assets/startapp/a//b.png",
        "asset/jp-assets/startapp/a/b/",
        "asset/jp-assets/startapp/",
        "asset/jp-assets/startapp/./b.png",
        "asset/jp-assets/startapp/a\0b.png",
    ],
)
def test_malformed_keys_rejected_and_warned_once(logical: str, caplog: pytest.LogCaptureFixture) -> None:
    km = _map()
    with caplog.at_level(logging.WARNING, logger="src.assets.keymap"):
        assert km.map(logical) is None
        assert km.map(logical) is None
    warnings = [r for r in caplog.records if "malformed_key" in r.getMessage()]
    assert len(warnings) == 1


def test_warned_set_is_bounded(monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture) -> None:
    monkeypatch.setattr(keymap_module, "_WARNED_MAX", 2)
    km = _map()
    with caplog.at_level(logging.WARNING, logger="src.assets.keymap"):
        for i in range(3):
            assert km.map(f"asset/jp-assets/startapp/{i}/../x.png") is None
        # the set was cleared when full, so the first key warns again
        assert km.map("asset/jp-assets/startapp/0/../x.png") is None
    assert len([r for r in caplog.records if "malformed_key" in r.getMessage()]) == 4


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("v0", "v0"),
        ("20260913.1", "20260913.1"),
        ("  v7\n", "v7"),
        ("A-b_c.9", "A-b_c.9"),
        ("x" * 64, "x" * 64),
        ("x" * 65, DEFAULT_VERSION),
        ("", DEFAULT_VERSION),
        ("   ", DEFAULT_VERSION),
        ("v1/../../etc", DEFAULT_VERSION),
        ("..", DEFAULT_VERSION),
        (".", DEFAULT_VERSION),
        ("v 1", DEFAULT_VERSION),
        ("版本", DEFAULT_VERSION),
        (None, DEFAULT_VERSION),
        (7, DEFAULT_VERSION),
    ],
)
def test_version_sanitising(raw: object, expected: str) -> None:
    assert sanitize_version(raw) == expected
    if isinstance(raw, str):
        km = AssetKeyMap(mirror_dir="mirror", version=raw)
        assert km.version == expected
        mapped = km.map("asset/kr-assets/ondemand/y.png")
        assert mapped is not None
        assert mapped.mirror_rel == PurePosixPath(f"mirror/{expected}/kr-assets/ondemand/y.png")


@pytest.mark.parametrize("mirror_dir", ["", "  ", "/abs/mirror", "../mirror", "a/../../b", "\\abs"])
def test_mirror_dir_validated(mirror_dir: str) -> None:
    with pytest.raises(ValueError, match="mirror_dir"):
        AssetKeyMap(mirror_dir=mirror_dir, version="v0")


def test_mirror_dir_backslashes_normalised() -> None:
    km = AssetKeyMap(mirror_dir="cache\\mirror", version="v1")
    assert km.mirror_dir == "cache/mirror"
    mapped = km.map("asset/jp-assets/startapp/x.png")
    assert mapped is not None
    assert str(mapped.mirror_rel) == "cache/mirror/v1/jp-assets/startapp/x.png"


def test_mapped_asset_is_frozen() -> None:
    mapped = _map().map("asset/jp-assets/startapp/x.png")
    assert mapped is not None
    with pytest.raises(AttributeError):
        mapped.object_key = "other"  # type: ignore[misc]


def test_package_reexports_and_no_opendal_import() -> None:
    import subprocess
    import sys

    code = (
        "import sys; sys.modules['opendal'] = None\n"
        "import src.assets as a\n"
        "assert a.AssetKeyMap and a.build_version_source and a.StaticVersion and a.FileVersion\n"
        "assert a.MappedAsset and a.ManifestVersionSource and a.sanitize_version('v1') == 'v1'\n"
    )
    result = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, check=False)
    assert result.returncode == 0, result.stderr
