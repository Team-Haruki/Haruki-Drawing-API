"""C1 candidate keys (plan §6.1, task T7): helpers and first-existing resolution in `base/utils.py`."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path

from PIL import Image
from pydantic import BaseModel
import pytest

from src.assets.mirror import NullMirror, set_asset_mirror
from src.core import missing_asset_telemetry as telemetry
from src.sekai.base import utils
from src.sekai.base.asset_key import AssetKey, candidates, first_candidate, is_candidate_list
from src.sekai.base.image_source import AssetImageRef, MissingImageRef


@pytest.fixture(autouse=True)
def clean_state():
    set_asset_mirror(NullMirror())
    _clear()
    telemetry.reset_missing_asset_stats()
    yield
    _clear()
    telemetry.reset_missing_asset_stats()
    set_asset_mirror(None)


def _clear() -> None:
    utils._load_asset_image_ref_cached.cache_clear()
    utils._resolved_path_cache.clear()
    with utils._missing_placeholder_lock:
        utils._missing_placeholder_logged.clear()
    with utils._image_cache_lock:
        for image, _ in utils._image_cache.values():
            image.close()
        utils._image_cache.clear()
        utils._image_cache_total_bytes = 0


def _save(path: Path, size: tuple[int, int] = (12, 8)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, (20, 80, 140, 255)).save(path)
    return path


def _reason(name: str) -> int:
    return telemetry.get_missing_asset_stats()["by_reason"][name]


# ---------------------------------------------------------------------------------------------------- helpers
def test_candidates_normalises_every_shape() -> None:
    assert candidates(None) == []
    assert candidates("") == []
    assert candidates("   ") == []
    assert candidates([]) == []
    assert candidates(" a.png ") == [" a.png "]  # a legacy string is passed through exactly as sent
    assert candidates([" b.png", "", "  ", "a.png", "b.png", 3]) == ["b.png", "a.png"]  # type: ignore[list-item]


def test_first_candidate_and_is_candidate_list() -> None:
    assert first_candidate(None) is None
    assert first_candidate("") is None
    assert first_candidate([]) is None
    assert first_candidate("x.png") == "x.png"
    assert first_candidate(["", "y.png", "z.png"]) == "y.png"
    assert is_candidate_list(["a"]) is True
    assert is_candidate_list([]) is True
    assert is_candidate_list("a") is False
    assert is_candidate_list(None) is False


def test_pydantic_keeps_a_string_a_string_and_an_array_a_list() -> None:
    class Model(BaseModel):
        path: AssetKey
        optional: AssetKey | None = None

    assert Model.model_validate({"path": "a"}).path == "a"
    assert Model.model_validate({"path": ["a", "b"]}).path == ["a", "b"]
    assert Model.model_validate({"path": "a", "optional": ["c"]}).optional == ["c"]
    assert Model.model_validate({"path": "a"}).optional is None


# ---------------------------------------------------------------------------------------------------- refs
def test_asset_image_ref_takes_the_first_existing_candidate_in_order(tmp_path) -> None:
    _save(tmp_path / "second.png", (5, 4))
    _save(tmp_path / "third.png", (7, 6))

    ref = asyncio.run(utils.get_asset_image_ref(tmp_path, ["first.png", "second.png", "third.png"]))

    assert isinstance(ref, AssetImageRef)
    assert ref.path == (tmp_path / "second.png").resolve()
    assert ref.size == (5, 4)
    assert telemetry.get_missing_asset_stats()["total"] == 0  # a candidate miss is not a missing asset

    _save(tmp_path / "first.png", (3, 2))
    ref = asyncio.run(utils.get_asset_image_ref(tmp_path, ["first.png", "second.png"]))
    assert ref.path == (tmp_path / "first.png").resolve()


def test_a_legacy_string_resolves_exactly_as_before(tmp_path) -> None:
    _save(tmp_path / "icon.png")
    by_str = asyncio.run(utils.get_asset_image_ref(tmp_path, "icon.png"))
    by_list = asyncio.run(utils.get_asset_image_ref(tmp_path, ["icon.png"]))
    assert by_str == by_list


@pytest.mark.parametrize("on_missing", ["placeholder", "raise"])
def test_traversal_propagates_immediately_for_the_offending_candidate(tmp_path, on_missing, monkeypatch) -> None:
    _save(tmp_path / "later.png")
    tried: list[str] = []
    original = utils._resolve_and_stat

    def spy(base_path, path, **kwargs):
        tried.append(path)
        return original(base_path, path, **kwargs)

    monkeypatch.setattr(utils, "_resolve_and_stat", spy)

    with pytest.raises(ValueError, match="越界"):
        asyncio.run(utils.get_asset_image_ref(tmp_path, ["missing.png", "../escape.png", "later.png"], on_missing))
    assert tried == ["missing.png", "../escape.png"]

    # A candidate that exists BEFORE the offending one wins and the traversal is never looked at.
    _save(tmp_path / "missing.png")
    ref = asyncio.run(utils.get_asset_image_ref(tmp_path, ["missing.png", "../escape.png"], on_missing))
    assert isinstance(ref, AssetImageRef)


def test_all_candidates_missing_placeholder_logs_the_first_candidate(tmp_path, caplog) -> None:
    caplog.set_level(logging.WARNING, logger=utils.logger.name)

    ref = asyncio.run(utils.get_asset_image_ref(tmp_path, ["background/story_bg_a.png", "icon/b.png"]))

    assert isinstance(ref, MissingImageRef)
    assert ref.variant == "landscape"  # guessed from the FIRST candidate
    messages = [r.getMessage() for r in caplog.records]
    assert any(
        "background/story_bg_a.png" in m and str((tmp_path / "background" / "story_bg_a.png").resolve()) in m
        for m in messages
    )
    assert not any("icon/b.png" in m for m in messages)
    assert _reason("candidates_exhausted") == 1
    assert _reason("local_not_found") == 0


def test_all_candidates_missing_raise_uses_the_first_candidates_error(tmp_path) -> None:
    with pytest.raises(FileNotFoundError) as excinfo:
        asyncio.run(utils.get_asset_image_ref(tmp_path, ["a.png", "b.png"], on_missing="raise"))

    assert type(excinfo.value) is FileNotFoundError
    assert str(excinfo.value) == f"图片文件不存在: {(tmp_path / 'a.png').resolve()}"
    assert _reason("candidates_exhausted") == 1


@pytest.mark.parametrize("key", [[], ["", "  "]])
def test_an_empty_candidate_list_is_an_empty_path(tmp_path, key) -> None:
    ref = asyncio.run(utils.get_asset_image_ref(tmp_path, key))
    assert isinstance(ref, MissingImageRef)
    with pytest.raises(ValueError, match="不能为空"):
        asyncio.run(utils.get_asset_image_ref(tmp_path, key, on_missing="raise"))
    assert _reason("empty_path") == 2
    with pytest.raises(FileNotFoundError):
        utils._resolve_key_and_stat(tmp_path, key)


def test_asset_image_refs_batch_accepts_list_elements(tmp_path) -> None:
    _save(tmp_path / "b.png", (4, 4))
    _save(tmp_path / "c.png", (6, 6))

    refs = asyncio.run(
        utils.get_asset_image_refs(tmp_path, [["a.png", "b.png"], "c.png", None, [], ["x.png", "y.png"]] * 5)
    )

    assert len(refs) == 25
    first_five = refs[:5]
    assert isinstance(first_five[0], AssetImageRef)
    assert first_five[0].size == (4, 4)
    assert isinstance(first_five[1], AssetImageRef)
    assert first_five[1].size == (6, 6)
    assert isinstance(first_five[2], MissingImageRef)
    assert isinstance(first_five[3], MissingImageRef)
    assert isinstance(first_five[4], MissingImageRef)
    assert _reason("candidates_exhausted") == 5
    assert _reason("empty_path") == 10


# ---------------------------------------------------------------------------------------------------- signatures
def test_collect_asset_signatures_stats_every_candidate(tmp_path) -> None:
    _save(tmp_path / "b.png")
    material = {"card": {"image": ["a.png", "b.png"]}, "note": "not-an-asset"}

    signatures = utils.collect_asset_signatures(tmp_path, material)

    assert set(signatures) == {"a.png", "b.png"}
    assert signatures["a.png"] == {"source_path": "a.png", "missing": True}
    assert signatures["b.png"]["resolved_path"] == str((tmp_path / "b.png").resolve())


def test_image_asset_signature_of_a_list_keeps_missing_markers(tmp_path) -> None:
    _save(tmp_path / "b.png")

    signature = utils.get_image_asset_signature(tmp_path, ["a.png", " b.png ", "a.png"])

    assert signature is not None
    assert list(signature) == ["candidates"]
    first, second = signature["candidates"]
    assert first == {"source_path": "a.png", "missing": True}
    assert second["source_path"] == "b.png"
    assert second["size"] == (tmp_path / "b.png").stat().st_size
    assert utils.get_image_asset_signature(tmp_path, []) is None
    assert utils.get_image_asset_signature(tmp_path, ["  "]) is None
    assert utils.get_image_asset_signature(tmp_path, "b.png") == second


# ---------------------------------------------------------------------------------------------------- pixel loaders
def test_pixel_loaders_accept_a_candidate_list(tmp_path) -> None:
    _save(tmp_path / "wide.png", (20, 10))
    key = ["gone.png", "wide.png"]

    assert asyncio.run(utils.get_img_from_path(tmp_path, key)).size == (20, 10)
    assert asyncio.run(utils.get_img_resized(tmp_path, key, 8, 4)).size == (8, 4)
    assert asyncio.run(utils.get_img_resized(tmp_path, key, 0, 4)).size == (20, 10)
    assert asyncio.run(utils.get_img_resized_long_edge(tmp_path, key, 10)).size == (10, 5)
    assert utils._load_image_contain_resized_sync(tmp_path, key, 6, 6).size == (6, 3)

    # The resize cache stays keyed by the RESOLVED path: no new key shape.
    resolved = str((tmp_path / "wide.png").resolve())
    assert all(cache_key[0] == resolved for cache_key in utils._image_cache)


def test_pixel_loaders_degrade_for_exhausted_and_empty_lists(tmp_path) -> None:
    assert asyncio.run(utils.get_img_from_path(tmp_path, ["a.png", "b.png"])).size == (512, 512)
    assert asyncio.run(utils.get_img_resized(tmp_path, ["a.png"], 8, 8)).size == (8, 8)
    assert asyncio.run(utils.get_img_resized(tmp_path, [], 8, 8)).size == (8, 8)
    assert asyncio.run(utils.get_img_from_path(tmp_path, [])).size == (512, 512)
    with pytest.raises(ValueError, match="不能为空"):
        asyncio.run(utils.get_img_from_path(tmp_path, [], on_missing="raise"))
    with pytest.raises(ValueError, match="不能为空"):
        asyncio.run(utils.get_img_resized(tmp_path, [" "], 8, 8, on_missing="raise"))
    with pytest.raises(FileNotFoundError):
        asyncio.run(utils.get_img_resized(tmp_path, ["a.png"], 8, 8, on_missing="raise"))
    assert _reason("candidates_exhausted") == 3
    assert _reason("empty_path") == 4


# ---------------------------------------------------------------------------------------------------- first existing
def test_first_existing_asset_path(tmp_path) -> None:
    _save(tmp_path / "b.png")

    assert utils.first_existing_asset_path(tmp_path, None) is None
    assert utils.first_existing_asset_path(tmp_path, []) is None
    assert utils.first_existing_asset_path(tmp_path, "  ") is None
    assert utils.first_existing_asset_path(tmp_path, ["a.png", "b.png"]) == (tmp_path / "b.png").resolve()
    assert utils.first_existing_asset_path(tmp_path, "b.png") == (tmp_path / "b.png").resolve()
    assert utils.first_existing_asset_path(tmp_path, ["a.png", "c.png"]) is None
    assert utils.first_existing_asset_path(tmp_path, "a.png") is None
    with pytest.raises(ValueError, match="越界"):
        utils.first_existing_asset_path(tmp_path, ["a.png", "../escape.png"])
    assert telemetry.get_missing_asset_stats()["total"] == 0


def test_birthday_candidates_are_all_tried_before_the_year_fallback(tmp_path) -> None:
    root = tmp_path / "asset" / "jp-assets" / "ondemand" / "mysekai" / "birthday"
    _save(root / "miku_2024" / "icon_refresh.png", (3, 3))
    past = _save(root / "miku_2025" / "icon_refresh.png", (4, 4))
    future = _save(root / "miku_2027" / "icon_refresh.png", (5, 5))
    rel = "asset/jp-assets/ondemand/mysekai/birthday/miku_{}/icon_refresh.png"
    ordered = [rel.format(year) for year in (2026, 2025, 2024, 2027)]

    assert utils.first_existing_asset_path(tmp_path, ordered) == past.resolve()
    assert utils.first_existing_asset_path(tmp_path, [ordered[0], ordered[3]]) == future.resolve()
    assert _reason("birthday_fallback") == 0

    # Nothing in the list exists: the local year fallback (latest <= year) fires once and is counted.
    stale = [rel.format(2030), rel.format(2031)]
    assert utils.first_existing_asset_path(tmp_path, stale) == future.resolve()
    assert _reason("birthday_fallback") == 1
    assert _reason("candidates_exhausted") == 0


def test_legacy_key_is_hashable_and_string_exact() -> None:
    from src.sekai.base.asset_key import legacy_key

    assert legacy_key(" a ") == " a "
    assert legacy_key(["", " b ", "c"]) == "b"
    assert legacy_key(None) is None
