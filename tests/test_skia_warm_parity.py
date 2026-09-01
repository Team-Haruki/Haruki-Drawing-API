from __future__ import annotations

import asyncio
from io import BytesIO
from types import SimpleNamespace

from PIL import Image

import scripts.skia_warm_parity as warm


def _png_bytes() -> bytes:
    buffer = BytesIO()
    Image.new("RGBA", (2, 2), (10, 20, 30, 255)).save(buffer, format="PNG")
    return buffer.getvalue()


def test_render_hashes_pillow_tuple_and_skia_payload() -> None:
    image = Image.new("RGBA", (2, 2), (10, 20, 30, 255))

    async def compose(_request):
        return image, 2.0

    async def try_render(_request):
        return SimpleNamespace(image_bytes=_png_bytes())

    pillow_case = SimpleNamespace(compose="compose", try_render="try_render")
    expected = warm._hash_image(image)
    assert (
        asyncio.run(warm._render(pillow_case, object(), SimpleNamespace(compose=compose), object(), "pillow"))
        == expected
    )
    assert (
        asyncio.run(warm._render(pillow_case, object(), object(), SimpleNamespace(try_render=try_render), "skia"))
        == expected
    )

    missing_case = SimpleNamespace(compose="compose", try_render=None)
    assert asyncio.run(warm._render(missing_case, object(), object(), object(), "skia")) is None

    async def fallback(_request):
        return None

    assert (
        asyncio.run(warm._render(pillow_case, object(), object(), SimpleNamespace(try_render=fallback), "skia")) is None
    )


def test_run_classifies_cache_results_and_binding_failures(monkeypatch) -> None:
    cases = [
        SimpleNamespace(name=name)
        for name in (
            "ok",
            "early-nondeterministic",
            "no-path",
            "cold-error",
            "drift",
            "late-nondeterministic",
            "warm-error",
            "skipped",
        )
    ]
    values = {
        "ok": ["A", "A", "A", "A", "A"],
        "early-nondeterministic": ["A", "B"],
        "no-path": [None, None],
        "cold-error": [RuntimeError("cold failed")],
        "drift": ["A", "A", "B", "A", "A"],
        "late-nondeterministic": ["A", "A", "B", "B", "C"],
        "warm-error": ["A", "A", RuntimeError("warm failed"), "A"],
    }

    def bind(case, _mysekai_real):
        if case.name == "skipped":
            return None, "skipped"
        return (case, object(), object(), object()), None

    async def render(case, *_args):
        value = values[case.name].pop(0)
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(warm, "_bind", bind)
    monkeypatch.setattr(warm, "_render", render)
    monkeypatch.setattr(warm, "clear_all_caches", lambda: None)

    rows = asyncio.run(warm.run(cases, "skia", None))
    by_name = {row["endpoint"]: row for row in rows}

    assert by_name["ok"]["status"] == "ok"
    assert by_name["early-nondeterministic"]["status"] == "nondeterministic"
    assert by_name["no-path"]["status"] == "no-path"
    assert by_name["cold-error"]["status"] == "error"
    assert "trace" in by_name["cold-error"]
    assert by_name["drift"]["status"] == "CACHE-DRIFT"
    assert by_name["drift"]["drift"] == {
        "cold_vs_warm_fwd": "DIFFERENT",
        "cold_vs_warm_rev": "same",
        "warm_fwd_vs_warm_rev": "DIFFERENT",
    }
    assert by_name["late-nondeterministic"]["status"] == "nondeterministic"
    assert "time-dependent" in by_name["late-nondeterministic"]["note"]
    assert by_name["warm-error"]["status"] == "error"
    assert by_name["warm-error"]["error"] == "RuntimeError: warm failed"
    assert by_name["skipped"] == {"endpoint": "skipped", "backend": "skia", "status": "skipped"}
