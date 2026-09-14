"""`opendal_kwargs` parity with Asset-Updater's `resolve_storage_provider` (storage.rs:560-650)."""

from __future__ import annotations

import pytest

from src.settings import ArtifactProviderSettings, AssetMirrorProviderSettings, StorageProviderSettings
from src.storage.provider import construct_endpoint, opendal_kwargs, resolve_template


def _p(**kwargs) -> StorageProviderSettings:
    return StorageProviderSettings.model_validate(kwargs)


@pytest.mark.parametrize(
    ("provider", "region", "expected"),
    [
        # 1. bare host + tls=True -> https, default region, bucket templated with {server}
        (
            {"scheme": "s3", "endpoint": "assets.example.com", "bucket": "sekai-{server}-assets"},
            "jp",
            {"bucket": "sekai-jp-assets", "endpoint": "https://assets.example.com", "region": "garage"},
        ),
        # 2. bare host:port + tls=False -> http
        (
            {"scheme": "s3", "endpoint": "10.0.0.1:3900/", "tls": False, "bucket": "b"},
            None,
            {"bucket": "b", "endpoint": "http://10.0.0.1:3900", "region": "garage"},
        ),
        # 3. full URL endpoint kept (trailing slash trimmed), tls ignored
        (
            {"scheme": "s3", "endpoint": "http://garage:3900/", "tls": True, "bucket": "b", "region": "cn"},
            None,
            {"bucket": "b", "endpoint": "http://garage:3900", "region": "cn"},
        ),
        # 4. {region} root and bucket, root stripped of slashes
        (
            {"scheme": "s3", "bucket": "{region}-bucket", "root": "/assets/{region}/"},
            "tw",
            {"root": "assets/tw", "bucket": "tw-bucket", "region": "garage"},
        ),
        # 5. legacy prefix alias of root (root wins when both are set)
        (
            {"scheme": "s3", "bucket": "b", "prefix": "/upload/smoke/"},
            None,
            {"root": "upload/smoke", "bucket": "b", "region": "garage"},
        ),
        # 6. options win over every derived key, and are templated
        (
            {
                "scheme": "s3",
                "bucket": "derived",
                "endpoint": "derived.example.com",
                "root": "derived",
                "options": {"bucket": "sekai-{region}-assets", "endpoint": "https://s3.example.com", "root": "x"},
            },
            "jp",
            {"bucket": "sekai-jp-assets", "endpoint": "https://s3.example.com", "root": "x", "region": "garage"},
        ),
        # 7. public_read -> default_acl, credentials passed through
        (
            {
                "scheme": "s3",
                "bucket": "b",
                "public_read": True,
                "access_key_id": "AK",
                "secret_access_key": "SK",
            },
            None,
            {
                "bucket": "b",
                "region": "garage",
                "access_key_id": "AK",
                "secret_access_key": "SK",
                "default_acl": "public-read",
            },
        ),
        # 8. path_style=False -> enable_virtual_host_style
        (
            {"scheme": "s3", "bucket": "b", "path_style": False},
            None,
            {"bucket": "b", "region": "garage", "enable_virtual_host_style": "true"},
        ),
    ],
)
def test_opendal_kwargs_vectors(provider, region, expected):
    assert opendal_kwargs(_p(**provider), region) == expected


def test_root_wins_over_prefix():
    assert opendal_kwargs(_p(scheme="s3", bucket="b", root="r", prefix="p"), None)["root"] == "r"


def test_blank_root_and_prefix_contribute_nothing():
    kwargs = opendal_kwargs(_p(scheme="s3", bucket="b", root="  ", prefix="   "), None)
    assert "root" not in kwargs


def test_missing_bucket_raises_for_s3():
    with pytest.raises(ValueError, match="missing bucket"):
        opendal_kwargs(_p(scheme="s3", endpoint="garage:3900"), None)


def test_bucket_from_options_satisfies_s3():
    assert opendal_kwargs(_p(scheme="s3", options={"bucket": "o"}), None)["bucket"] == "o"


def test_empty_credentials_and_region_are_omitted():
    kwargs = opendal_kwargs(_p(scheme="s3", bucket="b", region="", access_key_id="", secret_access_key=""), None)
    assert kwargs == {"bucket": "b"}


def test_public_read_is_s3_only():
    assert "default_acl" not in opendal_kwargs(_p(scheme="fs", root="/data", public_read=True), None)


def test_fs_maps_root_to_directory_and_drops_s3_keys():
    provider = _p(
        scheme="local",
        root="/srv/{region}/cache/",
        bucket="ignored",
        endpoint="garage:3900",
        access_key_id="AK",
        public_read=True,
        path_style=False,
    )
    assert opendal_kwargs(provider, "jp") == {"root": "/srv/jp/cache"}
    assert opendal_kwargs(_p(scheme="fs", root="/"), None) == {"root": "/"}
    assert opendal_kwargs(_p(scheme="fs", root="relative\\dir"), None) == {"root": "relative/dir"}


def test_fs_keeps_explicit_options():
    assert opendal_kwargs(_p(scheme="fs", root="/a", options={"atomic_write_dir": "/tmp"}), None) == {
        "atomic_write_dir": "/tmp",
        "root": "/a",
    }


def test_memory_returns_no_options():
    assert opendal_kwargs(_p(scheme="memory", bucket="b", options={"x": "y"}), None) == {}


def test_slot_defaults():
    assert opendal_kwargs(AssetMirrorProviderSettings(), "jp") == {"bucket": "pjsk-assets", "region": "garage"}
    assert opendal_kwargs(ArtifactProviderSettings(), None) == {"bucket": "image-cache", "region": "garage"}


def test_resolve_template():
    assert resolve_template("{server}-{region}", "kr") == "kr-kr"
    assert resolve_template("{region}-bucket", None) == "{region}-bucket"


def test_construct_endpoint():
    assert construct_endpoint(" host/ ", True) == "https://host"
    assert construct_endpoint("host", False) == "http://host"
    assert construct_endpoint("https://h:1/", False) == "https://h:1"


def test_describe_contains_no_secret():
    provider = _p(scheme="s3", bucket="b", endpoint="e", access_key_id="AKSECRET", secret_access_key="SKSECRET")
    described = provider.describe()
    assert set(described) == {"provider", "scheme", "endpoint", "bucket", "root"}
    assert "AKSECRET" not in repr(described)
    assert "SKSECRET" not in repr(described)
    assert "SKSECRET" not in repr(provider)
