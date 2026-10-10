"""Incomplete pixels and renderer epoch mismatches never enter the persistent cache."""

from dataclasses import replace

import pytest

from src.artifact.directive import DirectiveError, parse_render_cache_directive
from src.core import cache_identity, health, utils
from src.core.missing_asset_telemetry import MISSING_LOCAL_NOT_FOUND, record_missing_asset
from tests.test_artifact_directive import _headers as directive_headers
from tests.test_artifact_exit import _directive, _drive, _exit, _headers, _payload


def test_optional_generations_are_strict_digests():
    headers = directive_headers()
    headers.update({"x-haruki-asset-revision": "a" * 64, "x-haruki-renderer-epoch": "b" * 64})
    directive = parse_render_cache_directive(headers, ttl_max=10000)
    assert directive.asset_revision == "a" * 64
    assert directive.renderer_epoch == "b" * 64
    for name in ("x-haruki-asset-revision", "x-haruki-renderer-epoch"):
        broken = dict(headers)
        broken[name] = "../unsafe"
        with pytest.raises(DirectiveError):
            parse_render_cache_directive(broken, ttl_max=10000)


@pytest.mark.parametrize("reason", ["worker_missing", "parent_missing", "epoch_mismatch", "unknown_epoch"])
def test_unhealthy_or_wrong_generation_image_does_not_reach_artifacts(monkeypatch, reason):
    monkeypatch.setattr(utils, "get_artifact_runtime", lambda: pytest.fail("must not call artifact storage"))
    monkeypatch.setattr(utils, "renderer_epoch", lambda: None if reason == "unknown_epoch" else "a" * 64)
    payload = _payload(missing_asset_count=1 if reason == "worker_missing" else 0)
    directive = _directive()
    if reason == "parent_missing":
        monkeypatch.setattr(utils, "current_missing_asset_count", lambda: 1)
    if reason in {"epoch_mismatch", "unknown_epoch"}:
        directive = replace(directive, renderer_epoch="b" * 64)
    response = _exit(payload, directive)
    assert _headers(_drive(response))["x-haruki-cache-store"] == "0"
    assert response.body == payload.image_bytes


def test_heavy_payload_carries_missing_assets_to_parent():
    from src.core import debug
    from src.core.heavy_render_pool import _stamp_skia_backend

    tokens = debug.push_request_context("test", "/deck", "POST")
    try:
        record_missing_asset(MISSING_LOCAL_NOT_FOUND)
        payload = _stamp_skia_backend(_payload())
        assert payload.missing_asset_count == 1
    finally:
        debug.pop_request_context(tokens)
    assert utils.current_missing_asset_count() == 0


def test_identity_endpoint_reports_known_and_unavailable(monkeypatch):
    monkeypatch.setattr(health, "renderer_epoch", lambda: "a" * 64)
    assert health.cache_identity() == {"version": 1, "renderer_epoch": "a" * 64}
    monkeypatch.setattr(health, "renderer_epoch", lambda: None)
    assert health.cache_identity().status_code == 503


def test_renderer_epoch_changes_with_native_binary(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from src.sekai.base import utils as render_utils

    binary = tmp_path / "renderer.so"
    binary.write_bytes(b"native-v1")
    monkeypatch.setattr(cache_identity.importlib.util, "find_spec", lambda name: SimpleNamespace(origin=str(binary)))
    monkeypatch.setattr(render_utils, "renderer_code_fingerprint", lambda: "python-v1")
    cache_identity.renderer_epoch.cache_clear()
    try:
        first = cache_identity.renderer_epoch()
        assert len(first) == 64
        binary.write_bytes(b"native-v2")
        cache_identity.renderer_epoch.cache_clear()
        assert cache_identity.renderer_epoch() != first
        monkeypatch.setattr(render_utils, "renderer_code_fingerprint", lambda: "unknown")
        cache_identity.renderer_epoch.cache_clear()
        assert cache_identity.renderer_epoch() is None
    finally:
        cache_identity.renderer_epoch.cache_clear()


def test_incomplete_page_and_fragment_do_not_become_clean_warm_hits():
    from src.core import debug
    from src.sekai.skia_renderer.payload_cache import _SkiaPayloadCache

    cache = _SkiaPayloadCache(4, 4096, 60)
    tokens = debug.push_request_context("test", "/honor", "POST")
    try:
        record_missing_asset(MISSING_LOCAL_NOT_FOUND)
        cache.set("fragment", object(), 10)
        cache.set("page", _payload(), 100)
    finally:
        debug.pop_request_context(tokens)
    assert cache.get("fragment") is None
    assert cache.get("page") is None
    cache.set("worker_page", _payload(missing_asset_count=1), 100)
    assert cache.get("worker_page") is None
    complete = _payload()
    cache.set("complete", complete, 100)
    assert cache.get("complete") is complete


@pytest.mark.parametrize("asynchronous", [False, True])
@pytest.mark.parametrize("worker", [False, True])
def test_missing_image_disables_cache_even_without_directive(monkeypatch, asynchronous, worker):
    payload = _payload(missing_asset_count=int(worker))
    monkeypatch.setattr(utils, "current_missing_asset_count", lambda: int(not worker))
    response = _exit(payload, None) if asynchronous else utils.encoded_image_payload_to_bytes_response(payload)
    assert response.headers["x-haruki-cache-store"] == "0"
    assert response.body == payload.image_bytes


def test_epoch_excludes_node_tuning_but_tracks_fonts_templates_and_pixels(monkeypatch, tmp_path):
    from types import SimpleNamespace

    from src.sekai.base import utils as render_utils
    from src.settings import settings

    binary = tmp_path / "renderer.so"
    binary.write_bytes(b"native")
    monkeypatch.setattr(cache_identity.importlib.util, "find_spec", lambda name: SimpleNamespace(origin=str(binary)))
    monkeypatch.setattr(render_utils, "renderer_code_fingerprint", lambda: "python-v1")
    font_dir = tmp_path / "fonts"
    font_dir.mkdir()
    font = font_dir / (settings.font.default + ".otf")
    font.write_bytes(b"font-v1")
    template_dir = tmp_path / "templates"
    template_dir.mkdir()
    template = template_dir / "header.png"
    template.write_bytes(b"template-v1")
    monkeypatch.setattr(settings.font, "dir", font_dir)
    monkeypatch.setattr(settings.assets, "base_dir", tmp_path)
    monkeypatch.setattr(settings.assets, "result_asset_path", "templates")

    def identity():
        cache_identity.renderer_epoch.cache_clear()
        return cache_identity.renderer_epoch()

    try:
        initial = identity()
        monkeypatch.setattr(settings.drawing, "thread_pool_size", 77)
        monkeypatch.setattr(settings.drawing, "image_cache_size", 999)
        monkeypatch.setenv("HARUKI_SKIA_RASTER_CACHE_MB", "999")
        assert identity() == initial
        # Mount location has no effect when the selected font contents are identical.
        moved = tmp_path / "relocated"
        font_dir.rename(moved)
        monkeypatch.setattr(settings.font, "dir", moved)
        assert identity() == initial
        (moved / font.name).write_bytes(b"font-v2")
        font_changed = identity()
        assert font_changed != initial
        template.write_bytes(b"template-v2")
        template_changed = identity()
        assert template_changed != font_changed
        monkeypatch.setattr(settings.drawing, "jpg_quality", 42)
        quality_changed = identity()
        assert quality_changed != template_changed
        monkeypatch.setattr(settings.drawing, "jpg_subsampling", "420")
        assert identity() != quality_changed
    finally:
        cache_identity.renderer_epoch.cache_clear()


def test_missing_native_font_disables_response_and_page_cache(monkeypatch):
    from src.sekai.skia_renderer.payload_cache import _SkiaPayloadCache

    payload = _payload(native_metrics={"font_fallbacks": 1})
    for directive in (None, _directive()):
        response = _exit(payload, directive)
        assert response.headers["x-haruki-cache-store"] == "0"
        assert response.body == payload.image_bytes
    cache = _SkiaPayloadCache(4, 4096, 60)
    cache.set("font-fallback", payload, 100)
    assert cache.get("font-fallback") is None
