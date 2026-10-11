"""Optional assets: an absent one is skipped or substituted without counting as a missing-asset placeholder.

`X-Haruki-Render-Missing-Assets` reports renders that drew a "?" or lost an asset that should exist. Assets the
game does not ship for every entity (honor rank/word/medal overlays, an event's story background) are probed
with `get_optional_asset_image_ref`, so a complete picture is not flagged; a real miss is still counted.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from types import SimpleNamespace

from PIL import Image
import pytest

from src.assets.mirror import AssetMirror, MirrorStats, NullMirror, set_asset_mirror
from src.assets.version import StaticVersion
from src.core import missing_asset_telemetry as telemetry
from src.sekai.base import utils
from src.sekai.event import drawer as event_drawer
from src.sekai.event.model import EventAssets
from src.sekai.honor import drawer as honor_drawer
from src.sekai.honor.model import HonorRequest
from src.settings import AssetMirrorSettings
from src.storage.protocols import StorageUnavailable
from tests.storage_fakes import FakeObjectStore

LOGICAL = "asset/jp-assets/startapp/honor/rank_main_99/rank.png"


@pytest.fixture(autouse=True)
def _isolated_counters():
    telemetry.reset_missing_asset_stats()
    utils.clear_resolved_path_cache()
    utils._load_asset_image_ref_cached.cache_clear()
    set_asset_mirror(NullMirror())
    yield
    set_asset_mirror(None)
    telemetry.reset_missing_asset_stats()
    utils.clear_resolved_path_cache()


def _total() -> int:
    return telemetry.get_missing_asset_stats()["total"]


def _nonzero() -> dict[str, int]:
    return {reason: n for reason, n in telemetry.get_missing_asset_stats()["by_reason"].items() if n}


def _png(path: Path, size=(6, 4)) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    Image.new("RGBA", size, (10, 20, 30, 255)).save(path)
    return path


def _in_request_scope(coro_factory):
    """Run a coroutine inside a request's missing-asset scope; return (result, scope count)."""

    async def run():
        token = utils.begin_missing_asset_scope()
        try:
            return await coro_factory(), utils.current_missing_asset_count()
        finally:
            utils.end_missing_asset_scope(token)

    return asyncio.run(run())


# ------------------------------------------------------------------ the probe


def test_optional_probe_returns_the_ref_when_present(tmp_path) -> None:
    _png(tmp_path / "rank.png")
    ref = asyncio.run(utils.get_optional_asset_image_ref(tmp_path, "rank.png"))
    assert isinstance(ref, utils.AssetImageRef)
    assert ref.size == (6, 4)
    assert _total() == 0


@pytest.mark.parametrize("path", [None, "", "  ", "absent.png", ["absent_a.png", "absent_b.png"]])
def test_optional_probe_absent_asset_is_not_counted(tmp_path, path) -> None:
    result, scope_count = _in_request_scope(lambda: utils.get_optional_asset_image_ref(tmp_path, path))
    assert result is None
    assert scope_count == 0
    assert _total() == 0


def test_optional_probe_mirror_not_found_is_not_counted(tmp_path) -> None:
    _mirror(tmp_path, FakeObjectStore(bucket="pjsk-assets"))
    result, scope_count = _in_request_scope(lambda: utils.get_optional_asset_image_ref(tmp_path, LOGICAL))
    assert result is None
    assert scope_count == 0


def test_optional_probe_still_counts_a_mirror_outage(tmp_path) -> None:
    # The asset may exist: leaving it out because the mirror is down is a real degradation.
    store = FakeObjectStore(bucket="pjsk-assets", fail=StorageUnavailable("down"))
    _mirror(tmp_path, store, breaker_failures=1, breaker_open_seconds=3600)

    async def twice():
        first = await utils.get_optional_asset_image_ref(tmp_path, LOGICAL)
        second = await utils.get_optional_asset_image_ref(tmp_path, LOGICAL)
        return first, second

    result, scope_count = _in_request_scope(twice)
    assert result == (None, None)
    assert scope_count == 2
    assert _nonzero() == {"mirror_fetch_error": 1, "mirror_breaker_open": 1}


def test_optional_probe_still_counts_an_unreadable_file(tmp_path) -> None:
    (tmp_path / "broken.png").write_bytes(b"not an image")
    result, scope_count = _in_request_scope(lambda: utils.get_optional_asset_image_ref(tmp_path, "broken.png"))
    assert result is None
    assert scope_count == 1


def test_optional_probe_rejects_traversal(tmp_path) -> None:
    with pytest.raises(ValueError, match="越界"):
        asyncio.run(utils.get_optional_asset_image_ref(tmp_path, "../x.png"))
    assert _total() == 0


def test_raise_mode_still_counts_a_missing_asset(tmp_path) -> None:
    async def load():
        try:
            await utils.get_asset_image_ref(tmp_path, "absent.png", on_missing="raise")
        except FileNotFoundError:
            return "raised"

    assert _in_request_scope(load) == ("raised", 1)


def _mirror(tmp_path: Path, store: FakeObjectStore, **overrides) -> AssetMirror:
    mirror = AssetMirror(
        base_dir=tmp_path,
        settings=AssetMirrorSettings(**overrides),
        store_factory=lambda region: store,
        version_source=StaticVersion("v0"),
        stats=MirrorStats(),
    )
    set_asset_mirror(mirror)
    return mirror


# ------------------------------------------------------------------ honor


def _main_honor(**paths) -> HonorRequest:
    return HonorRequest(honor_type="normal", group_type="event", is_main_honor=True, honor_rarity="high", **paths)


def test_honor_missing_optional_overlays_are_not_counted(tmp_path, monkeypatch) -> None:
    """The live bug: official bundles ship no rank_main/rank_sub image for many honors, and every such
    profile was answered as a placeholder render although the badge is complete without it."""
    monkeypatch.setattr(honor_drawer, "ASSETS_BASE_DIR", tmp_path)
    _png(tmp_path / "honor.png")
    request = _main_honor(
        honor_img_path="honor.png",
        rank_img_path="honor/rank_main_12/rank.png",
        word_img_path="honor_word/w1.png",
        medal_img_path="honor_medal/m1.png",
    )

    images, scope_count = _in_request_scope(lambda: honor_drawer.load_honor_images(request))

    assert isinstance(images["honor_img"], utils.AssetImageRef)
    assert images["rank_img"] is None
    assert images["word_img"] is None
    assert images["medal_img"] is None
    assert scope_count == 0
    assert _total() == 0


def test_honor_present_optional_overlay_is_loaded(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(honor_drawer, "ASSETS_BASE_DIR", tmp_path)
    _png(tmp_path / "honor.png")
    _png(tmp_path / "rank.png", size=(3, 3))
    images, scope_count = _in_request_scope(
        lambda: honor_drawer.load_honor_images(_main_honor(honor_img_path="honor.png", rank_img_path="rank.png"))
    )
    assert images["rank_img"].size == (3, 3)
    assert scope_count == 0


def test_honor_missing_required_asset_is_still_counted(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(honor_drawer, "ASSETS_BASE_DIR", tmp_path)

    async def load():
        try:
            await honor_drawer.load_honor_images(_main_honor(honor_img_path="absent.png"))
        except FileNotFoundError:
            return "raised"

    assert _in_request_scope(load) == ("raised", 1)


# ------------------------------------------------------------------ event story background


def _event_request(*, story_bg: str = "event_story/ev186/screen_image/story_bg.png", bg: str = "event/ev186/bg.png"):
    return SimpleNamespace(
        event_assets=EventAssets(
            event_bg_path=bg,
            event_logo_path="event/ev186/logo.png",
            event_story_bg_path=story_bg,
            event_attr_image_path="",
            event_ban_chara_img="",
        )
    )


def test_event_story_bg_is_used_when_present(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(event_drawer, "ASSETS_BASE_DIR", tmp_path)
    _png(tmp_path / "event_story/ev186/screen_image/story_bg.png", size=(16, 9))
    _png(tmp_path / "event/ev186/bg.png", size=(8, 8))
    bg, scope_count = _in_request_scope(lambda: event_drawer._load_event_detail_background(_event_request(), True))
    assert bg.path.name == "story_bg.png"
    assert scope_count == 0


def test_event_missing_story_bg_falls_back_to_event_bg_uncounted(tmp_path, monkeypatch) -> None:
    """JP event 186 (a test event) has no story_bg upstream, while the caller sends the path for every
    non-WL event: the page uses the event background and is not a placeholder render."""
    monkeypatch.setattr(event_drawer, "ASSETS_BASE_DIR", tmp_path)
    _png(tmp_path / "event/ev186/bg.png", size=(8, 8))
    bg, scope_count = _in_request_scope(lambda: event_drawer._load_event_detail_background(_event_request(), True))
    assert isinstance(bg, utils.AssetImageRef)
    assert bg.path.name == "bg.png"
    assert scope_count == 0
    assert _total() == 0


def test_event_empty_story_bg_falls_back_to_event_bg(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(event_drawer, "ASSETS_BASE_DIR", tmp_path)
    _png(tmp_path / "event/ev186/bg.png", size=(8, 8))
    bg, scope_count = _in_request_scope(
        lambda: event_drawer._load_event_detail_background(_event_request(story_bg=""), True)
    )
    assert bg.path.name == "bg.png"
    assert scope_count == 0


def test_event_missing_story_bg_and_event_bg_is_counted_once(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(event_drawer, "ASSETS_BASE_DIR", tmp_path)
    bg, scope_count = _in_request_scope(lambda: event_drawer._load_event_detail_background(_event_request(), True))
    assert isinstance(bg, utils.MissingImageRef)
    assert scope_count == 1


def test_world_link_event_uses_event_bg_without_probing_story_bg(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(event_drawer, "ASSETS_BASE_DIR", tmp_path)
    _png(tmp_path / "event_story/ev186/screen_image/story_bg.png", size=(16, 9))
    _png(tmp_path / "event/ev186/bg.png", size=(8, 8))
    bg, _ = _in_request_scope(lambda: event_drawer._load_event_detail_background(_event_request(), False))
    assert bg.path.name == "bg.png"


def test_event_detail_tasks_route_bg_through_the_fallback(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr(event_drawer, "ASSETS_BASE_DIR", tmp_path)
    _png(tmp_path / "event/ev186/bg.png", size=(8, 8))
    _png(tmp_path / "event/ev186/logo.png", size=(4, 2))
    request = _event_request()

    async def load():
        return await event_drawer._load_event_detail_images(request, [], True)

    images, scope_count = _in_request_scope(load)
    assert images["bg"].path.name == "bg.png"
    assert scope_count == 0
