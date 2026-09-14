"""The async route exit (plan §8.6): four branches, `X-Haruki-Node` on every one, one body message each."""

from __future__ import annotations

import asyncio
import contextvars
import json
import logging
from typing import Any

import pytest

from src.artifact import stats as artifact_stats_mod
from src.artifact.directive import RenderCacheDirective
from src.artifact.runtime import ArtifactRuntime, set_artifact_runtime
from src.core import debug, utils as core_utils
from src.core.image_payload import EncodedImagePayload
from src.core.utils import encoded_image_payload_to_bytes_response, encoded_image_payload_to_response
from src.settings import settings
from tests.storage_fakes import FakeObjectStore, FakeRenderIndex, build_test_runtime

PNG_LIKE = b"\x89PNG\r\n\x1a\n" + bytes(range(256)) * 8
KEY = "0123456789abcdef0123"


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setattr(settings.storage, "node_name", "cn-exit")
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


def _directive(*, store: bool = True) -> RenderCacheDirective:
    return RenderCacheDirective(
        cache_key=KEY,
        key_version=3,
        ttl_seconds=600,
        store=store,
        group="pjsk",
        api_path="api/pjsk/honor",
        user_id="public",
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


def _exit(payload: EncodedImagePayload, directive: RenderCacheDirective | None):
    def run():
        tokens = debug.push_request_context("rid-exit", "/api/pjsk/honor", "POST")
        token = debug._render_directive_var.set(directive)
        try:
            return asyncio.run(encoded_image_payload_to_response(payload))
        finally:
            debug._render_directive_var.reset(token)
            debug.pop_request_context(tokens)

    return contextvars.copy_context().run(run)


def _headers(sent: list[dict]) -> dict[str, str]:
    start = next(m for m in sent if m["type"] == "http.response.start")
    return {k.decode().lower(): v.decode() for k, v in start["headers"]}


def _bodies(sent: list[dict]) -> list[dict]:
    return [m for m in sent if m["type"] == "http.response.body"]


def _response_lines(caplog: pytest.LogCaptureFixture) -> list[str]:
    return [r.getMessage() for r in caplog.records if r.getMessage().startswith("image.response")]


def test_no_directive_is_todays_bytes_response_plus_the_node_header(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="src.core.utils")
    baseline = _drive(encoded_image_payload_to_bytes_response(_payload()))
    sent = _drive(_exit(_payload(), None))

    assert len(_bodies(sent)) == 1
    assert _bodies(sent)[0]["body"] == _bodies(baseline)[0]["body"] == PNG_LIKE
    headers = _headers(sent)
    assert headers.pop("x-haruki-node") == "cn-exit"
    assert headers == _headers(baseline)
    assert headers["content-length"] == str(len(PNG_LIKE))
    assert next(m for m in sent if m["type"] == "http.response.start")["status"] == 200
    lines = _response_lines(caplog)
    assert len(lines) == 2  # one for the baseline call, one for the exit
    assert " artifact=0 missing_assets=0 " in lines[-1]
    snapshot = artifact_stats_mod.get_artifact_stats()
    assert snapshot["bytes_no_directive"] == 1
    assert snapshot["requests_with_directive"] == 0


def test_store_zero_returns_bytes_and_touches_nothing(caplog: pytest.LogCaptureFixture) -> None:
    store = FakeObjectStore(bucket="image-cache")
    index = FakeRenderIndex()
    set_artifact_runtime(build_test_runtime(store=store, index=index))
    caplog.set_level(logging.INFO, logger="src.core.utils")

    sent = _drive(_exit(_payload(), _directive(store=False)))

    headers = _headers(sent)
    assert headers["x-haruki-cache-store"] == "0"
    assert headers["x-haruki-node"] == "cn-exit"
    assert "x-haruki-artifact" not in headers
    assert "x-haruki-artifact-degraded" not in headers
    assert headers["content-type"] == "image/png"
    assert _bodies(sent)[0]["body"] == PNG_LIKE
    assert store.writes == []
    assert store.ops == 0
    assert index.calls == []
    lines = _response_lines(caplog)
    assert len(lines) == 1
    assert " artifact=store0 " in lines[0]
    snapshot = artifact_stats_mod.get_artifact_stats()
    assert snapshot["store_skipped"] == 1
    assert snapshot["bytes_no_directive"] == 0


def test_disabled_runtime_degrades_to_bytes(caplog: pytest.LogCaptureFixture) -> None:
    set_artifact_runtime(ArtifactRuntime(reason="disabled"))
    caplog.set_level(logging.INFO, logger="src.core.utils")

    sent = _drive(_exit(_payload(), _directive()))

    headers = _headers(sent)
    assert headers["x-haruki-artifact-degraded"] == "1"
    assert headers["x-haruki-node"] == "cn-exit"
    assert "x-haruki-artifact" not in headers
    assert len(_bodies(sent)) == 1
    assert _bodies(sent)[0]["body"] == PNG_LIKE
    lines = _response_lines(caplog)
    assert len(lines) == 1
    assert " artifact=degraded reason=disabled " in lines[0]
    assert artifact_stats_mod.get_artifact_stats()["degraded"]["disabled"] == 1


def test_upload_failure_degrades_to_bytes() -> None:
    store = FakeObjectStore(bucket="image-cache", fail=RuntimeError("boom"))
    set_artifact_runtime(build_test_runtime(store=store, index=FakeRenderIndex()))

    sent = _drive(_exit(_payload(), _directive()))

    headers = _headers(sent)
    assert headers["x-haruki-artifact-degraded"] == "1"
    assert headers["x-haruki-node"] == "cn-exit"
    assert _bodies(sent)[0]["body"] == PNG_LIKE


def test_artifact_ref_is_one_json_body_with_content_length(caplog: pytest.LogCaptureFixture) -> None:
    store = FakeObjectStore(bucket="image-cache")
    index = FakeRenderIndex()
    set_artifact_runtime(build_test_runtime(store=store, index=index, node_name="cn-exit"))
    caplog.set_level(logging.INFO, logger="src.core.utils")

    sent = _drive(_exit(_payload(), _directive()))

    bodies = _bodies(sent)
    assert len(bodies) == 1
    headers = _headers(sent)
    assert headers["x-haruki-artifact"] == "1"
    assert headers["x-haruki-node"] == "cn-exit"
    assert headers["content-type"] == "application/json"
    assert headers["content-length"] == str(len(bodies[0]["body"]))
    assert "x-haruki-artifact-degraded" not in headers
    document = json.loads(bodies[0]["body"])
    assert document["kind"] == "artifact_ref"
    assert document["node_name"] == "cn-exit"
    assert document["object_key"].startswith("pjsk/api/pjsk/honor/")
    assert store.writes == [(document["object_key"], len(PNG_LIKE), "image/png")]
    assert [name for name, _ in index.calls] == ["lookup_content", "record"]
    lines = _response_lines(caplog)
    assert len(lines) == 1
    assert f" artifact=1 hash={document['hash']} reused=0 index_written=1 upload=" in lines[0]
    assert " missing_assets=0 " in lines[0]
    assert artifact_stats_mod.get_artifact_stats()["published"] == 1


def test_bytes_response_accepts_extra_headers() -> None:
    sent = _drive(encoded_image_payload_to_bytes_response(_payload(), extra_headers={"X-Extra": "1"}))
    headers = _headers(sent)
    assert headers["x-extra"] == "1"
    assert "x-haruki-node" not in headers


def test_legacy_pil_exit_is_gone() -> None:
    assert not hasattr(core_utils, "image_to_response")
    assert callable(core_utils._encode_image)


def test_custom_profile_exit_runs_outside_the_render_slot(monkeypatch: pytest.MonkeyPatch) -> None:
    """The exit may upload; it must not hold `_custom_profile_render_slots` while it does (plan §8.6)."""
    from types import SimpleNamespace

    from src.core.pjsk import profile as profile_route

    payload = _payload()
    recorded: list[int] = []
    slot_locked_during_exit: list[bool] = []

    class _Attempt:
        error = None

        def __init__(self) -> None:
            self.payload = payload

        def tag_backend(self) -> None:
            pass

        def record(self, status: int) -> None:
            recorded.append(status)

        def reject(self) -> None:  # pragma: no cover - not reached
            pytest.fail("unexpected reject")

    async def attempt_render(_request: Any) -> _Attempt:
        return _Attempt()

    async def exit_stub(value: EncodedImagePayload) -> Any:
        assert value is payload
        slot_locked_during_exit.append(profile_route._custom_profile_render_slots.locked())
        return SimpleNamespace(status_code=200)

    monkeypatch.setattr(profile_route, "_custom_profile_render_slots", asyncio.Semaphore(1))
    monkeypatch.setattr(profile_route, "validate_custom_profile_card", lambda *_a, **_k: None)
    monkeypatch.setattr(profile_route, "try_render_custom_profile_card_attempt", attempt_render)
    monkeypatch.setattr(profile_route, "encoded_image_payload_to_response", exit_stub)

    response = asyncio.run(profile_route.custom_profile_card(SimpleNamespace(card={})))

    assert response.status_code == 200
    assert slot_locked_during_exit == [False]
    assert recorded == [200]
