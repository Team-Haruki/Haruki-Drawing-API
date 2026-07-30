"""Request-scoped Pillow-touch telemetry and native purity classification."""

from __future__ import annotations

import asyncio

from PIL import Image, ImageFont
import pytest

from src.core.debug import pop_request_context, push_request_context
from src.core.pillow_telemetry import (
    PILLOW_TOUCH_IMAGE_HEADER_PROBE,
    PILLOW_TOUCH_IRPAINTER_MEM_RASTER,
    PILLOW_TOUCH_IRPAINTER_PIL_IMAGE,
    PILLOW_TOUCH_PLACEHOLDER,
    PILLOW_TOUCH_TEXT_METRIC,
    record_pillow_touch,
)
from src.sekai.base.painter import get_text_size
from src.sekai.base.utils import get_asset_image_ref, run_in_pool
from src.sekai.skia_renderer.ir_painter import IRPainter
from src.sekai.skia_renderer.render_stats import (
    get_render_stats,
    record_render,
    record_worker_payload_backend,
    reset_render_stats,
)


@pytest.fixture(autouse=True)
def _clean_stats():
    reset_render_stats()
    yield
    reset_render_stats()


def _record_in_request(endpoint: str, touches: tuple[str, ...] = ()) -> None:
    tokens = push_request_context("rid", f"/api/{endpoint}", "POST")
    try:
        for reason in touches:
            record_pillow_touch(reason)
        record_render(endpoint, "skia")
    finally:
        pop_request_context(tokens)


def test_native_purity_distinguishes_pure_hybrid_and_unclassified():
    _record_in_request("pure")
    _record_in_request("hybrid", (PILLOW_TOUCH_TEXT_METRIC, PILLOW_TOUCH_TEXT_METRIC))
    record_render("unclassified", "cache_hit")

    stats = get_render_stats()
    assert stats["endpoints"]["pure"]["native_pure"] == 1
    assert stats["endpoints"]["hybrid"]["native_hybrid"] == 1
    assert stats["endpoints"]["unclassified"]["native_unclassified"] == 1
    assert stats["endpoints"]["hybrid"]["pillow_touch_reasons"] == {
        PILLOW_TOUCH_TEXT_METRIC: {"renders": 1, "touches": 2}
    }
    assert stats["totals"]["native_pure"] == 1
    assert stats["totals"]["native_hybrid"] == 1
    assert stats["totals"]["native_unclassified"] == 1


def test_worker_results_are_unclassified_until_touch_counts_cross_process_boundary():
    record_worker_payload_backend("heavy_unknown", "skia")
    record_worker_payload_backend("heavy_pure", "skia", {})
    record_worker_payload_backend("heavy_hybrid", "skia", {PILLOW_TOUCH_TEXT_METRIC: 4})

    endpoints = get_render_stats()["endpoints"]
    assert endpoints["heavy_unknown"]["native_unclassified"] == 1
    assert endpoints["heavy_pure"]["native_pure"] == 1
    assert endpoints["heavy_hybrid"]["native_hybrid"] == 1
    assert endpoints["heavy_hybrid"]["pillow_touch_reasons"][PILLOW_TOUCH_TEXT_METRIC] == {
        "renders": 1,
        "touches": 4,
    }


def test_worker_replay_consumes_and_merges_the_parent_request_scope():
    tokens = push_request_context("rid", "/api/heavy", "POST")
    try:
        record_pillow_touch(PILLOW_TOUCH_PLACEHOLDER)
        record_worker_payload_backend("heavy_merged", "skia", {PILLOW_TOUCH_TEXT_METRIC: 2})
        record_worker_payload_backend("heavy_missing_child", "skia")
    finally:
        pop_request_context(tokens)

    endpoints = get_render_stats()["endpoints"]
    merged = endpoints["heavy_merged"]
    assert merged["native_hybrid"] == 1
    assert merged["pillow_touch_reasons"][PILLOW_TOUCH_PLACEHOLDER] == {"renders": 1, "touches": 1}
    assert merged["pillow_touch_reasons"][PILLOW_TOUCH_TEXT_METRIC] == {"renders": 1, "touches": 2}
    # The first replay consumed the local scope. A later result with no child snapshot cannot
    # borrow those touches or claim purity.
    assert endpoints["heavy_missing_child"]["native_unclassified"] == 1


def test_run_in_pool_propagates_one_thread_safe_request_scope():
    async def exercise() -> None:
        tokens = push_request_context("rid", "/api/threaded", "POST")
        try:
            await asyncio.gather(*(run_in_pool(record_pillow_touch, PILLOW_TOUCH_TEXT_METRIC) for _ in range(24)))
            record_render("threaded", "skia")
        finally:
            pop_request_context(tokens)

    asyncio.run(exercise())
    entry = get_render_stats()["endpoints"]["threaded"]
    assert entry["native_hybrid"] == 1
    assert entry["pillow_touch_reasons"][PILLOW_TOUCH_TEXT_METRIC] == {
        "renders": 1,
        "touches": 24,
    }


def test_ir_painter_reports_pil_source_and_one_mem_raster(tmp_path):
    image = Image.new("RGB", (3, 2), "red")
    painter = IRPainter(
        (10, 10),
        assets_base_dir=str(tmp_path),
        font_dir=str(tmp_path),
        default_font="default",
        bold_font="bold",
    )

    tokens = push_request_context("rid", "/api/ir", "POST")
    try:
        assert painter._image_ref(image) == "mem:m0"
        assert painter._image_ref(image) == "mem:m0"
        record_render("ir", "skia")
    finally:
        pop_request_context(tokens)

    reasons = get_render_stats()["endpoints"]["ir"]["pillow_touch_reasons"]
    assert reasons[PILLOW_TOUCH_IRPAINTER_PIL_IMAGE] == {"renders": 1, "touches": 2}
    assert reasons[PILLOW_TOUCH_IRPAINTER_MEM_RASTER] == {"renders": 1, "touches": 1}


def test_header_probe_placeholder_and_text_metric_are_reported(tmp_path):
    Image.new("RGBA", (4, 5), "blue").save(tmp_path / "asset.png")

    async def exercise() -> None:
        tokens = push_request_context("rid", "/api/probe", "POST")
        try:
            ref = await get_asset_image_ref(tmp_path, "asset.png", on_missing="raise")
            placeholder = await get_asset_image_ref(tmp_path, "missing.png")
            assert ref.size == (4, 5)
            assert placeholder.size[0] > 0
            assert get_text_size(ImageFont.load_default(), "metric")[0] > 0
            record_render("probe", "skia")
        finally:
            pop_request_context(tokens)

    asyncio.run(exercise())
    reasons = get_render_stats()["endpoints"]["probe"]["pillow_touch_reasons"]
    assert reasons[PILLOW_TOUCH_IMAGE_HEADER_PROBE] == {"renders": 1, "touches": 1}
    assert reasons[PILLOW_TOUCH_PLACEHOLDER] == {"renders": 1, "touches": 1}
    assert reasons[PILLOW_TOUCH_TEXT_METRIC] == {"renders": 1, "touches": 1}
