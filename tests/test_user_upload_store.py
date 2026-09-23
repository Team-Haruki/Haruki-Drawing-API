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
from src.settings import Settings, UserUploadProviderSettings, UserUploadSettings
from src.storage.protocols import StorageNotFound, StorageUnavailable
from src.storage.provider import opendal_kwargs
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


class _Clock:
    def __init__(self, now: float = 1000.0) -> None:
        self.now = now

    def __call__(self) -> float:
        return self.now


def _enabled_store(store: FakeObjectStore | None = None, clock: _Clock | None = None, **overrides) -> UserUploadStore:
    settings = UserUploadSettings(enabled=True, **overrides)
    return UserUploadStore(
        settings=settings,
        store=store if store is not None else FakeObjectStore(bucket="user-upload"),
        clock=clock if clock is not None else _Clock(),
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


@pytest.mark.parametrize(
    "key",
    [
        "user_upload/profile_bg/tw/binding_3.jpg",
        "user_upload/profile_bg/tw/binding_3_0badf00d.jpg",
        "user_upload/profile_bg/en/uid_42.jpg",
        "user_upload/profile_bg/cn/uid_1234567890123456_deadbeef.jpg",
    ],
)
def test_profile_bg_object_key_keeps_every_filename_cloud_has_written(key: str) -> None:
    assert profile_bg_object_key(key) == key


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
        "user_upload/profile_bg/jp/extra/uid_1_0badf00d.jpg",
        "user_upload/profile_bg/JP/uid_1_0badf00d.jpg",
        "user_upload/profile_bg/jp/uid_1_0badf00d.png",
        "user_upload/profile_bg/jp/avatar.jpg",
        "user_upload/profile_bg/jp/uid_1_0badf00d.jpg.exe",
        "user_upload/profile_bg/jp/uid_1_XYZ.jpg",
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


def test_fetch_is_bounded_by_the_total_budget_and_counts_as_an_error(caplog: pytest.LogCaptureFixture) -> None:
    fake = FakeObjectStore({KEY: b"x"}, delay=5.0, bucket="user-upload")
    store = _enabled_store(fake, fetch_timeout_seconds=0.2)

    async def run():
        started = asyncio.get_running_loop().time()
        result = await store.fetch(KEY)
        return result, asyncio.get_running_loop().time() - started

    with caplog.at_level(logging.WARNING, logger="src.assets.user_upload"):
        result, elapsed = asyncio.run(run())
    assert result is None
    assert elapsed < 2.0  # the fake would have taken 5 s: the wait_for is the real budget
    assert any(
        "user_upload.read_failed" in record.message and "TimeoutError" in record.message for record in caplog.records
    )
    snapshot = store.stats_snapshot()
    assert snapshot["errors"] == 1
    assert snapshot["not_found"] == 0


def test_default_store_factory_clamps_the_per_operation_timeout_under_the_budget(monkeypatch) -> None:
    pytest.importorskip("opendal")
    from src.storage import opendal_store

    seen: dict[str, float] = {}
    real = opendal_store.OpendalObjectStore.from_provider

    def spy(provider, **kwargs):
        seen.update(timeout=kwargs["timeout"], io_timeout=kwargs["io_timeout"])
        return real(provider, **kwargs)

    monkeypatch.setattr(opendal_store.OpendalObjectStore, "from_provider", spy)
    config = UserUploadSettings(
        enabled=True,
        fetch_timeout_seconds=1.5,
        fetch_io_timeout_seconds=9.0,
        provider={"scheme": "s3", "endpoint": "127.0.0.1:1", "access_key_id": "k", "secret_access_key": "s"},
    )
    store = mod._default_store_factory(config)
    assert store.bucket == "user-upload"
    assert seen == {"timeout": 1.5, "io_timeout": 1.5}


def test_concurrent_first_fetches_of_one_key_share_one_read() -> None:
    fake = FakeObjectStore({KEY: b"jpeg-bytes"}, delay=0.05, bucket="user-upload")
    store = _enabled_store(fake)

    async def run():
        return await asyncio.gather(*(store.fetch(KEY) for _ in range(5)))

    results = asyncio.run(run())
    assert results == [b"jpeg-bytes"] * 5
    assert fake.reads == [KEY]
    snapshot = store.stats_snapshot()
    assert snapshot["fetches"] == 1
    assert snapshot["single_flight_waits"] == 4


def test_single_flight_waiters_see_the_leaders_failure_and_the_slot_is_released() -> None:
    fake = FakeObjectStore({KEY: b"x"}, delay=0.05, fail=StorageUnavailable("down"), bucket="user-upload")
    store = _enabled_store(fake)

    async def run():
        return await asyncio.gather(*(store.fetch(KEY) for _ in range(3)))

    assert asyncio.run(run()) == [None, None, None]
    assert store._inflight == {}
    assert store.stats_snapshot()["errors"] == 1


# ---------------------------------------------------------------------------------------------- breaker


def test_breaker_opens_after_consecutive_failures_and_skips_the_bucket(caplog: pytest.LogCaptureFixture) -> None:
    clock = _Clock()
    fake = FakeObjectStore({KEY: b"x"}, fail=StorageUnavailable("down"), bucket="user-upload")
    store = _enabled_store(fake, clock, breaker_failures=3, breaker_open_seconds=30.0)

    with caplog.at_level(logging.WARNING, logger="src.assets.user_upload"):
        for _ in range(3):
            assert asyncio.run(store.fetch(KEY)) is None
    assert store.breaker_open
    assert any(
        "user_upload.breaker_open" in record.message and "failures=3" in record.message for record in caplog.records
    )
    ops_before = fake.ops
    for _ in range(4):
        assert asyncio.run(store.fetch(KEY)) is None
    assert fake.ops == ops_before  # open: the bucket is never touched
    snapshot = store.stats_snapshot()
    assert snapshot["breaker_trips"] == 1
    assert snapshot["breaker_skips"] == 4
    assert snapshot["errors"] == 3


def test_breaker_half_open_probe_failure_reopens_and_success_closes(caplog: pytest.LogCaptureFixture) -> None:
    clock = _Clock()
    fake = FakeObjectStore({KEY: b"jpeg-bytes"}, fail=StorageUnavailable("down"), bucket="user-upload")
    store = _enabled_store(fake, clock, breaker_failures=2, breaker_open_seconds=30.0, cache_ttl_seconds=0)
    for _ in range(2):
        asyncio.run(store.fetch(KEY))
    assert store.breaker_open

    clock.now += 31.0  # window elapsed: the next fetch is the single probe, and it fails
    ops_before = fake.ops
    assert asyncio.run(store.fetch(KEY)) is None
    assert fake.ops == ops_before + 1
    assert store.breaker_open
    assert store.stats_snapshot()["breaker_trips"] == 2
    assert asyncio.run(store.fetch(KEY)) is None  # re-opened: skipped again
    assert fake.ops == ops_before + 1

    clock.now += 31.0
    fake.fail = None  # Garage is back
    with caplog.at_level(logging.INFO, logger="src.assets.user_upload"):
        assert asyncio.run(store.fetch(KEY)) == b"jpeg-bytes"
    assert not store.breaker_open
    assert any("user_upload.breaker_closed" in record.message for record in caplog.records)
    assert asyncio.run(store.fetch(KEY)) == b"jpeg-bytes"  # closed: normal reads again
    assert fake.ops == ops_before + 3


def test_breaker_treats_not_found_as_an_answer_and_timeouts_as_failures() -> None:
    clock = _Clock()
    fake = FakeObjectStore({KEY: b"x"}, fail=StorageUnavailable("down"), bucket="user-upload")
    store = _enabled_store(fake, clock, breaker_failures=2)
    asyncio.run(store.fetch(KEY))  # 1 failure
    fake.fail = StorageNotFound("gone")
    asyncio.run(store.fetch(KEY))  # NotFound resets the count
    fake.fail = StorageUnavailable("down")
    asyncio.run(store.fetch(KEY))  # 1 failure again
    assert not store.breaker_open

    slow = FakeObjectStore({KEY: b"x"}, delay=5.0, bucket="user-upload")
    timing_out = _enabled_store(slow, clock, breaker_failures=2, fetch_timeout_seconds=0.1)
    asyncio.run(timing_out.fetch(KEY))
    asyncio.run(timing_out.fetch(KEY))
    assert timing_out.breaker_open
    assert timing_out.stats_snapshot()["errors"] == 2


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


def test_build_over_the_opendal_memory_provider() -> None:
    pytest.importorskip("opendal")
    settings = Settings()
    settings.assets.user_upload = UserUploadSettings(enabled=True, provider={"scheme": "memory"})
    store = build_user_upload_store(settings)
    assert store.enabled

    async def run():
        assert await store.fetch(KEY) is None  # empty memory backend: NotFound, not an exception
        await store.close()

    asyncio.run(run())


@pytest.mark.parametrize("field", ["root", "prefix"])
def test_s3_root_is_dropped_before_it_reaches_the_opendal_kwargs(field: str, caplog) -> None:
    with caplog.at_level(logging.WARNING, logger="src.settings"):
        provider = UserUploadProviderSettings(
            scheme="s3", endpoint="100.64.0.9:3900", access_key_id="k", secret_access_key="s", **{field: "user_upload"}
        )
    assert provider.root == ""
    assert provider.prefix is None
    assert any("settings.user_upload_root_dropped" in r.message and "user_upload" in r.message for r in caplog.records)
    kwargs = opendal_kwargs(provider, None)
    assert "root" not in kwargs  # this is the dict opendal receives: no key prefix
    assert kwargs["bucket"] == "user-upload"
    assert kwargs["endpoint"] == "https://100.64.0.9:3900"


def test_s3_root_is_dropped_from_env_and_the_real_operator_gets_no_prefix(monkeypatch: pytest.MonkeyPatch) -> None:
    pytest.importorskip("opendal")
    for key in [k for k in os.environ if k.startswith("HARUKI_")]:
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setenv("HARUKI_ASSETS__USER_UPLOAD__ENABLED", "true")
    monkeypatch.setenv("HARUKI_ASSETS__USER_UPLOAD__PROVIDER__ENDPOINT", "http://127.0.0.1:1")
    monkeypatch.setenv("HARUKI_ASSETS__USER_UPLOAD__PROVIDER__ROOT", "user_upload")
    monkeypatch.setenv("HARUKI_ASSETS__USER_UPLOAD__PROVIDER__ACCESS_KEY_ID", "unit-test-key-id")
    monkeypatch.setenv("HARUKI_ASSETS__USER_UPLOAD__PROVIDER__SECRET_ACCESS_KEY", "unit-test-secret")
    settings = Settings()
    assert settings.assets.user_upload.provider.root == ""
    store = build_user_upload_store(settings)  # the real opendal s3 operator, built offline
    assert store.enabled
    assert 'root="/"' in repr(store._store._op)  # opendal's own view: bucket root, no user_upload/ prefix
    asyncio.run(store.close())


def test_build_refuses_a_root_assigned_after_validation() -> None:
    settings = Settings()
    settings.assets.user_upload = UserUploadSettings(enabled=True)
    settings.assets.user_upload.provider.root = "user_upload"  # bypasses the validator
    store = build_user_upload_store(settings, store_factory=lambda _s: pytest.fail("built store"))
    assert not store.enabled
    assert "root must stay empty" in store.disabled_reason


def test_fs_provider_keeps_its_root_and_reads_the_real_opendal_file(tmp_path) -> None:
    pytest.importorskip("opendal")
    target = tmp_path / KEY
    target.parent.mkdir(parents=True)
    target.write_bytes(b"jpeg-from-disk")
    settings = Settings()
    settings.assets.user_upload = UserUploadSettings(enabled=True, provider={"scheme": "fs", "root": str(tmp_path)})
    assert settings.assets.user_upload.provider.root == str(tmp_path)
    store = build_user_upload_store(settings)
    assert store.enabled

    async def run():
        data = await store.fetch(KEY)
        await store.close()
        return data

    assert asyncio.run(run()) == b"jpeg-from-disk"


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
    missing_key = "user_upload/profile_bg/jp/uid_9_deadbeef.jpg"

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


@pytest.mark.anyio
async def test_bucket_background_renders_through_the_real_profile_paths(local_assets: list[str]) -> None:
    """Not just the canvas: the encoded bytes must reach the pixels on both backends."""
    from src.sekai.skia_renderer import canvas as canvas_mod

    fake = FakeObjectStore({KEY: _png_bytes((64, 48), (200, 30, 40, 255))}, bucket="user-upload")
    mod.set_user_upload_store(_enabled_store(fake))

    pillow = await drawer.compose_profile_image(_request(KEY))
    assert pillow.getpixel((2, 2))[:3] == (200, 30, 40)  # fade=0, blur off: the bucket colour, verbatim

    canvas = await drawer._build_profile_canvas(_request(KEY))
    builder, memory = canvas_mod.build_canvas_ir(canvas)
    scene = builder.build()
    assert any(value == fake.objects[KEY] for value in memory.values())  # the bytes ride the IR's mem: transport
    assert any(node.get("path", "").startswith("mem:") for node in scene["root"]["children"] if isinstance(node, dict))

    try:
        canvas_mod.load_native_renderer()
    except ImportError:
        return  # lint-test job: no extension; the native-tests job renders it end to end below
    if canvas_mod.skia_plot_enabled():
        payload = await drawer.try_render_profile_payload(_request(KEY))
        assert payload is not None
        skia = Image.open(BytesIO(payload.image_bytes)).convert("RGBA")
        assert skia.getpixel((2, 2))[:3] == (200, 30, 40)
