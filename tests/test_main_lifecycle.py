from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from typing import Any

import pytest

from src.core import heavy_render_pool, main as main_mod
from src.sekai.base import painter, utils
from src.sekai.profile.custom_profile import diagnostics
from src.sekai.sk import drawer as sk_drawer
from src.sekai.skia_renderer import canvas, ir_builder
import src.settings as settings_mod
from src.settings import settings


def test_nogil_runtime_guard_rejects_unknown_or_enabled_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(main_mod.sys, "_is_gil_enabled", raising=False)
    with pytest.raises(RuntimeError, match="does not expose GIL status"):
        main_mod._ensure_nogil_runtime()

    monkeypatch.setattr(main_mod.sys, "_is_gil_enabled", lambda: True, raising=False)
    with pytest.raises(RuntimeError, match="GIL is enabled"):
        main_mod._ensure_nogil_runtime()

    monkeypatch.setattr(main_mod.sys, "_is_gil_enabled", lambda: False)
    main_mod._ensure_nogil_runtime()


@pytest.mark.parametrize(
    ("path", "name", "expected"),
    [
        ("/fonts/Example.otf", "Example.ttf", True),
        ("/fonts/Other.otf", "Example.ttf", False),
        (object(), "Example.ttf", False),
    ],
)
def test_font_resolution_uses_the_loaded_face_path(
    path: object,
    name: str,
    expected: bool,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(painter, "get_font", lambda _name, _size: SimpleNamespace(path=path))

    assert main_mod._font_resolves(name) is expected


def test_pillow_font_check_separates_text_and_emoji(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings_mod, "DEFAULT_FONT", "regular")
    monkeypatch.setattr(settings_mod, "DEFAULT_BOLD_FONT", "bold")
    monkeypatch.setattr(settings_mod, "DEFAULT_HEAVY_FONT", "heavy")
    monkeypatch.setattr(settings_mod, "DEFAULT_EMOJI_FONT", "emoji")
    monkeypatch.setattr(main_mod, "_font_resolves", lambda name: name in {"regular", "heavy"})

    assert main_mod._check_pillow_fonts() == (["bold"], ["emoji"])


def test_native_font_check_reports_only_fallback_faces(monkeypatch: pytest.MonkeyPatch) -> None:
    names = ("regular", "bold", "heavy")
    monkeypatch.setattr(settings_mod, "DEFAULT_FONT", names[0])
    monkeypatch.setattr(settings_mod, "DEFAULT_BOLD_FONT", names[1])
    monkeypatch.setattr(settings_mod, "DEFAULT_HEAVY_FONT", names[2])

    class Builder:
        def __init__(self, *_args: Any, default_font: str, **_kwargs: Any) -> None:
            self.default_font = default_font

        def text(self, *_args: Any, **_kwargs: Any) -> None:
            return None

        def build(self) -> dict[str, str]:
            return {"font": self.default_font}

    class Native:
        def render_scene(self, scene: bytes, _images: dict[str, object]) -> dict[str, object]:
            name = json.loads(scene)["font"]
            return {"native_metrics": {"font_fallbacks": int(name != "bold")}}

    monkeypatch.setattr(ir_builder, "IRBuilder", Builder)
    monkeypatch.setattr(canvas, "load_native_renderer", lambda: Native())

    assert main_mod._check_native_fonts() == ["regular", "heavy"]


def test_font_self_check_skips_native_probe_when_disabled_or_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(main_mod, "_check_pillow_fonts", lambda: ([], []))
    monkeypatch.setattr(settings.drawing, "use_skia_plot", False)
    monkeypatch.setattr(main_mod, "_check_native_fonts", lambda: pytest.fail("native probe should be skipped"))
    main_mod._self_check_fonts()

    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)

    def unavailable() -> list[str]:
        raise ImportError("extension missing")

    monkeypatch.setattr(main_mod, "_check_native_fonts", unavailable)
    main_mod._self_check_fonts()


def test_disk_cache_cleanup_reports_only_nonempty_sweeps(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counts = {"composed": 0, "painter": 0, "diagnostic": 0}
    monkeypatch.setattr(utils, "cleanup_expired_composed_image_disk_cache", lambda: counts["composed"])
    monkeypatch.setattr(painter.Painter, "cleanup_old_disk_cache", lambda: counts["painter"])
    monkeypatch.setattr(diagnostics, "cleanup_custom_profile_diagnostics", lambda: counts["diagnostic"])

    with caplog.at_level(logging.INFO, logger=main_mod.__name__):
        main_mod._cleanup_disk_caches()
    assert not caplog.records

    counts.update(composed=1, painter=2, diagnostic=3)
    with caplog.at_level(logging.INFO, logger=main_mod.__name__):
        main_mod._cleanup_disk_caches()
    assert "composed=1 painter=2 custom_profile_diagnostics=3" in caplog.records[-1].message


def test_periodic_cleanup_survives_a_failed_sweep(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sleep_calls = 0

    async def controlled_sleep(_seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls > 1:
            raise asyncio.CancelledError

    def failed_cleanup() -> None:
        raise RuntimeError("cleanup failed")

    monkeypatch.setattr(main_mod.asyncio, "sleep", controlled_sleep)
    with caplog.at_level(logging.WARNING, logger=main_mod.__name__):
        with pytest.raises(asyncio.CancelledError):
            asyncio.run(main_mod._periodic_cleanup(1, failed_cleanup, "cleanup warning"))

    assert sleep_calls == 2
    assert caplog.records[-1].message == "cleanup warning"


def test_cleanup_tasks_are_created_and_cancellable() -> None:
    async def exercise() -> None:
        tasks = main_mod._create_cleanup_tasks()
        assert len(tasks) == 2
        assert all(isinstance(task, asyncio.Task) for task in tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)

    asyncio.run(exercise())


def test_initial_disk_cleanup_is_fail_open(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls = 0

    def cleanup() -> None:
        nonlocal calls
        calls += 1

    monkeypatch.setattr(main_mod, "_cleanup_disk_caches", cleanup)
    main_mod._run_initial_disk_cleanup()
    assert calls == 1

    monkeypatch.setattr(main_mod, "_cleanup_disk_caches", lambda: (_ for _ in ()).throw(RuntimeError("failed")))
    with caplog.at_level(logging.WARNING, logger=main_mod.__name__):
        main_mod._run_initial_disk_cleanup()
    assert caplog.records[-1].message == "Failed to cleanup drawing disk caches"


def test_skia_import_probe_honors_the_gate(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(settings.drawing, "use_skia_plot", False)
    monkeypatch.setitem(main_mod.sys.modules, "haruki_skia_renderer", None)
    main_mod._report_missing_skia_extension()
    assert not caplog.records

    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)
    with caplog.at_level(logging.ERROR, logger=main_mod.__name__):
        main_mod._report_missing_skia_extension()
    assert "not importable" in caplog.records[-1].message

    caplog.clear()
    monkeypatch.setitem(main_mod.sys.modules, "haruki_skia_renderer", SimpleNamespace())
    main_mod._report_missing_skia_extension()
    assert not caplog.records


def test_runtime_startup_runs_each_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    cleanup_tasks: list[Any] = [object()]

    monkeypatch.setattr(main_mod, "_ensure_nogil_runtime", lambda: calls.append("nogil"))
    monkeypatch.setattr(main_mod.coloredlogs, "install", lambda **_kwargs: calls.append("logging"))
    monkeypatch.setattr(main_mod, "configure_runtime_diagnostics", lambda: calls.append("diagnostics"))
    monkeypatch.setattr(main_mod, "_create_cleanup_tasks", lambda: calls.append("tasks") or cleanup_tasks)
    monkeypatch.setattr(main_mod, "_run_initial_disk_cleanup", lambda: calls.append("disk"))
    monkeypatch.setattr(main_mod, "_report_missing_skia_extension", lambda: calls.append("skia"))
    monkeypatch.setattr(main_mod, "_self_check_fonts", lambda: calls.append("fonts"))

    async def start_pool() -> None:
        calls.append("pool")

    monkeypatch.setattr(heavy_render_pool, "startup_heavy_render_worker_pool", start_pool)

    assert asyncio.run(main_mod._startup_runtime()) is cleanup_tasks
    assert calls == ["nogil", "logging", "diagnostics", "tasks", "disk", "skia", "fonts", "pool"]


def test_runtime_shutdown_cancels_tasks_and_releases_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(main_mod, "dump_runtime_diagnostics", lambda reason: calls.append(reason))

    async def stop_pool() -> None:
        calls.append("pool")

    monkeypatch.setattr(heavy_render_pool, "shutdown_heavy_render_worker_pool", stop_pool)
    monkeypatch.setattr(painter, "shutdown_painter", lambda: calls.append("painter"))
    monkeypatch.setattr(sk_drawer, "shutdown_sk_drawer", lambda: calls.append("sk"))
    monkeypatch.setattr(utils, "shutdown_utils", lambda: calls.append("utils"))

    async def exercise() -> None:
        task = asyncio.create_task(asyncio.Event().wait())
        await main_mod._shutdown_runtime([task])
        assert task.cancelled()

    asyncio.run(exercise())
    assert calls == ["lifespan_shutdown", "pool", "painter", "sk", "utils"]


def test_lifespan_delegates_startup_and_shutdown(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[object] = []
    cleanup_tasks: list[Any] = [object()]

    async def startup() -> list[Any]:
        calls.append("startup")
        return cleanup_tasks

    async def shutdown(received: list[Any]) -> None:
        calls.append(received)

    monkeypatch.setattr(main_mod, "_startup_runtime", startup)
    monkeypatch.setattr(main_mod, "_shutdown_runtime", shutdown)

    async def exercise() -> None:
        async with main_mod.lifespan(SimpleNamespace()):
            calls.append("running")

    asyncio.run(exercise())
    assert calls == ["startup", "running", cleanup_tasks]


def test_app_version_reads_project_metadata_and_falls_back(monkeypatch: pytest.MonkeyPatch) -> None:
    assert main_mod._app_version() != "unknown"

    def failed_open(*_args: Any, **_kwargs: Any) -> None:
        raise OSError("missing")

    monkeypatch.setattr("builtins.open", failed_open)
    assert main_mod._app_version() == "unknown"
