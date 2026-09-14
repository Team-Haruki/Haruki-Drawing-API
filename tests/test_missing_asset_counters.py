"""Missing-asset counters (plan §5.3): every occurrence counts, request scope, `/cache/stats` shape."""

from __future__ import annotations

import asyncio
import contextvars
import io
import logging
from pathlib import Path

import httpx
from PIL import Image
import pytest

from src.assets.mirror import AssetMirror, MirrorStats, NullMirror, set_asset_mirror
from src.assets.version import StaticVersion
from src.core import debug, missing_asset_telemetry as telemetry
from src.core.image_payload import EncodedImagePayload
from src.core.utils import encoded_image_payload_to_response
from src.sekai.base import utils
from src.settings import AssetMirrorSettings
from src.storage.protocols import StorageUnavailable
from tests.storage_fakes import FakeObjectStore

LOGICAL = "asset/jp-assets/startapp/music/jacket/j001.png"
OBJECT_KEY = "jp-assets/startapp/music/jacket/j001.png"

EXPECTED_REASONS = (
    "empty_path",
    "local_not_found",
    "mirror_not_found",
    "mirror_fetch_error",
    "mirror_breaker_open",
    "candidates_exhausted",
    "birthday_fallback",
    "vanished",
)


@pytest.fixture(autouse=True)
def _isolated_counters():
    telemetry.reset_missing_asset_stats()
    with utils._missing_placeholder_lock:
        utils._missing_placeholder_logged.clear()
    utils.clear_resolved_path_cache()
    utils._load_asset_image_ref_cached.cache_clear()
    set_asset_mirror(NullMirror())
    yield
    set_asset_mirror(None)
    telemetry.reset_missing_asset_stats()
    utils.clear_resolved_path_cache()


def _by_reason() -> dict[str, int]:
    return telemetry.get_missing_asset_stats()["by_reason"]


def _nonzero() -> dict[str, int]:
    return {reason: n for reason, n in _by_reason().items() if n}


def _png(size=(6, 5)) -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", size, (5, 5, 5, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


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


def test_the_reason_vocabulary_is_exactly_the_eight_plan_reasons() -> None:
    assert telemetry.MISSING_ASSET_REASONS == EXPECTED_REASONS
    assert utils.MISSING_ASSET_REASONS == EXPECTED_REASONS
    stats = telemetry.get_missing_asset_stats()
    assert stats == {"total": 0, "by_reason": dict.fromkeys(EXPECTED_REASONS, 0)}


@pytest.mark.parametrize("reason", EXPECTED_REASONS)
def test_each_reason_counts_every_occurrence(reason: str) -> None:
    for _ in range(3):
        utils.record_missing_asset(reason)

    assert _nonzero() == {reason: 3}
    assert telemetry.get_missing_asset_stats()["total"] == 3


def test_unknown_reason_is_ignored() -> None:
    utils.record_missing_asset("nonsense")
    assert telemetry.get_missing_asset_stats()["total"] == 0


def test_log_once_still_warns_once_but_counts_every_call(caplog) -> None:
    caplog.set_level(logging.WARNING, logger=utils.logger.name)
    exc = FileNotFoundError("gone")
    for _ in range(4):
        utils._log_missing_image_once("a.png", exc)

    assert len([r for r in caplog.records if "a.png" in r.getMessage()]) == 1
    assert _nonzero() == {"local_not_found": 4}


def test_placeholder_paths_count_empty_and_local_misses(tmp_path) -> None:
    asyncio.run(utils.get_img_from_path(tmp_path, ""))
    asyncio.run(utils.get_asset_image_ref(tmp_path, None))
    asyncio.run(utils.get_img_resized(tmp_path, " ", 4, 4))
    asyncio.run(utils.get_asset_image_refs(tmp_path, ["", "missing.png"]))
    asyncio.run(utils.get_img_from_path(tmp_path, "missing.png"))
    asyncio.run(utils.get_asset_image_ref(tmp_path, "missing.png"))
    asyncio.run(utils.get_img_resized(tmp_path, "missing.png", 4, 4))

    assert _nonzero() == {"empty_path": 4, "local_not_found": 4}


def test_raise_mode_counts_before_propagating(tmp_path) -> None:
    for call in (
        lambda: utils.get_img_from_path(tmp_path, "", on_missing="raise"),
        lambda: utils.get_asset_image_ref(tmp_path, "", on_missing="raise"),
        lambda: utils.get_img_resized(tmp_path, "", 4, 4, on_missing="raise"),
    ):
        with pytest.raises(ValueError, match="不能为空"):
            asyncio.run(call())
    for call in (
        lambda: utils.get_img_from_path(tmp_path, "gone.png", on_missing="raise"),
        lambda: utils.get_asset_image_ref(tmp_path, "gone.png", on_missing="raise"),
        lambda: utils.get_img_resized(tmp_path, "gone.png", 4, 4, on_missing="raise"),
    ):
        with pytest.raises(FileNotFoundError):
            asyncio.run(call())

    assert _nonzero() == {"empty_path": 3, "local_not_found": 3}


def test_traversal_is_not_a_missing_asset(tmp_path) -> None:
    with pytest.raises(ValueError, match="越界"):
        asyncio.run(utils.get_asset_image_ref(tmp_path, "../x.png", on_missing="raise"))
    assert telemetry.get_missing_asset_stats()["total"] == 0


def test_vanished_file_counts_vanished(tmp_path) -> None:
    path = tmp_path / "v.png"
    Image.new("RGBA", (3, 3)).save(path)
    ref = asyncio.run(utils.get_asset_image_ref(tmp_path, "v.png", on_missing="raise"))
    path.unlink()

    utils.resolve_image_source_sync(ref)
    utils.resolve_image_source_sync(ref)

    assert _nonzero() == {"vanished": 2}


def test_birthday_fallback_counts_every_hit(tmp_path) -> None:
    older = tmp_path / "static_images" / "mysekai" / "birthday" / "miku_2024" / "icon" / "item.png"
    older.parent.mkdir(parents=True)
    Image.new("RGBA", (3, 3)).save(older)
    requested = "static_images/mysekai/birthday/miku_2026/icon/item.png"

    for _ in range(2):
        assert utils._resolve_and_stat(tmp_path, requested)[0] == older

    assert _nonzero() == {"birthday_fallback": 2}


def test_mirror_not_found_is_counted_with_its_reason(tmp_path) -> None:
    _mirror(tmp_path, FakeObjectStore(bucket="pjsk-assets"))

    asyncio.run(utils.get_asset_image_ref(tmp_path, LOGICAL))
    asyncio.run(utils.get_asset_image_ref(tmp_path, LOGICAL))  # negative memo: still a miss, still counted

    assert _nonzero() == {"mirror_not_found": 2}


def test_mirror_fetch_error_and_breaker_open_are_counted(tmp_path) -> None:
    store = FakeObjectStore(bucket="pjsk-assets", fail=StorageUnavailable("down"))
    _mirror(tmp_path, store, breaker_failures=1, breaker_open_seconds=3600)

    asyncio.run(utils.get_asset_image_ref(tmp_path, LOGICAL))
    asyncio.run(utils.get_asset_image_ref(tmp_path, LOGICAL))

    assert _nonzero() == {"mirror_fetch_error": 1, "mirror_breaker_open": 1}


def test_mirror_fetch_success_counts_nothing(tmp_path) -> None:
    store = FakeObjectStore({OBJECT_KEY: _png()}, bucket="pjsk-assets")
    _mirror(tmp_path, store)

    ref = asyncio.run(utils.get_asset_image_ref(tmp_path, LOGICAL, on_missing="raise"))

    assert isinstance(ref, utils.AssetImageRef)
    assert telemetry.get_missing_asset_stats()["total"] == 0


def test_resolve_logical_file_miss_carries_the_mirror_reason(tmp_path) -> None:
    _mirror(tmp_path, FakeObjectStore(bucket="pjsk-assets"))

    with pytest.raises(FileNotFoundError) as excinfo:
        utils.resolve_logical_file(tmp_path, "asset/jp-assets/startapp/music/x.txt")

    assert utils._missing_reason_of(excinfo.value) == "mirror_not_found"
    assert utils._missing_reason_of("empty-path") == "empty_path"
    assert utils._missing_reason_of("other") == "local_not_found"


def test_request_scope_round_trips_through_pool_threads(tmp_path) -> None:
    assert utils.current_missing_asset_count() == 0

    async def request() -> tuple[int, int]:
        token = utils.begin_missing_asset_scope()
        await utils.get_img_from_path(tmp_path, "a.png")
        await utils.get_asset_image_refs(tmp_path, ["b.png", "c.png"])
        seen = utils.current_missing_asset_count()
        return seen, utils.end_missing_asset_scope(token)

    assert asyncio.run(request()) == (3, 3)
    assert utils.current_missing_asset_count() == 0
    assert telemetry.get_missing_asset_stats()["total"] == 3


def test_nested_scopes_restore_the_parent() -> None:
    def run() -> tuple[int, int, int]:
        outer = telemetry.begin_missing_asset_scope()
        utils.record_missing_asset("vanished")
        inner = telemetry.begin_missing_asset_scope()
        utils.record_missing_asset("vanished")
        utils.record_missing_asset("vanished")
        inner_count = telemetry.end_missing_asset_scope(inner)
        after_inner = telemetry.current_missing_asset_count()
        return inner_count, after_inner, telemetry.end_missing_asset_scope(outer)

    assert contextvars.copy_context().run(run) == (2, 1, 1)


def test_push_and_pop_request_context_manage_the_scope() -> None:
    def run() -> tuple[int, int]:
        tokens = debug.push_request_context("rid", "/x", "POST")
        assert tokens.missing_assets is not None
        utils.record_missing_asset("empty_path")
        inside = telemetry.current_missing_asset_count()
        debug.pop_request_context(tokens)
        return inside, telemetry.current_missing_asset_count()

    assert contextvars.copy_context().run(run) == (1, 0)


def test_image_response_line_reports_missing_assets(caplog) -> None:
    payload = EncodedImagePayload(
        image_bytes=b"\x89PNG",
        media_type="image/png",
        filename="x.png",
        image_width=1,
        image_height=1,
        image_mode="RGBA",
        encode_elapsed=0.0,
    )

    def run() -> None:
        tokens = debug.push_request_context("rid", "/x", "POST")
        try:
            utils.record_missing_asset("local_not_found")
            utils.record_missing_asset("vanished")
            encoded_image_payload_to_response(payload)
        finally:
            debug.pop_request_context(tokens)

    caplog.set_level(logging.INFO, logger="src.core.utils")
    contextvars.copy_context().run(run)

    lines = [r.getMessage() for r in caplog.records if r.getMessage().startswith("image.response")]
    assert len(lines) == 1
    assert " missing_assets=2 " in lines[0]


def test_cache_stats_endpoint_exposes_missing_assets() -> None:
    from src.core.main import app

    utils.record_missing_asset("candidates_exhausted")

    async def fetch() -> httpx.Response:
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
            return await client.get("/cache/stats")

    response = asyncio.run(fetch())

    assert response.status_code == 200
    caches = response.json()["caches"]
    assert caches["missing_assets"]["total"] >= 1
    assert set(caches["missing_assets"]["by_reason"]) == set(EXPECTED_REASONS)
    assert caches["missing_assets"]["by_reason"]["candidates_exhausted"] >= 1
    assert caches["asset_mirror"]["source"] == "local"
