import asyncio
from datetime import timedelta
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

    utils._composed_image_cache.clear()


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


def test_rendered_image_cache_key_is_stable_for_dict_ordering():
    first = utils.build_rendered_image_cache_key("sample", {"b": 2, "a": 1})
    second = utils.build_rendered_image_cache_key("sample", {"a": 1, "b": 2})
    changed = utils.build_rendered_image_cache_key("sample", {"a": 1, "b": 2}, extra={"version": 2})

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
