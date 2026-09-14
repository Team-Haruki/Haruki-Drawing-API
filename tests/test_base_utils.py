import asyncio
from datetime import timedelta
import logging
import os
from pathlib import Path
from tempfile import TemporaryDirectory

from PIL import Image
import pytest

from src.sekai.base import utils


@pytest.fixture(autouse=True)
def clean_runtime_state():
    _clear_runtime_state()
    yield
    _clear_runtime_state()


def _clear_runtime_state() -> None:
    with utils._image_cache_lock:
        for image, _ in utils._image_cache.values():
            image.close()
        utils._image_cache.clear()
        utils._image_cache_total_bytes = 0
        utils._image_cache_hits = 0
        utils._image_cache_misses = 0
        utils._image_cache_sets = 0
        utils._image_cache_evictions = 0

    with utils._thumb_cache_lock:
        for image, _ in utils._thumb_cache.values():
            image.close()
        utils._thumb_cache.clear()
        utils._thumb_cache_total_bytes = 0
        utils._thumb_cache_hits = 0
        utils._thumb_cache_misses = 0
        utils._thumb_cache_sets = 0
        utils._thumb_cache_evictions = 0

    with utils._missing_placeholder_lock:
        for image in utils._missing_placeholder_cache.values():
            image.close()
        utils._missing_placeholder_cache.clear()
        utils._missing_placeholder_logged.clear()

    with utils._tmp_files_lock:
        utils._tmp_files_to_remove.clear()

    utils._load_asset_image_ref_cached.cache_clear()
    utils._composed_image_cache.clear()
    utils._resolved_path_cache.clear()
    utils._resolved_existing_cache.clear()


def _save_image(path: Path, size: tuple[int, int] = (12, 8)) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, (20, 80, 140, 255)).save(path)


@pytest.mark.parametrize(
    ("precision", "expected"),
    [
        ("d", "1天"),
        ("h", "1天2小时"),
        ("m", "1天2小时3分钟"),
        ("s", "1天2小时3分钟4秒"),
    ],
)
def test_get_readable_timedelta_respects_precision(precision: str, expected: str) -> None:
    delta = timedelta(days=1, hours=2, minutes=3, seconds=4)

    assert utils.get_readable_timedelta(delta, precision) == expected
    assert utils.get_readable_timedelta(delta, precision, use_en_unit=True).endswith(
        {"d": "1d", "h": "2h", "m": "3m", "s": "4s"}[precision]
    )


def test_get_readable_timedelta_keeps_first_nonzero_unit_and_clamps_negative() -> None:
    assert utils.get_readable_timedelta(timedelta(hours=2, minutes=3), "d") == "2小时"
    assert utils.get_readable_timedelta(timedelta(0)) == ""
    assert utils.get_readable_timedelta(timedelta(seconds=-1)) == "0秒"
    assert utils.get_readable_timedelta(timedelta(seconds=-1), use_en_unit=True) == "0s"


def test_get_img_from_path_blocks_path_traversal(tmp_path):
    with pytest.raises(ValueError, match="越界"):
        asyncio.run(utils.get_img_from_path(tmp_path, "../outside.png", on_missing="raise"))


def test_missing_image_placeholder_is_returned_without_real_assets(tmp_path):
    image = asyncio.run(utils.get_img_from_path(tmp_path, "missing/banner/title.png"))

    assert image.size == (960, 320)
    assert image.mode == "RGBA"


def test_image_and_thumbnail_caches_record_hits(monkeypatch):
    monkeypatch.setattr(utils, "IMAGE_CACHE_SIZE", 8)
    monkeypatch.setattr(utils, "IMAGE_CACHE_MAX_BYTES", 1024 * 1024)
    monkeypatch.setattr(utils, "THUMB_CACHE_SIZE", 8)
    monkeypatch.setattr(utils, "THUMB_CACHE_MAX_BYTES", 1024 * 1024)

    with TemporaryDirectory(prefix="haruki-cache-") as tmpdir:
        base_path = Path(tmpdir)
        _save_image(base_path / "regular.png")
        _save_image(base_path / "thumbnail" / "icon.png", size=(10, 10))

        first_regular = asyncio.run(utils.get_img_resized(base_path, "regular.png", 6, 4))
        second_regular = asyncio.run(utils.get_img_resized(base_path, "regular.png", 6, 4))
        first_thumb = asyncio.run(utils.get_img_from_path(base_path, "thumbnail/icon.png"))
        second_thumb = asyncio.run(utils.get_img_from_path(base_path, "thumbnail/icon.png"))

        assert first_regular.size == second_regular.size == (6, 4)
        assert first_thumb.size == second_thumb.size == (10, 10)

    stats = utils.get_runtime_cache_stats()
    assert stats["image_cache"]["entries"] == 1
    assert stats["image_cache"]["hits"] == 1
    assert stats["image_cache"]["misses"] == 1
    assert stats["thumbnail_cache"]["entries"] == 1
    assert stats["thumbnail_cache"]["hits"] == 1
    assert stats["thumbnail_cache"]["misses"] == 1


def test_clear_runtime_memory_caches_includes_native_renderer(monkeypatch):
    from src.sekai.profile.custom_profile import cache as custom_cache
    from src.sekai.skia_renderer import canvas, payload_cache

    calls = []
    monkeypatch.setattr(canvas, "clear_native_renderer_caches", lambda: calls.append("native"))
    monkeypatch.setattr(payload_cache, "clear_skia_payload_cache", lambda: calls.append("payload"))
    monkeypatch.setattr(custom_cache, "clear_custom_profile_caches", lambda: calls.append("custom"))

    utils.clear_runtime_memory_caches()

    assert calls == ["payload", "native", "custom"]


@pytest.mark.parametrize("path", ["regular.png", "thumbnail/icon.png"])
def test_image_cache_replacement_and_entry_limit_eviction(monkeypatch, path: str) -> None:
    monkeypatch.setattr(utils, "IMAGE_CACHE_SIZE", 1)
    monkeypatch.setattr(utils, "IMAGE_CACHE_MAX_BYTES", 1024 * 1024)
    monkeypatch.setattr(utils, "THUMB_CACHE_SIZE", 1)
    monkeypatch.setattr(utils, "THUMB_CACHE_MAX_BYTES", 1024 * 1024)
    first = Image.new("RGBA", (2, 2), (1, 2, 3, 255))
    replacement = Image.new("RGBA", (3, 3), (4, 5, 6, 255))
    newest = Image.new("RGBA", (4, 4), (7, 8, 9, 255))

    utils._put_image_cache(path, 1, 1, first)
    utils._put_image_cache(path, 1, 1, replacement)
    utils._put_image_cache(path, 2, 2, newest)

    cache = utils._thumb_cache if "thumbnail" in path else utils._image_cache
    stats = utils.get_runtime_cache_stats()["thumbnail_cache" if "thumbnail" in path else "image_cache"]
    assert len(cache) == 1
    assert next(iter(cache.values()))[0] is newest
    assert stats["sets"] == 3
    assert stats["evictions"] == 1


def test_put_image_cache_is_disabled_when_a_limit_is_zero(monkeypatch) -> None:
    monkeypatch.setattr(utils, "IMAGE_CACHE_SIZE", 0)
    image = Image.new("RGBA", (2, 2), (1, 2, 3, 255))

    utils._put_image_cache("regular.png", 1, 1, image)

    assert not utils._image_cache
    assert utils._image_cache_sets == 0
    image.close()


def test_asset_path_provenance_only_survives_while_pixels_are_pristine(tmp_path):
    path = tmp_path / "asset.png"
    _save_image(path)

    image = asyncio.run(utils.get_img_from_path(tmp_path, "asset.png", on_missing="raise"))
    assert utils.get_pristine_image_asset_path(image) == path.resolve()

    copied = image.copy()
    resized = image.resize((6, 4))
    cached_resize = asyncio.run(utils.get_img_resized(tmp_path, "asset.png", 6, 4))
    assert utils.get_pristine_image_asset_path(copied) is None
    assert utils.get_pristine_image_asset_path(resized) is None
    assert utils.get_pristine_image_asset_path(cached_resize) is None

    image.paste((255, 0, 0, 255), (0, 0, 1, 1))
    assert utils.get_pristine_image_asset_path(image) is None

    image.close()
    copied.close()
    resized.close()
    cached_resize.close()


def test_asset_image_ref_reads_header_without_populating_pixel_cache(tmp_path):
    path = tmp_path / "asset.png"
    _save_image(path, size=(17, 9))

    image_ref = asyncio.run(utils.get_asset_image_ref(tmp_path, "asset.png", on_missing="raise"))

    assert isinstance(image_ref, utils.AssetImageRef)
    assert image_ref.size == (17, 9)
    assert image_ref.mode == "RGBA"
    assert utils.get_pristine_image_asset_path(image_ref) == path.resolve()
    assert not utils._image_cache
    assert not utils._thumb_cache


def test_asset_image_ref_blocks_path_traversal_and_preserves_missing_placeholder(tmp_path):
    with pytest.raises(ValueError, match="越界"):
        asyncio.run(utils.get_asset_image_ref(tmp_path, "../outside.png", on_missing="raise"))

    placeholder = asyncio.run(utils.get_asset_image_ref(tmp_path, "missing/icon.png"))
    regular_placeholder = asyncio.run(utils.get_img_from_path(tmp_path, "missing/icon.png"))
    assert isinstance(placeholder, utils.MissingImageRef)
    assert placeholder.size == regular_placeholder.size
    assert utils.resolve_image_source_sync(placeholder).tobytes() == regular_placeholder.tobytes()


def test_a_replaced_asset_is_picked_up_despite_the_cached_path_resolution(tmp_path):
    """THE failure mode the path-resolution cache could introduce.

    ``_resolve_asset_path`` memoizes the realpath walk, which was costing 33k lstat calls on a
    696-jacket music list. What must NOT be memoized with it is the ``stat``: its mtime/size are the
    image cache key, so a stale one would keep serving the OLD pixels of a replaced asset forever —
    an asset sync would appear to have silently not happened.
    """
    path = tmp_path / "asset.png"
    _save_image(path, size=(12, 8))

    first = asyncio.run(utils.get_img_from_path(tmp_path, "asset.png"))
    assert first.size == (12, 8)

    # Replace the file in place (what an asset sync does), then re-read through the warm cache.
    Image.new("RGBA", (30, 20), (200, 30, 30, 255)).save(path)
    os.utime(path, (path.stat().st_atime, path.stat().st_mtime + 10))

    second = asyncio.run(utils.get_img_from_path(tmp_path, "asset.png"))
    assert second.size == (30, 20)
    assert second.getpixel((0, 0)) == (200, 30, 30, 255)


def test_an_asset_that_lands_later_is_not_cached_as_missing(tmp_path):
    """Only successful resolutions are cached. Caching a negative would keep an asset invisible
    after it lands on disk — the service would need a restart to see a newly synced file."""
    placeholder = asyncio.run(utils.get_img_from_path(tmp_path, "late.png"))
    assert placeholder.size == utils._get_missing_placeholder_image(str(tmp_path / "late.png")).size

    _save_image(tmp_path / "late.png", size=(21, 13))

    assert asyncio.run(utils.get_img_from_path(tmp_path, "late.png")).size == (21, 13)
    assert utils.resolve_existing_asset_path(tmp_path / "late.png") == (tmp_path / "late.png").resolve()


def test_path_traversal_stays_rejected_and_is_never_cached(tmp_path):
    """The escape check lives on the cached side of ``_resolve_asset_path``, so prove a rejected
    path is not admitted to the cache — otherwise the SECOND attempt would sail through."""
    for _ in range(2):
        with pytest.raises(ValueError, match="越界"):
            utils._resolve_asset_path(tmp_path, "../outside.png")

    assert not utils._resolved_path_cache


def test_resolve_existing_asset_path_reports_a_vanished_file(tmp_path):
    """IRPainter maps an absolute asset path back to an assets-root-relative one through this. It
    must still report a vanished file as missing, because Rust SKIPS a node whose asset will not
    load (leaving a hole) while Pillow draws a placeholder — so the miss has to be caught in Python
    for the two backends to agree."""
    path = tmp_path / "asset.png"
    assert utils.resolve_existing_asset_path(path) is None

    _save_image(path)
    assert utils.resolve_existing_asset_path(path) == path.resolve()
    path.unlink()
    assert utils.resolve_existing_asset_path(path) is None


def test_birthday_fallback_prefers_latest_same_or_older_year(tmp_path) -> None:
    requested = tmp_path / "static_images" / "mysekai" / "birthday" / "miku_2026" / "icon" / "item.png"
    older = tmp_path / "static_images" / "mysekai" / "birthday" / "miku_2024" / "icon" / "item.png"
    newer = tmp_path / "static_images" / "mysekai" / "birthday" / "miku_2027" / "icon" / "item.png"
    _save_image(older)
    _save_image(newer)

    assert utils._resolve_birthday_year_fallback(requested, tmp_path) == older
    earliest_request = requested.parents[2] / "miku_2023" / "icon" / "item.png"
    assert utils._resolve_birthday_year_fallback(earliest_request, tmp_path) == older


def test_birthday_fallback_uses_generic_and_rejects_unrelated_paths(tmp_path) -> None:
    generic = (
        tmp_path
        / "static_images"
        / "mysekai"
        / "harvest_fixture_icon"
        / "rarity_1"
        / "mdl_site_wood_common_fieldtree01.png"
    )
    _save_image(generic)
    requested = tmp_path / "static_images" / "mysekai" / "birthday" / "rin_2026" / "icon" / "item.png"

    assert utils._resolve_birthday_year_fallback(requested, tmp_path) == generic
    (tmp_path / "static_images" / "mysekai" / "birthday").mkdir()
    assert utils._resolve_birthday_year_fallback(requested, tmp_path) == generic
    assert utils._resolve_birthday_year_fallback(tmp_path / "unrelated.png", tmp_path) is None
    assert utils._resolve_birthday_year_fallback(requested, tmp_path / "other-root") is None


def test_rendered_image_cache_key_is_stable_for_dict_ordering():
    first = utils.build_rendered_image_cache_key("sample", {"b": 2, "a": 1})
    second = utils.build_rendered_image_cache_key("sample", {"a": 1, "b": 2})
    changed = utils.build_rendered_image_cache_key("sample", {"a": 1, "b": 2}, extra={"state": "changed"})

    assert first == second
    assert first != changed


def test_temp_file_path_can_schedule_and_cleanup_file(monkeypatch, tmp_path):
    monkeypatch.setattr(utils, "TEMP_FILE_DIR", tmp_path)

    with utils.TempFilePath("txt", remove_after=timedelta(seconds=0)) as path:
        temp_path = Path(path)
        temp_path.write_text("temporary", encoding="utf-8")
        assert temp_path.exists()

    assert utils.cleanup_expired_tmp_files() == 1
    assert not temp_path.exists()


# ---------------------------------------------------------------------------------------------------------------
# Asset-mirror hooks (plan §5.1/§5.2, task T5)
# ---------------------------------------------------------------------------------------------------------------

_MIRROR_LOGICAL = "asset/jp-assets/startapp/music/jacket/j001.png"
_MIRROR_OBJECT_KEY = "jp-assets/startapp/music/jacket/j001.png"


def _png_bytes(size: tuple[int, int] = (9, 7)) -> bytes:
    import io

    buffer = io.BytesIO()
    Image.new("RGBA", size, (1, 2, 3, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def _legacy_resolve_asset_path(base_path: Path, path: str) -> tuple[Path, Path, str]:
    """`_resolve_asset_path` exactly as it was at 5c2f807 (no memo), the regression oracle."""
    resolved_base = base_path.resolve()
    full_path = (resolved_base / path.lstrip("/")).resolve()
    if not full_path.is_relative_to(resolved_base):
        raise ValueError(f"图片路径越界: {path}")
    return resolved_base, full_path, str(full_path)


@pytest.fixture
def null_mirror():
    from src.assets.mirror import NullMirror, set_asset_mirror

    mirror = NullMirror()
    set_asset_mirror(mirror)
    try:
        yield mirror
    finally:
        set_asset_mirror(None)


class _RecordingMirror:
    """A NullMirror-shaped double that records `ensure_local` calls and can hand back a path."""

    version = "rec"

    def __init__(self, result: Path | None = None, reason: str | None = None, calls: list | None = None) -> None:
        self.result = result
        self.reason = reason
        self.calls = calls if calls is not None else []

    def local_path(self, logical: str):
        return None

    def ensure_local(self, logical: str):
        self.calls.append(("ensure_local", logical))
        return self.result

    def last_miss_reason(self):
        return self.reason

    def clear_memos(self) -> None:
        self.calls.append(("clear_memos", None))

    def stats_snapshot(self) -> dict:
        return {"enabled": False}


@pytest.fixture
def recording_mirror():
    from src.assets.mirror import set_asset_mirror

    mirror = _RecordingMirror()
    set_asset_mirror(mirror)
    try:
        yield mirror
    finally:
        set_asset_mirror(None)


@pytest.mark.parametrize(
    "path",
    [
        "asset.png",
        "/leading/slash.png",
        "thumbnail/icon.png",
        "nested/dir/banner.jpg",
        "asset/jp-assets/startapp/music/jacket/j001.png",
        "static_images/mysekai/birthday/miku_2026/icon/item.png",
        "custom_profile/frame.png",
        "fonts/x.otf",
        "missing/icon.png",
        "a/./b/../c.png",
        "late.png",
        "tmp/file.png",
    ],
)
def test_resolve_asset_path_with_local_source_is_identical_to_the_base_commit(tmp_path, null_mirror, path) -> None:
    _save_image(tmp_path / "asset.png")
    expected = _legacy_resolve_asset_path(tmp_path, path)

    assert utils._resolve_asset_path(tmp_path, path) == expected
    assert utils._resolve_asset_path(tmp_path, path) == expected  # memoized entry identical too
    assert (str(tmp_path), path, "") in utils._resolved_path_cache


def test_resolve_asset_path_local_source_still_rejects_traversal(tmp_path, null_mirror) -> None:
    with pytest.raises(ValueError, match="越界"):
        utils._resolve_asset_path(tmp_path, "../outside.png")
    assert not utils._resolved_path_cache


def test_mirror_memo_key_carries_the_version_and_a_bump_remaps(tmp_path, asset_mirror) -> None:
    _base, first, _ = utils._resolve_asset_path(tmp_path, _MIRROR_LOGICAL)
    assert first == (tmp_path / "mirror" / "v0" / _MIRROR_OBJECT_KEY).resolve()
    assert (str(tmp_path), _MIRROR_LOGICAL, "v0") in utils._resolved_path_cache

    assert asset_mirror.set_version("v1") is True
    assert not utils._resolved_path_cache  # set_version calls clear_resolved_path_cache()

    _base, second, _ = utils._resolve_asset_path(tmp_path, _MIRROR_LOGICAL)
    assert second == (tmp_path / "mirror" / "v1" / _MIRROR_OBJECT_KEY).resolve()
    assert (str(tmp_path), _MIRROR_LOGICAL, "v1") in utils._resolved_path_cache


def test_mirror_non_bucket_keys_keep_the_legacy_join(tmp_path, asset_mirror) -> None:
    assert utils._resolve_asset_path(tmp_path, "static_images/x.png") == _legacy_resolve_asset_path(
        tmp_path, "static_images/x.png"
    )


def test_traversal_guard_fires_on_the_mapped_mirror_path(tmp_path, asset_mirror) -> None:
    base = tmp_path / "base"
    outside = tmp_path / "outside"
    base.mkdir()
    outside.mkdir()
    (base / "mirror").symlink_to(outside, target_is_directory=True)

    with pytest.raises(ValueError, match="越界"):
        utils._resolve_asset_path(base, _MIRROR_LOGICAL)
    with pytest.raises(ValueError, match="越界"):
        utils._resolve_asset_path(base, "../outside.png")
    assert not utils._resolved_path_cache


def test_resolve_and_stat_fetches_a_cold_asset_through_the_mirror(tmp_path, asset_mirror) -> None:
    store = asset_mirror.store_for("jp")
    store.objects[_MIRROR_OBJECT_KEY] = _png_bytes((9, 7))

    ref = asyncio.run(utils.get_asset_image_ref(tmp_path, _MIRROR_LOGICAL, on_missing="raise"))

    assert isinstance(ref, utils.AssetImageRef)
    assert ref.size == (9, 7)
    assert ref.path == tmp_path / "mirror" / "v0" / _MIRROR_OBJECT_KEY
    assert store.reads == [_MIRROR_OBJECT_KEY]
    # Warm: a plain local stat, no second remote read.
    asyncio.run(utils.get_asset_image_ref(tmp_path, _MIRROR_LOGICAL, on_missing="raise"))
    assert store.reads == [_MIRROR_OBJECT_KEY]


def test_resolve_and_stat_returns_the_legacy_file_through_the_mirror_fallback(tmp_path, asset_mirror) -> None:
    legacy = tmp_path / _MIRROR_LOGICAL
    _save_image(legacy, size=(5, 4))

    full_path, full_path_str, st = utils._resolve_and_stat(tmp_path, _MIRROR_LOGICAL)

    assert full_path == legacy
    assert full_path_str == str(legacy)
    assert st.st_size == legacy.stat().st_size


def test_resolve_and_stat_order_is_stat_then_mirror_then_birthday_then_raise(
    tmp_path, recording_mirror, monkeypatch
) -> None:
    calls = recording_mirror.calls
    original_birthday = utils._resolve_birthday_year_fallback

    def birthday(full_path, resolved_base):
        calls.append(("birthday", None))
        return original_birthday(full_path, resolved_base)

    original_stat = utils._stat_regular_file

    def stat(path):
        calls.append(("stat", None))
        return original_stat(path)

    monkeypatch.setattr(utils, "_resolve_birthday_year_fallback", birthday)
    monkeypatch.setattr(utils, "_stat_regular_file", stat)

    with pytest.raises(FileNotFoundError) as excinfo:
        utils._resolve_and_stat(tmp_path, "missing/icon.png")

    assert [name for name, _ in calls] == ["stat", "ensure_local", "birthday"]
    assert str(excinfo.value) == f"图片文件不存在: {(tmp_path / 'missing' / 'icon.png').resolve()}"
    assert type(excinfo.value) is FileNotFoundError

    # A local hit never reaches the mirror.
    calls.clear()
    _save_image(tmp_path / "present.png")
    utils._resolve_and_stat(tmp_path, "present.png")
    assert [name for name, _ in calls] == ["stat"]


def test_resolve_and_stat_uses_a_path_the_mirror_hands_back(tmp_path, recording_mirror) -> None:
    elsewhere = tmp_path / "elsewhere.png"
    _save_image(elsewhere)
    recording_mirror.result = elsewhere

    assert utils._resolve_and_stat(tmp_path, "missing.png")[0] == elsewhere

    # A handed-back path that is not a regular file is ignored and the miss proceeds.
    recording_mirror.result = tmp_path / "nope.png"
    with pytest.raises(FileNotFoundError):
        utils._resolve_and_stat(tmp_path, "missing.png")


def test_every_resolve_and_stat_entry_point_reaches_the_mirror_hook(tmp_path, recording_mirror) -> None:
    calls = recording_mirror.calls

    def count() -> int:
        n = sum(1 for name, _ in calls if name == "ensure_local")
        calls.clear()
        return n

    assert utils.get_image_asset_signature(tmp_path, "a.png") == {"source_path": "a.png", "missing": True}
    assert count() == 1
    with pytest.raises(FileNotFoundError):
        utils._load_image_from_path_sync(tmp_path, "b.png")
    assert count() == 1
    with pytest.raises(FileNotFoundError):
        utils._load_asset_image_ref_sync(tmp_path, "c.png")
    assert count() == 1
    with pytest.raises(FileNotFoundError):
        utils._load_image_resized_sync(tmp_path, "d.png", 4, 4)
    assert count() == 1
    with pytest.raises(FileNotFoundError):
        utils._load_image_contain_resized_sync(tmp_path, "e.png", 4, 4)
    assert count() == 1


def test_resolve_logical_file_is_file_generic(tmp_path, asset_mirror) -> None:
    store = asset_mirror.store_for("jp")
    logical = "asset/jp-assets/startapp/music/music_score/0001_01/expert.txt"
    store.objects["jp-assets/startapp/music/music_score/0001_01/expert.txt"] = b"#SUS"

    fetched = utils.resolve_logical_file(tmp_path, logical)
    assert fetched == tmp_path / "mirror" / "v0" / "jp-assets/startapp/music/music_score/0001_01/expert.txt"
    assert fetched.read_bytes() == b"#SUS"
    assert utils.resolve_logical_file(tmp_path, logical) == fetched.resolve()  # warm local stat

    (tmp_path / "static_images").mkdir()
    (tmp_path / "static_images" / "style.css").write_text("x", encoding="utf-8")
    assert (
        utils.resolve_logical_file(tmp_path, "static_images/style.css")
        == (tmp_path / "static_images" / "style.css").resolve()
    )

    with pytest.raises(FileNotFoundError, match="文件不存在"):
        utils.resolve_logical_file(tmp_path, "asset/jp-assets/startapp/music/none.txt")
    with pytest.raises(FileNotFoundError):
        utils.resolve_logical_file(tmp_path, "  ")
    with pytest.raises(ValueError, match="越界"):
        utils.resolve_logical_file(tmp_path, "../escape.txt")


def test_resolve_local_dir_never_fetches_and_never_raises_on_payload_quirks(tmp_path, asset_mirror, caplog) -> None:
    notes = tmp_path / "static_images" / "chart_asset" / "notes"
    notes.mkdir(parents=True)
    store = asset_mirror.store_for("jp")

    assert utils.resolve_local_dir(tmp_path, "static_images/chart_asset/notes") == notes.resolve()

    caplog.set_level(logging.WARNING, logger=utils.logger.name)
    key = "asset/jp-assets/startapp/notes"
    for _ in range(2):
        assert utils.resolve_local_dir(tmp_path, key) == (tmp_path / key).resolve()
    errors = [r for r in caplog.records if "chart.note_host_not_local" in r.getMessage()]
    missing = [r for r in caplog.records if "assets.local_dir_missing" in r.getMessage()]
    assert len(errors) == 1
    assert errors[0].levelno == logging.ERROR
    assert len(missing) == 1
    assert store.reads == []
    assert store.stats == []

    with pytest.raises(ValueError, match="越界"):
        utils.resolve_local_dir(tmp_path, "../escape")


def test_local_dir_log_gate_is_bounded(monkeypatch) -> None:
    monkeypatch.setattr(utils, "_local_dir_logged", {f"missing|k{i}" for i in range(4096)})
    utils._log_local_dir_once("missing", "fresh")
    assert utils._local_dir_logged == {"missing|fresh"}


def test_clear_runtime_memory_caches_clears_mirror_memos(recording_mirror) -> None:
    utils._resolved_path_cache[("b", "p", "v")] = (Path("a"), Path("b"), "b")

    utils.clear_runtime_memory_caches()

    assert not utils._resolved_path_cache
    assert ("clear_memos", None) in recording_mirror.calls


def test_runtime_cache_stats_expose_asset_mirror_and_missing_assets(null_mirror) -> None:
    stats = utils.get_runtime_cache_stats()

    assert len(stats) == 10
    assert stats["asset_mirror"]["enabled"] is False
    assert stats["asset_mirror"]["source"] == "local"
    assert set(stats["missing_assets"]) == {"total", "by_reason"}
