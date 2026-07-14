import asyncio
from datetime import timedelta
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
    assert isinstance(placeholder, Image.Image)
    assert placeholder.size == regular_placeholder.size


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
