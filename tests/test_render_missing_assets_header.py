"""`X-Haruki-Render-Missing-Assets`: every image exit reports the "?" placeholders it drew.

The caller (Cloud) keys its render cache on the request, which does not change when a missing asset finally
arrives, so it needs the count to give such a render a short, non-sliding life instead of the rule TTL.
"""

from __future__ import annotations

import asyncio
import contextvars
import io
import json
from typing import Any

from PIL import Image
import pytest

from src.artifact import stats as artifact_stats_mod
from src.artifact.directive import RenderCacheDirective
from src.artifact.runtime import set_artifact_runtime
from src.core import debug
from src.core.image_payload import EncodedImagePayload
from src.core.missing_asset_telemetry import MISSING_LOCAL_NOT_FOUND, record_missing_asset
from src.core.utils import (
    MISSING_ASSETS_HEADER,
    encoded_image_payload_to_bytes_response,
    encoded_image_payload_to_response,
)
from src.sekai.base import utils as base_utils
from src.settings import settings
from src.storage.protocols import StorageUnavailable
from tests.storage_fakes import FakeObjectStore, FakeRenderIndex, build_test_runtime

PNG_LIKE = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 8
KEY = "0123456789abcdef0123"
HEADER = MISSING_ASSETS_HEADER.lower()


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings.storage, "node_name", "cn-missing")
    artifact_stats_mod.reset_artifact_node_name()
    artifact_stats_mod.reset_artifact_stats()
    yield
    set_artifact_runtime(None)
    artifact_stats_mod.reset_artifact_node_name()
    artifact_stats_mod.reset_artifact_stats()


def _payload(**overrides: Any) -> EncodedImagePayload:
    values: dict[str, Any] = {
        "image_bytes": PNG_LIKE,
        "media_type": "image/png",
        "filename": "x.png",
        "image_width": 4,
        "image_height": 4,
        "image_mode": "RGBA",
        "encode_elapsed": 0.0,
    }
    values.update(overrides)
    return EncodedImagePayload(**values)


def _directive(*, store: bool = True, store_ref: bool = False) -> RenderCacheDirective:
    return RenderCacheDirective(
        cache_key=KEY,
        key_version=6,
        ttl_seconds=0,
        store=store,
        group="pjsk",
        api_path="api/pjsk/card/list",
        user_id="public",
        store_ref=store_ref,
    )


def _drive(response) -> list[dict]:
    sent: list[dict] = []

    async def send(message):
        sent.append(message)

    async def receive():
        return {"type": "http.disconnect"}

    scope = {"type": "http", "method": "POST", "path": "/x", "headers": []}
    asyncio.run(response(scope, receive, send))
    return sent


def _exit(payload: EncodedImagePayload, directive: RenderCacheDirective | None, *, recorded: int = 0) -> list[dict]:
    """Run the async route exit inside a request scope that recorded `recorded` missing assets."""

    def run():
        tokens = debug.push_request_context("rid-missing", "/api/pjsk/card/list", "POST")
        token = debug._render_directive_var.set(directive)
        try:
            for _ in range(recorded):
                record_missing_asset(MISSING_LOCAL_NOT_FOUND)
            return asyncio.run(encoded_image_payload_to_response(payload))
        finally:
            debug._render_directive_var.reset(token)
            debug.pop_request_context(tokens)

    return _drive(contextvars.copy_context().run(run))


def _headers(sent: list[dict]) -> dict[str, str]:
    start = next(m for m in sent if m["type"] == "http.response.start")
    return {k.decode().lower(): v.decode() for k, v in start["headers"]}


def _body(sent: list[dict]) -> bytes:
    return b"".join(m["body"] for m in sent if m["type"] == "http.response.body")


def test_a_complete_render_carries_no_header() -> None:
    assert HEADER not in _headers(_exit(_payload(), None))
    assert HEADER not in _headers(_drive(encoded_image_payload_to_bytes_response(_payload())))


def test_bytes_without_a_directive_report_the_request_scope_count() -> None:
    headers = _headers(_exit(_payload(), None, recorded=2))

    assert headers[HEADER] == "2"
    assert headers["x-haruki-cache-store"] == "0"
    assert headers["x-haruki-node"] == "cn-missing"


def test_heavy_worker_count_rides_on_the_payload() -> None:
    """The heavy pool's child process counts in its own scope; the parent only sees the payload field."""
    headers = _headers(_exit(_payload(missing_asset_count=3), None, recorded=1))

    assert headers[HEADER] == "4"


def test_synchronous_bytes_helper_reports_the_count() -> None:
    def run():
        tokens = debug.push_request_context("rid-sync", "/x", "POST")
        try:
            record_missing_asset(MISSING_LOCAL_NOT_FOUND)
            return encoded_image_payload_to_bytes_response(_payload(), extra_headers={"X-Extra": "1"})
        finally:
            debug.pop_request_context(tokens)

    headers = _headers(_drive(contextvars.copy_context().run(run)))

    assert headers[HEADER] == "1"
    assert headers["x-extra"] == "1"


def test_store_one_with_missing_assets_answers_store_zero_bytes_and_stores_nothing() -> None:
    store = FakeObjectStore(bucket="image-cache")
    index = FakeRenderIndex()
    set_artifact_runtime(build_test_runtime(store=store, index=index))

    sent = _exit(_payload(), _directive(), recorded=1)

    headers = _headers(sent)
    assert headers[HEADER] == "1"
    assert headers["x-haruki-cache-store"] == "0"
    assert headers["content-type"] == "image/png"
    assert _body(sent) == PNG_LIKE
    assert store.writes == []
    assert index.calls == []


def test_a_complete_artifact_ref_reports_zero_and_no_header() -> None:
    set_artifact_runtime(build_test_runtime(store=FakeObjectStore(bucket="image-cache"), index=FakeRenderIndex()))

    sent = _exit(_payload(), _directive())

    headers = _headers(sent)
    assert headers["x-haruki-artifact"] == "1"
    assert HEADER not in headers
    assert json.loads(_body(sent))["missing_assets"] == 0


def test_store_ref_ref_and_header_carry_the_count() -> None:
    store = FakeObjectStore(bucket="image-cache")
    set_artifact_runtime(build_test_runtime(store=store))

    sent = _exit(_payload(missing_asset_count=2), _directive(store=False, store_ref=True), recorded=1)

    headers = _headers(sent)
    assert headers["x-haruki-artifact-mode"] == "store-ref"
    assert headers["x-haruki-cache-store"] == "0"
    assert headers[HEADER] == "3"
    document = json.loads(_body(sent))
    assert document["kind"] == "artifact_ref"
    assert document["missing_assets"] == 3
    assert len(store.writes) == 1


def test_degraded_store_ref_bytes_still_carry_the_header() -> None:
    store = FakeObjectStore(bucket="image-cache", fail=StorageUnavailable("down"))
    set_artifact_runtime(build_test_runtime(store=store))

    sent = _exit(_payload(), _directive(store=False, store_ref=True), recorded=1)

    headers = _headers(sent)
    assert headers["x-haruki-artifact-degraded"] == "1"
    assert headers[HEADER] == "1"
    assert _body(sent) == PNG_LIKE


def test_composed_caches_refuse_a_fragment_drawn_with_a_missing_asset(monkeypatch: pytest.MonkeyPatch) -> None:
    memory: list[str] = []
    disk: list[str] = []
    monkeypatch.setattr(base_utils._composed_image_cache, "set", lambda key, image: memory.append(key))
    monkeypatch.setattr(base_utils._composed_image_disk_cache, "set", lambda ns, key, image: disk.append(key))
    image = Image.new("RGBA", (2, 2))

    def run(recorded: int) -> None:
        tokens = debug.push_request_context("rid-composed", "/x", "POST")
        try:
            for _ in range(recorded):
                record_missing_asset(MISSING_LOCAL_NOT_FOUND)
            base_utils.put_composed_image_cache(f"k{recorded}", image)
            base_utils.put_composed_image_disk_cache("ns", f"k{recorded}", image)
        finally:
            debug.pop_request_context(tokens)

    contextvars.copy_context().run(run, 1)
    contextvars.copy_context().run(run, 0)

    assert memory == ["k0"]
    assert disk == ["k0"]


def test_an_asset_that_vanished_before_lowering_is_counted(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from src.sekai.base.image_source import AssetImageRef
    from src.sekai.skia_renderer import placeholder as placeholder_mod
    from src.sekai.skia_renderer.ir_painter import IRPainter
    from src.settings import DEFAULT_BOLD_FONT, DEFAULT_FONT, FONT_DIR

    root = tmp_path / "assets"
    root.mkdir()
    asset = root / "thumb.png"
    Image.new("RGBA", (8, 6), (1, 2, 3, 255)).save(asset)
    stat = asset.stat()
    ref = AssetImageRef(path=asset, size=(8, 6), mode="RGBA", mtime_ns=stat.st_mtime_ns, file_size=stat.st_size)
    asset.unlink()
    placeholder = base_utils.get_encoded_image_ref(_png())
    monkeypatch.setattr(placeholder_mod, "render_placeholder", lambda _source: placeholder)

    def run() -> int:
        tokens = debug.push_request_context("rid-vanished", "/x", "POST")
        try:
            painter = IRPainter(
                (8, 6),
                assets_base_dir=str(root),
                font_dir=str(FONT_DIR),
                default_font=DEFAULT_FONT,
                bold_font=DEFAULT_BOLD_FONT,
            )
            assert painter._image_ref(ref).startswith("mem:")
            return base_utils.current_missing_asset_count()
        finally:
            debug.pop_request_context(tokens)

    assert contextvars.copy_context().run(run) == 1


def _png() -> bytes:
    buffer = io.BytesIO()
    Image.new("RGBA", (8, 6), (255, 255, 255, 255)).save(buffer, "PNG")
    return buffer.getvalue()
