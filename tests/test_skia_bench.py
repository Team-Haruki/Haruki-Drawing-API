from __future__ import annotations

import asyncio
from types import SimpleNamespace

from PIL import Image

import scripts.skia_bench as bench


def test_bench_case_warms_both_paths_and_alternates_measurements(monkeypatch) -> None:
    calls = {"pillow": 0, "skia": 0, "clear": 0, "payload_clear": 0, "encode": 0}

    async def compose(_request):
        calls["pillow"] += 1
        return Image.new("RGBA", (1, 1)), 2.0

    async def try_render(_request):
        calls["skia"] += 1
        return object()

    monkeypatch.setattr(bench, "clear_all_caches", lambda: calls.__setitem__("clear", calls["clear"] + 1))
    monkeypatch.setattr(
        bench,
        "clear_skia_payload_cache",
        lambda: calls.__setitem__("payload_clear", calls["payload_clear"] + 1),
    )
    monkeypatch.setattr(
        bench,
        "_encode_image",
        lambda *_args: calls.__setitem__("encode", calls["encode"] + 1),
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
        return object()

    monkeypatch.setattr(bench, "clear_all_caches", lambda: calls.__setitem__("clear", calls["clear"] + 1))
    monkeypatch.setattr(
        bench,
        "clear_skia_payload_cache",
        lambda: calls.__setitem__("payload_clear", calls["payload_clear"] + 1),
    )
    monkeypatch.setattr(bench, "_encode_image", lambda *_args: b"encoded")
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

    monkeypatch.setattr(bench, "_encode_image", lambda *_args: b"encoded")
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
