"""User-upload store: `img_path` -> object key mapping, bucket reads, fallbacks, and the profile drawer wiring."""

from __future__ import annotations

import asyncio
from io import BytesIO
import logging
import os
import sys

from PIL import Image
import pytest

from src.assets import user_upload as mod
from src.assets.user_upload import UserUploadStore, build_user_upload_store, profile_bg_object_key
from src.sekai.base.image_source import EncodedImageRef
from src.sekai.base.plot import ImageBg
from src.sekai.profile import drawer
from src.sekai.profile.model import BasicProfile, CharacterRank, MusicClearCount, ProfileBgSettings, ProfileRequest
from src.settings import Settings, UserUploadSettings
from src.storage.protocols import StorageUnavailable
from tests.storage_fakes import FakeObjectStore

KEY = "user_upload/profile_bg/jp/uid_1234_0badf00d.jpg"


@pytest.fixture(autouse=True)
def _isolate():
    mod.set_user_upload_store(None)
    yield
    mod.set_user_upload_store(None)


def _png_bytes(size=(40, 30), color=(255, 0, 0, 255)) -> bytes:
    buf = BytesIO()
    Image.new("RGBA", size, color).save(buf, "PNG")
    return buf.getvalue()


def _enabled_store(store: FakeObjectStore | None = None, **overrides) -> UserUploadStore:
    settings = UserUploadSettings(enabled=True, **overrides)
    return UserUploadStore(
        settings=settings, store=store if store is not None else FakeObjectStore(bucket="user-upload")
    )


# ---------------------------------------------------------------------------------------------- key mapping


@pytest.mark.parametrize(
    "img_path",
    [
        KEY,
        f"/{KEY}",
        f"./{KEY}",
        f"  {KEY}\n",
        KEY.replace("/", "\\"),
        f"/pjskdata/Data/{KEY}",
        f"/asset/{KEY}",
        f"//{KEY}",
    ],
)
def test_profile_bg_object_key_accepts_every_form_cloud_has_sent(img_path: str) -> None:
    assert profile_bg_object_key(img_path) == KEY


def test_profile_bg_object_key_keeps_legacy_binding_filenames() -> None:
    assert profile_bg_object_key("user_upload/profile_bg/tw/binding_3.jpg") == "user_upload/profile_bg/tw/binding_3.jpg"


@pytest.mark.parametrize(
    "img_path",
    [None, "", "   ", "asset/jp-assets/startapp/bg.png", "user_upload/preview3d/x.png", "profile_bg/jp/uid_1.jpg"],
)
def test_profile_bg_object_key_returns_none_outside_the_namespace(img_path: str | None) -> None:
    assert profile_bg_object_key(img_path) is None


@pytest.mark.parametrize(
    "img_path",
    [
        "user_upload/profile_bg/../../etc/passwd",
        "../user_upload/profile_bg/jp/uid_1.jpg",
        "user_upload/profile_bg/jp/../cn/uid_1.jpg",
        "user_upload/profile_bg/jp/uid_1.jpg\x00.png",
        "user_upload/profile_bg/jp",
        "user_upload/profile_bg/",
    ],
)
def test_profile_bg_object_key_rejects_traversal_and_malformed_paths(img_path: str) -> None:
    with pytest.raises(ValueError, match="profile background path"):
        profile_bg_object_key(img_path)


# ---------------------------------------------------------------------------------------------- store reads


def test_fetch_reads_by_key_and_caches_the_bytes() -> None:
    fake = FakeObjectStore({KEY: b"jpeg-bytes"}, bucket="user-upload")
    store = _enabled_store(fake)

    async def run():
        first = await store.fetch(KEY)
        second = await store.fetch(KEY)
        return first, second

    first, second = asyncio.run(run())
    assert first == b"jpeg-bytes"
    assert second == b"jpeg-bytes"
    assert fake.reads == [KEY]  # the second read is a cache hit
    snapshot = store.stats_snapshot()
    assert snapshot["fetches"] == 1
    assert snapshot["cache"] == {"entries": 1, "bytes": len(b"jpeg-bytes"), "hits": 1, "misses": 1}


def test_fetch_missing_object_returns_none_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    store = _enabled_store(FakeObjectStore(bucket="user-upload"))
    with caplog.at_level(logging.WARNING, logger="src.assets.user_upload"):
        assert asyncio.run(store.fetch(KEY)) is None
    assert any("user_upload.not_found" in record.message and KEY in record.message for record in caplog.records)
    assert store.stats_snapshot()["not_found"] == 1


def test_fetch_store_error_returns_none_with_a_warning(caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeObjectStore({KEY: b"x"}, fail=StorageUnavailable("garage down"), bucket="user-upload")
    store = _enabled_store(fake)
    with caplog.at_level(logging.WARNING, logger="src.assets.user_upload"):
        assert asyncio.run(store.fetch(KEY)) is None
    assert any(
        "user_upload.read_failed" in record.message and "garage down" in record.message for record in caplog.records
    )
    assert store.stats_snapshot()["errors"] == 1


def test_fetch_is_bounded_by_the_timeout(caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeObjectStore({KEY: b"x"}, delay=5.0, bucket="user-upload")
    store = _enabled_store(fake, fetch_timeout_seconds=0.0)  # hard cap: max(0.1, 0.0) + 1.0 s
    with caplog.at_level(logging.WARNING, logger="src.assets.user_upload"):
        assert asyncio.run(store.fetch(KEY)) is None
    assert any(
        "user_upload.read_failed" in record.message and "TimeoutError" in record.message for record in caplog.records
    )


def test_fetch_refuses_oversized_objects() -> None:
    fake = FakeObjectStore({KEY: b"0" * 64}, bucket="user-upload")
    store = _enabled_store(fake, fetch_max_bytes=16)
    assert asyncio.run(store.fetch(KEY)) is None
    assert store.stats_snapshot()["errors"] == 1


def test_cache_is_bounded_and_can_be_disabled() -> None:
    fake = FakeObjectStore({KEY: b"a" * 10, "user_upload/profile_bg/jp/b.jpg": b"b" * 10}, bucket="user-upload")
    store = _enabled_store(fake, cache_size=1)
    asyncio.run(store.fetch(KEY))
    asyncio.run(store.fetch("user_upload/profile_bg/jp/b.jpg"))
    assert store.stats_snapshot()["cache"]["entries"] == 1

    disabled = _enabled_store(FakeObjectStore({KEY: b"a"}, bucket="user-upload"), cache_ttl_seconds=0)
    asyncio.run(disabled.fetch(KEY))
    asyncio.run(disabled.fetch(KEY))
    assert disabled.stats_snapshot()["fetches"] == 2


def test_close_disables_the_store_and_is_idempotent() -> None:
    fake = FakeObjectStore({KEY: b"x"}, bucket="user-upload")
    store = _enabled_store(fake)
    asyncio.run(store.close())
    asyncio.run(store.close())
    assert fake.closed
    assert not store.enabled
    assert asyncio.run(store.fetch(KEY)) is None


# ---------------------------------------------------------------------------------------------- build / settings


def test_disabled_by_default_never_imports_opendal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "opendal", None)
    store = build_user_upload_store(Settings(), store_factory=lambda _s: pytest.fail("built store"))
    assert not store.enabled
    assert store.disabled_reason == "disabled"
    assert asyncio.run(store.fetch(KEY)) is None


def test_settings_defaults_and_partial_provider_block_keep_slot_defaults(monkeypatch: pytest.MonkeyPatch) -> None:
    for key in [k for k in os.environ if k.startswith("HARUKI_")]:
        monkeypatch.delenv(key, raising=False)
    assert Settings().assets.user_upload.enabled is False
    monkeypatch.setenv("HARUKI_ASSETS__USER_UPLOAD__ENABLED", "true")
    monkeypatch.setenv("HARUKI_ASSETS__USER_UPLOAD__PROVIDER__ENDPOINT", "100.64.0.9:3900")
    config = Settings().assets.user_upload
    assert config.enabled is True
    assert config.provider.scheme == "s3"
    assert config.provider.bucket == "user-upload"
    assert config.provider.endpoint == "100.64.0.9:3900"
    assert config.provider.region == "garage"
    assert config.provider.path_style is True


def test_build_failure_yields_a_disabled_store_with_reason(caplog: pytest.LogCaptureFixture) -> None:
    settings = Settings()
    settings.assets.user_upload = UserUploadSettings(enabled=True)

    def boom(_config):
        raise RuntimeError("no operator")

    with caplog.at_level(logging.ERROR, logger="src.assets.user_upload"):
        store = build_user_upload_store(settings, store_factory=boom)
    assert not store.enabled
    assert store.disabled_reason == "RuntimeError: no operator"
    assert any("user_upload.disabled" in record.message for record in caplog.records)


def test_build_over_the_opendal_memory_provider(caplog: pytest.LogCaptureFixture) -> None:
    pytest.importorskip("opendal")
    settings = Settings()
    settings.assets.user_upload = UserUploadSettings(enabled=True, provider={"scheme": "memory", "root": "ignored"})
    with caplog.at_level(logging.WARNING, logger="src.assets.user_upload"):
        store = build_user_upload_store(settings)
    assert store.enabled
    assert any("root=" in record.message and "ignored" in record.message for record in caplog.records)

    async def run():
        assert await store.fetch(KEY) is None  # empty memory backend: NotFound, not an exception
        await store.close()

    asyncio.run(run())


def test_lifecycle_accessor_builds_lazily_and_shutdown_closes(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.settings import settings

    monkeypatch.setattr(settings.assets, "user_upload", UserUploadSettings(enabled=False))
    first = mod.get_user_upload_store()
    assert first is mod.get_user_upload_store()
    assert not first.enabled

    fake = FakeObjectStore(bucket="user-upload")
    installed = _enabled_store(fake)
    mod.set_user_upload_store(installed)
    assert mod.start_user_upload_store() is installed
    asyncio.run(mod.shutdown_user_upload_store())
    assert fake.closed
    assert mod.get_user_upload_store() is installed  # stays installed, now disabled
    assert not installed.enabled


# ---------------------------------------------------------------------------------------------- profile drawer


def _request(background: str | None) -> ProfileRequest:
    return ProfileRequest(
        profile=BasicProfile(
            id="1234567890123456",
            region="jp",
            nickname="Player",
            is_hide_uid=True,
            leader_image_path="avatar.png",
        ),
        rank=321,
        twitter_id="haruki",
        word="hello",
        pcards=[],
        bg_settings=ProfileBgSettings(img_path=background, alpha=120, blur=2),
        music_difficulty_count=[MusicClearCount(difficulty="expert", clear=3, fc=2, ap=1)],
        character_rank=[CharacterRank(character_id=1, rank=50)],
        lv_rank_bg_path="rank.png",
        x_icon_path="x.png",
        icon_clear_path="clear.png",
        icon_fc_path="fc.png",
        icon_ap_path="ap.png",
        chara_rank_icon_path_map={1: "chara.png"},
    )


@pytest.fixture
def local_assets(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Fake the local asset reads; returns the list of `on_missing="raise"` paths the drawer asked for."""
    raised_reads: list[str] = []

    async def fake_asset(_root, path, **kwargs):
        if kwargs.get("on_missing") == "raise":
            raised_reads.append(path)
            if path.startswith("missing"):
                raise FileNotFoundError(path)
        size = (180, 60) if path == "rank.png" else (32, 32)
        return Image.new("RGBA", size, (20, 40, 60, 255))

    async def fake_assets(root, paths, **kwargs):
        return [await fake_asset(root, path, **kwargs) for path in paths]

    monkeypatch.setattr(drawer, "get_asset_image_ref", fake_asset)
    monkeypatch.setattr(drawer, "get_asset_image_refs", fake_assets)
    monkeypatch.setattr(drawer, "_profile_stats_badge_width", lambda text, font_size=18: len(text) * 5 + font_size)
    return raised_reads


@pytest.mark.anyio
async def test_store_disabled_keeps_the_local_read_for_every_path(local_assets: list[str]) -> None:
    mod.set_user_upload_store(UserUploadStore(settings=UserUploadSettings(), store=None, disabled_reason="disabled"))
    canvas = await drawer._build_profile_canvas(_request(KEY))
    assert isinstance(canvas.bg, ImageBg)
    assert isinstance(canvas.bg.img, Image.Image)  # the faked local ref, not encoded bucket bytes
    assert local_assets == [KEY]

    missing = await drawer._build_profile_canvas(_request("missing-bg.png"))
    assert missing.bg is drawer.SEKAI_BLUE_BG


@pytest.mark.anyio
async def test_store_enabled_reads_the_bucket_object_as_encoded_bytes(local_assets: list[str]) -> None:
    fake = FakeObjectStore({KEY: _png_bytes((64, 48))}, bucket="user-upload")
    mod.set_user_upload_store(_enabled_store(fake))

    canvas = await drawer._build_profile_canvas(_request(f"/pjskdata/Data/{KEY}"))
    assert isinstance(canvas.bg, ImageBg)
    assert isinstance(canvas.bg.img, EncodedImageRef)
    assert canvas.bg.img.size == (64, 48)
    assert fake.reads == [KEY]
    assert local_assets == []  # the bucket answered; local disk was never touched


@pytest.mark.anyio
async def test_store_enabled_leaves_non_user_upload_paths_on_local_disk(local_assets: list[str]) -> None:
    fake = FakeObjectStore(bucket="user-upload")
    mod.set_user_upload_store(_enabled_store(fake))
    canvas = await drawer._build_profile_canvas(_request("asset/jp-assets/bg.png"))
    assert isinstance(canvas.bg.img, Image.Image)
    assert fake.reads == []
    assert local_assets == ["asset/jp-assets/bg.png"]


@pytest.mark.anyio
async def test_missing_object_falls_back_to_local_then_default(local_assets: list[str], caplog) -> None:
    fake = FakeObjectStore(bucket="user-upload")
    mod.set_user_upload_store(_enabled_store(fake))
    missing_key = "user_upload/profile_bg/jp/uid_9_dead.jpg"

    with caplog.at_level(logging.WARNING):
        canvas = await drawer._build_profile_canvas(_request(missing_key))
    assert isinstance(canvas.bg, ImageBg)  # the local copy still exists during the dual-write window
    assert local_assets == [missing_key]
    assert any("user_upload.not_found" in record.message for record in caplog.records)

    local_assets.clear()
    gone = await drawer._build_profile_canvas(_request("missing/" + missing_key))
    assert gone.bg is drawer.SEKAI_BLUE_BG
    assert local_assets == ["missing/" + missing_key]


@pytest.mark.anyio
async def test_store_error_falls_back_and_never_raises(local_assets: list[str], caplog) -> None:
    fake = FakeObjectStore({KEY: b"x"}, fail=StorageUnavailable("garage down"), bucket="user-upload")
    mod.set_user_upload_store(_enabled_store(fake, local_fallback=False))
    with caplog.at_level(logging.WARNING):
        canvas = await drawer._build_profile_canvas(_request(KEY))
    assert canvas.bg is drawer.SEKAI_BLUE_BG
    assert local_assets == []  # local_fallback=false: straight to the default background
    assert any("user_upload.read_failed" in record.message for record in caplog.records)


@pytest.mark.anyio
async def test_traversal_is_rejected_with_a_warning(local_assets: list[str], caplog) -> None:
    fake = FakeObjectStore(bucket="user-upload")
    mod.set_user_upload_store(_enabled_store(fake))
    with caplog.at_level(logging.WARNING, logger=drawer.logger.name):
        canvas = await drawer._build_profile_canvas(_request("user_upload/profile_bg/../../etc/passwd"))
    assert canvas.bg is drawer.SEKAI_BLUE_BG
    assert fake.reads == []
    assert fake.stats == []
    assert local_assets == []
    assert any("profile.bg_rejected" in record.message for record in caplog.records)


@pytest.mark.anyio
async def test_undecodable_object_falls_back_to_default(local_assets: list[str], caplog) -> None:
    fake = FakeObjectStore({KEY: b"not an image"}, bucket="user-upload")
    mod.set_user_upload_store(_enabled_store(fake, local_fallback=False))
    with caplog.at_level(logging.WARNING, logger=drawer.logger.name):
        canvas = await drawer._build_profile_canvas(_request(KEY))
    assert canvas.bg is drawer.SEKAI_BLUE_BG
    assert any("profile.bg_undecodable" in record.message for record in caplog.records)
