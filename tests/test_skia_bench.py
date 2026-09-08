"""Response-byte benchmarks must charge the same route footer on both sides."""

from __future__ import annotations

import asyncio
from io import BytesIO
from types import SimpleNamespace

from PIL import Image
import pytest

from scripts.skia_bench import bench_case
from src.sekai.base import draw


@pytest.mark.parametrize("route_watermark", [True, False])
def test_benchmark_checks_full_response_dimensions(monkeypatch, route_watermark):
    calls = []

    async def compose(_request):
        return Image.new("RGBA", (12, 8))

    async def footer(image, request):
        calls.append(request)
        return Image.new("RGBA", (image.width, image.height + 5))

    async def native(_request):
        return SimpleNamespace(image_bytes=b"native", media_type="image/png", image_width=12, image_height=13)

    monkeypatch.setattr(draw, "add_request_watermark_to_image", footer)
    case = SimpleNamespace(name="footer", compose="compose", try_render="native", route_watermark=route_watermark)
    result = bench_case(
        case,
        "request",
        SimpleNamespace(compose=compose),
        SimpleNamespace(native=native),
        reps=1,
        cold=False,
        output_format="png",
        jpg_quality=90,
    )
    if route_watermark:
        assert asyncio.run(result)["endpoint"] == "footer"
        assert calls == ["request", "request"]
    else:
        with pytest.raises(ValueError, match="response dimensions differ"):
            asyncio.run(result)


import scripts.skia_bench as bench


def test_bench_case_warms_both_paths_and_alternates_measurements(monkeypatch) -> None:
    calls = {"pillow": 0, "skia": 0, "clear": 0, "payload_clear": 0, "encode": 0}

    async def compose(_request):
        calls["pillow"] += 1
        return Image.new("RGBA", (1, 1)), 2.0

    async def try_render(_request):
        calls["skia"] += 1
        return SimpleNamespace(image_bytes=b"native", media_type="image/png", image_width=1, image_height=1)

    monkeypatch.setattr(bench, "clear_all_caches", lambda: calls.__setitem__("clear", calls["clear"] + 1))
    monkeypatch.setattr(
        bench,
        "clear_skia_payload_cache",
        lambda: calls.__setitem__("payload_clear", calls["payload_clear"] + 1),
    )
    monkeypatch.setattr(
        bench,
        "_encode_image",
        lambda *_args: (
            calls.__setitem__("encode", calls["encode"] + 1) or BytesIO(b"encoded"),
            "image/png",
            "image.png",
        ),
    )
    case = SimpleNamespace(name="fixture", compose="compose", try_render="try_render")
    row = asyncio.run(
        bench.bench_case(
            case,
            object(),
            SimpleNamespace(compose=compose),
            SimpleNamespace(try_render=try_render),
            reps=2,
            cold=False,
        )
    )

    assert row is not None
    assert row["endpoint"] == "fixture"
    assert row["pillow"] > 0
    assert row["skia"] > 0
    assert row["speedup"] > 0
    assert calls == {"pillow": 3, "skia": 3, "clear": 0, "payload_clear": 3, "encode": 3}


def test_bench_case_cold_mode_clears_before_every_backend(monkeypatch) -> None:
    calls = {"clear": 0, "payload_clear": 0}

    async def compose(_request):
        return Image.new("RGBA", (1, 1))

    async def try_render(_request):
        return SimpleNamespace(image_bytes=b"native", media_type="image/png", image_width=1, image_height=1)

    monkeypatch.setattr(bench, "clear_all_caches", lambda: calls.__setitem__("clear", calls["clear"] + 1))
    monkeypatch.setattr(
        bench,
        "clear_skia_payload_cache",
        lambda: calls.__setitem__("payload_clear", calls["payload_clear"] + 1),
    )
    monkeypatch.setattr(bench, "_encode_image", lambda *_args: (BytesIO(b"encoded"), "image/png", "image.png"))
    case = SimpleNamespace(name="cold", compose="compose", try_render="try_render")

    row = asyncio.run(
        bench.bench_case(
            case,
            object(),
            SimpleNamespace(compose=compose),
            SimpleNamespace(try_render=try_render),
            reps=2,
            cold=True,
        )
    )

    assert row is not None
    assert calls == {"clear": 4, "payload_clear": 2}


def test_bench_case_declines_missing_or_fallback_skia_path(monkeypatch) -> None:
    async def compose(_request):
        return Image.new("RGBA", (1, 1))

    async def fallback(_request):
        return None

    monkeypatch.setattr(bench, "_encode_image", lambda *_args: (BytesIO(b"encoded"), "image/png", "image.png"))
    monkeypatch.setattr(bench, "clear_skia_payload_cache", lambda: None)
    missing = SimpleNamespace(name="missing", compose="compose", try_render=None)
    assert asyncio.run(bench.bench_case(missing, object(), object(), object(), reps=1, cold=False)) is None

    fallback_case = SimpleNamespace(name="fallback", compose="compose", try_render="try_render")
    assert (
        asyncio.run(
            bench.bench_case(
                fallback_case,
                object(),
                SimpleNamespace(compose=compose),
                SimpleNamespace(try_render=fallback),
                reps=1,
                cold=False,
            )
        )
        is None
    )
