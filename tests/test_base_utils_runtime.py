from __future__ import annotations

from datetime import UTC, datetime, timedelta
import os

from PIL import Image
import pytest

from src.sekai.base import utils


def _image(size=(8, 6), color=(10, 20, 30, 255)) -> Image.Image:
    return Image.new("RGBA", size, color)


def test_ttl_image_cache_covers_disabled_hit_replace_expiry_and_eviction() -> None:
    disabled = utils._TTLImageCache(0, 0, 0)
    disabled.set("x", _image())
    assert disabled.get("x") is None
    assert disabled.stats()["enabled"] is False

    cache = utils._TTLImageCache(1, 8 * 6 * 4, 60)
    assert cache.get("missing") is None
    cache.set("a", _image())
    hit = cache.get("a")
    assert hit is not None
    assert hit.size == (8, 6)
    hit.close()

    cache.set("a", _image(color=(1, 2, 3, 255)))
    cache.set("b", _image())
    assert cache.get("a") is None
    assert cache.stats()["evictions"] == 1

    image, size, _expires = cache._cache["b"]
    cache._cache["b"] = (image, size, 0.0)
    assert cache.get("b") is None
    stats = cache.stats()
    assert stats["expired"] == 1
    assert stats["hit_rate"] is not None
    cache.clear()
    assert cache.stats()["entries"] == 0


def test_disk_image_cache_covers_round_trip_expiry_corruption_cleanup_and_errors(tmp_path, monkeypatch) -> None:
    disabled = utils._DiskImageCache(tmp_path / "disabled", 0)
    disabled.set("n", "k", _image())
    assert disabled.get("n", "k") is None
    assert disabled.cleanup_expired() == 0

    cache = utils._DiskImageCache(tmp_path / "cache", 10)
    assert cache._path("", "a").parent.name == "default"
    assert cache._path("/nested/", "a").parent.name == "nested"
    assert cache.get("n", "missing") is None
    cache.set("n", "ok", _image())
    loaded = cache.get("n", "ok")
    assert loaded is not None
    assert loaded.size == (8, 6)
    loaded.close()

    expired_path = cache._path("n", "expired")
    expired_path.parent.mkdir(parents=True, exist_ok=True)
    _image().save(expired_path)
    os.utime(expired_path, (0, 0))
    assert cache.get("n", "expired") is None
    assert not expired_path.exists()

    corrupt = cache._path("n", "corrupt")
    corrupt.write_bytes(b"not a png")
    assert cache.get("n", "corrupt") is None

    old = cache._path("n", "old")
    _image().save(old)
    os.utime(old, (0, 0))
    assert cache.cleanup_expired() == 1
    assert cache.stats()["entries"] == 2

    monkeypatch.setattr(Image.Image, "save", lambda *_args, **_kwargs: (_ for _ in ()).throw(OSError("disk")))
    cache.set("n", "failed", _image())
    assert cache.stats()["errors"] >= 2


@pytest.mark.anyio
async def test_exact_and_long_edge_resize_cover_empty_missing_and_both_orientations(tmp_path, monkeypatch) -> None:
    wide_path = tmp_path / "wide.png"
    tall_path = tmp_path / "tall.png"
    _image((20, 10)).save(wide_path)
    _image((10, 20)).save(tall_path)
    monkeypatch.setattr(utils, "_cache_enabled", lambda _path: False)

    assert utils._load_image_resized_sync(tmp_path, "wide.png", 7, 5).size == (7, 5)
    assert utils._load_image_resized_full_path_sync(wide_path, 6, 4).size == (6, 4)
    assert (await utils.get_img_resized(tmp_path, "wide.png", 9, 3)).size == (9, 3)
    assert (await utils.get_img_resized_long_edge(tmp_path, "wide.png", 8)).size == (8, 4)
    assert (await utils.get_img_resized_long_edge(tmp_path, "tall.png", 8)).size == (4, 8)

    original = _image((4, 3))

    async def fake_get(*_args, **_kwargs):
        return original.copy()

    monkeypatch.setattr(utils, "get_img_from_path", fake_get)
    assert (await utils.get_img_resized(tmp_path, "x", 0, 2)).size == (4, 3)
    assert (await utils.get_img_resized_long_edge(tmp_path, "x", 0)).size == (4, 3)
    assert (await utils.get_img_resized(tmp_path, None, 5, 5)).size == (5, 5)
    with pytest.raises(ValueError, match="图片路径不能为空"):
        await utils.get_img_resized(tmp_path, "", 5, 5, on_missing="raise")

    async def failing_pool(*_args, **_kwargs):
        raise FileNotFoundError("missing")

    monkeypatch.setattr(utils, "run_in_pool", failing_pool)
    assert (await utils.get_img_resized(tmp_path, "missing.png", 5, 4)).size == (5, 4)
    with pytest.raises(FileNotFoundError):
        await utils.get_img_resized(tmp_path, "missing.png", 5, 4, on_missing="raise")


def test_contain_resize_batch_and_cache_paths(tmp_path, monkeypatch) -> None:
    source = tmp_path / "source.png"
    _image((20, 10)).save(source)
    same = _image((2, 2))
    assert utils._contain_resize(same, 2, 2) is same
    resized = utils._contain_resize(_image((20, 10)), 5, 5)
    assert resized.size == (5, 2)

    monkeypatch.setattr(utils, "_cache_enabled", lambda _path: False)
    assert utils._load_image_contain_resized_sync(tmp_path, "source.png", 6, 6).size == (6, 3)
    result = utils.batch_load_and_contain_resize(tmp_path, ["source.png", "missing.png"], 4, 4)
    assert result["source.png"].size == (4, 2)
    assert max(result["missing.png"].size) <= 4

    sentinel = _image((3, 3))
    monkeypatch.setattr(utils, "_cache_enabled", lambda _path: True)
    monkeypatch.setattr(utils, "_load_image_cached", lambda *_args, **_kwargs: sentinel)
    assert utils._load_image_contain_resized_sync(tmp_path, "source.png", 6, 6) is sentinel


@pytest.mark.anyio
async def test_concat_datetime_formatting_and_file_helpers_cover_edge_cases(tmp_path, monkeypatch) -> None:
    assert await utils.concat_images([]) is None
    assert await utils.concat_images([None, None]) is None
    a, b = _image((2, 3)), _image((4, 1))
    assert (await utils.concat_images([a, b], "h")).size == (6, 3)
    assert (await utils.concat_images([a, b], "v")).size == (4, 4)

    now = datetime.now(UTC)
    assert "s" in utils.get_readable_datetime(now + timedelta(seconds=20), False, True)
    assert "m" in utils.get_readable_datetime(now + timedelta(minutes=2), False, True)
    assert "h" in utils.get_readable_datetime(now + timedelta(hours=2, minutes=3), False, True)
    assert "d" in utils.get_readable_datetime(now + timedelta(days=2), False, True)
    assert "前" in utils.get_readable_datetime(now - timedelta(minutes=2), False)
    assert "(" in utils.get_readable_datetime(now + timedelta(seconds=2), True)
    assert utils.truncate(None, 2) == "<None>"
    assert utils.truncate("中文a", 2) == "中..."
    assert utils.get_float_str(1.20) == "1.2"
    assert utils.get_chara_nickname(999) is None

    assert utils.rand_filename(".png").endswith(".png")
    nested = tmp_path / "a" / "b.txt"
    assert utils.create_parent_folder(str(nested)) == str(nested)
    nested.write_text("x")
    utils.remove_file(nested)
    assert not nested.exists()
    utils.remove_file(nested)

    expired = tmp_path / "expired"
    expired.write_text("x")
    future = tmp_path / "future"
    future.write_text("x")
    monkeypatch.setattr(
        utils,
        "_tmp_files_to_remove",
        [(str(expired), datetime.now() - timedelta(seconds=1)), (str(future), datetime.now() + timedelta(days=1))],
    )
    assert utils.cleanup_expired_tmp_files() == 1
    assert utils._tmp_files_to_remove == [(str(future), utils._tmp_files_to_remove[0][1])]


def test_shutdown_utils_closes_owned_resources_without_touching_real_globals(monkeypatch) -> None:
    calls: list[str] = []

    class Executor:
        def shutdown(self, *, wait):
            assert wait is False
            calls.append("executor")

    class Cache:
        def clear(self):
            calls.append("composed")

    image = _image()
    thumb = _image()
    placeholder = _image()
    monkeypatch.setattr(utils, "_default_pool_executor", Executor())
    monkeypatch.setattr(utils, "cleanup_expired_tmp_files", lambda: calls.append("tmp"))
    monkeypatch.setattr(utils, "_image_cache", {("k",): (image, 1)})
    monkeypatch.setattr(utils, "_thumb_cache", {("k",): (thumb, 1)})
    monkeypatch.setattr(utils, "_missing_placeholder_cache", {"k": placeholder})
    monkeypatch.setattr(utils, "_missing_placeholder_logged", {"k"})
    monkeypatch.setattr(utils, "_composed_image_cache", Cache())

    from src.sekai.skia_renderer import payload_cache

    monkeypatch.setattr(payload_cache, "clear_skia_payload_cache", lambda: calls.append("skia"))
    utils.shutdown_utils()
    assert calls == ["executor", "tmp", "composed", "skia"]
    assert utils._image_cache == {}
    assert utils._thumb_cache == {}
    assert utils._missing_placeholder_cache == {}
