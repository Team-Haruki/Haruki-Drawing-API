from __future__ import annotations

import asyncio
import json
import logging
from types import SimpleNamespace
from typing import Any

import pytest

from src.assets import mirror as mirror_mod
from src.assets.mirror import AssetMirror, MirrorStats, NullMirror
from src.assets.version import StaticVersion
from src.core import heavy_render_pool, main as main_mod
from src.sekai.base import painter_cache, utils
from src.sekai.profile.custom_profile import diagnostics
from src.sekai.skia_renderer import canvas, ir_builder
import src.settings as settings_mod
from src.settings import AssetMirrorSettings, settings
from tests.storage_fakes import FakeObjectStore


def test_nogil_runtime_guard_rejects_unknown_or_enabled_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delattr(main_mod.sys, "_is_gil_enabled", raising=False)
    with pytest.raises(RuntimeError, match="does not expose GIL status"):
        main_mod._ensure_nogil_runtime()

    monkeypatch.setattr(main_mod.sys, "_is_gil_enabled", lambda: True, raising=False)
    with pytest.raises(RuntimeError, match="GIL is enabled"):
        main_mod._ensure_nogil_runtime()

    monkeypatch.setattr(main_mod.sys, "_is_gil_enabled", lambda: False)
    main_mod._ensure_nogil_runtime()


@pytest.mark.parametrize("name", ["regular", "bold", "heavy"])
def test_startup_rejects_each_missing_text_face(monkeypatch, name):
    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)
    monkeypatch.setattr(main_mod, "_check_native_fonts", lambda **_: [name])
    with pytest.raises(RuntimeError, match="configured text fonts cannot be resolved"):
        main_mod._self_check_fonts()


def test_missing_emoji_is_reported_without_blocking_native_text(monkeypatch, caplog):
    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)
    monkeypatch.setattr(main_mod, "_check_native_fonts", lambda *, emoji=False: ["emoji"] if emoji else [])
    with caplog.at_level(logging.ERROR, logger=main_mod.__name__):
        main_mod._self_check_fonts()
    assert "native emoji font cannot be resolved" in caplog.text


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


def test_font_self_check_rejects_disabled_or_unavailable_native(monkeypatch):
    monkeypatch.setattr(settings.drawing, "use_skia_plot", False)
    with pytest.raises(RuntimeError, match="Native rendering is required"):
        main_mod._self_check_fonts()
    monkeypatch.setattr(settings.drawing, "use_skia_plot", True)

    def unavailable():
        raise ImportError("extension missing")

    monkeypatch.setattr(main_mod, "_check_native_fonts", unavailable)
    with pytest.raises(RuntimeError, match="Native renderer startup check failed"):
        main_mod._self_check_fonts()


def test_disk_cache_cleanup_reports_only_nonempty_sweeps(
    caplog: pytest.LogCaptureFixture,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    counts = {"composed": 0, "painter": 0, "diagnostic": 0}
    monkeypatch.setattr(utils, "cleanup_expired_composed_image_disk_cache", lambda: counts["composed"])
    monkeypatch.setattr(painter_cache, "cleanup_painter_disk_cache", lambda: counts["painter"])
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


def test_cleanup_tasks_are_created_and_cancellable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.assets, "source", "local")

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


def test_failed_native_startup_allocates_no_cleanup_tasks(monkeypatch):
    monkeypatch.setattr(main_mod, "_ensure_nogil_runtime", lambda: None)
    monkeypatch.setattr(main_mod, "configure_runtime_diagnostics", lambda: None)

    def unavailable():
        raise RuntimeError("native unavailable")

    monkeypatch.setattr(main_mod, "_self_check_fonts", unavailable)
    monkeypatch.setattr(mirror_mod, "start_asset_mirror", lambda: pytest.fail("startup built the mirror before checks"))
    monkeypatch.setattr(main_mod, "_create_cleanup_tasks", lambda: pytest.fail("startup allocated tasks before checks"))
    with pytest.raises(RuntimeError, match="native unavailable"):
        asyncio.run(main_mod._startup_runtime())


def test_runtime_startup_runs_each_stage(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    cleanup_tasks: list[Any] = [object()]

    monkeypatch.setattr(main_mod, "_ensure_nogil_runtime", lambda: calls.append("nogil"))
    monkeypatch.setattr(main_mod.coloredlogs, "install", lambda **_kwargs: calls.append("logging"))
    monkeypatch.setattr(main_mod, "configure_runtime_diagnostics", lambda: calls.append("diagnostics"))
    monkeypatch.setattr(main_mod, "_create_cleanup_tasks", lambda: calls.append("tasks") or cleanup_tasks)
    monkeypatch.setattr(main_mod, "_run_initial_disk_cleanup", lambda: calls.append("disk"))
    monkeypatch.setattr(main_mod, "_self_check_fonts", lambda: calls.append("fonts"))
    monkeypatch.setattr(mirror_mod, "start_asset_mirror", lambda: calls.append("mirror"))

    async def start_pool() -> None:
        calls.append("pool")

    monkeypatch.setattr(heavy_render_pool, "startup_heavy_render_worker_pool", start_pool)

    assert asyncio.run(main_mod._startup_runtime()) is cleanup_tasks
    assert calls == ["nogil", "logging", "diagnostics", "fonts", "mirror", "tasks", "disk", "pool"]


def test_runtime_shutdown_cancels_tasks_and_releases_resources(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    monkeypatch.setattr(main_mod, "dump_runtime_diagnostics", lambda reason: calls.append(reason))

    async def stop_pool() -> None:
        calls.append("pool")

    monkeypatch.setattr(heavy_render_pool, "shutdown_heavy_render_worker_pool", stop_pool)
    monkeypatch.setattr(mirror_mod, "shutdown_asset_mirror", lambda: calls.append("mirror"))
    monkeypatch.setattr(painter_cache, "cleanup_painter_disk_cache", lambda: calls.append("painter"))
    monkeypatch.setattr(utils, "shutdown_utils", lambda: calls.append("utils"))

    async def exercise() -> None:
        task = asyncio.create_task(asyncio.Event().wait())
        await main_mod._shutdown_runtime([task])
        assert task.cancelled()

    asyncio.run(exercise())
    assert calls == ["lifespan_shutdown", "pool", "mirror", "painter", "utils"]


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


# ---------------------------------------------------------------------- asset mirror wiring (T6)


@pytest.fixture
def fake_mirror(tmp_path, monkeypatch: pytest.MonkeyPatch):
    mirror = AssetMirror(
        base_dir=tmp_path,
        settings=AssetMirrorSettings(),
        store_factory=lambda region: FakeObjectStore(bucket="pjsk-assets"),
        version_source=StaticVersion("v1"),
        stats=MirrorStats(),
    )
    mirror_mod.set_asset_mirror(mirror)
    try:
        yield mirror
    finally:
        mirror_mod.set_asset_mirror(None)
        mirror.close()


def _task_coroutine_names(tasks: list[asyncio.Task[None]]) -> list[str]:
    names = []
    for task in tasks:
        frame = task.get_coro().cr_frame
        func = frame.f_locals.get("cleanup") or frame.f_locals.get("func")
        names.append(getattr(func, "__name__", "?"))
    return names


def _created_tasks(monkeypatch: pytest.MonkeyPatch, source: str) -> list[str]:
    monkeypatch.setattr(settings.assets, "source", source)

    async def exercise() -> list[str]:
        tasks = main_mod._create_cleanup_tasks()
        await asyncio.sleep(0)
        names = _task_coroutine_names(tasks)
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        return names

    return asyncio.run(exercise())


def test_local_source_creates_no_mirror_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    names = _created_tasks(monkeypatch, "local")
    assert len(names) == 2
    assert "_sweep_asset_mirror" not in names
    assert "_poll_asset_mirror_version" not in names


def test_mirror_source_creates_sweep_and_poll_tasks(monkeypatch: pytest.MonkeyPatch) -> None:
    names = _created_tasks(monkeypatch, "mirror")
    assert len(names) == 4
    assert names[2:] == ["_sweep_asset_mirror", "_poll_asset_mirror_version"]


def test_periodic_pool_task_runs_on_pool_and_survives_failures(
    caplog: pytest.LogCaptureFixture, monkeypatch: pytest.MonkeyPatch
) -> None:
    sleep_calls = 0
    threads: list[int] = []

    async def controlled_sleep(_seconds: float) -> None:
        nonlocal sleep_calls
        sleep_calls += 1
        if sleep_calls > 2:
            raise asyncio.CancelledError

    def job() -> None:
        import threading

        threads.append(threading.get_ident())
        raise RuntimeError("sweep failed")

    monkeypatch.setattr(main_mod.asyncio, "sleep", controlled_sleep)

    async def exercise() -> int:
        import threading

        loop_thread = threading.get_ident()
        with pytest.raises(asyncio.CancelledError):
            await main_mod._periodic_pool_task(1, job, "pool job warning")
        return loop_thread

    with caplog.at_level(logging.WARNING, logger=main_mod.__name__):
        loop_thread = asyncio.run(exercise())
    assert len(threads) == 2
    assert loop_thread not in threads
    assert [r.message for r in caplog.records].count("pool job warning") == 2


def test_sweep_asset_mirror_skips_null_mirror(monkeypatch: pytest.MonkeyPatch) -> None:
    mirror_mod.set_asset_mirror(NullMirror())
    try:
        main_mod._sweep_asset_mirror()
        assert mirror_mod.get_asset_mirror().stats_snapshot()["sweeps"] == 0
    finally:
        mirror_mod.set_asset_mirror(None)


def test_sweep_asset_mirror_enforces_settings(
    fake_mirror: AssetMirror, tmp_path, monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    import os

    root = fake_mirror.mirror_root
    for index, name in enumerate(["v0", "old1", "old2", "v1"]):
        path = root / name / "jp-assets" / "startapp" / "a.png"
        path.parent.mkdir(parents=True)
        path.write_bytes(b"12345")
        os.utime(root / name, (1_000 + index, 1_000 + index))
    monkeypatch.setattr(settings.assets.mirror, "versions_keep", 1)
    monkeypatch.setattr(settings.assets.mirror, "max_entries", 0)
    monkeypatch.setattr(settings.assets.mirror, "max_bytes", 0)
    with caplog.at_level(logging.INFO, logger=main_mod.__name__):
        main_mod._sweep_asset_mirror()
    assert sorted(p.name for p in root.iterdir()) == ["old2", "v1"]
    snap = fake_mirror.stats_snapshot()
    assert snap["sweeps"] == 1
    assert snap["versions_removed"] == 2
    assert snap["dir_entries"] == 1
    assert snap["dir_bytes"] == 5
    assert "mirror.sweep versions_removed=2" in caplog.text

    caplog.clear()
    with caplog.at_level(logging.INFO, logger=main_mod.__name__):
        main_mod._sweep_asset_mirror()
    assert "mirror.sweep " not in caplog.text


def test_poll_asset_mirror_version_applies_a_change(fake_mirror: AssetMirror, monkeypatch) -> None:
    main_mod._poll_asset_mirror_version()
    assert fake_mirror.version == "v1"
    fake_mirror._version_source = StaticVersion("v2")
    main_mod._poll_asset_mirror_version()
    assert fake_mirror.version == "v2"
    assert fake_mirror.stats_snapshot()["version_changes"] == 1


def test_start_asset_mirror_local_source_warns_nothing(monkeypatch, caplog) -> None:
    calls: list[str] = []
    monkeypatch.setattr(settings.assets, "source", "local")
    monkeypatch.setattr(mirror_mod, "start_asset_mirror", lambda: calls.append("start"))
    monkeypatch.setattr(main_mod, "_missing_custom_profile_dirs", lambda: pytest.fail("checked dirs in local mode"))
    with caplog.at_level(logging.WARNING, logger=main_mod.__name__):
        main_mod._start_asset_mirror()
    assert calls == ["start"]
    assert not caplog.records


def test_start_asset_mirror_warns_on_missing_custom_profile_dirs(tmp_path, monkeypatch, caplog) -> None:
    monkeypatch.setattr(settings.assets, "source", "mirror")
    monkeypatch.setattr(mirror_mod, "start_asset_mirror", lambda: None)
    present = tmp_path / "jp-assets" / "custom_profile"
    present.mkdir(parents=True)
    monkeypatch.setattr(settings.drawing, "custom_profile_assets_dir", tmp_path / "{region}-assets" / "custom_profile")
    monkeypatch.setattr(settings.drawing, "custom_profile_fonts_dir", None)
    monkeypatch.setattr(settings.drawing, "custom_profile_shape_sprite_dir", present)
    monkeypatch.setattr(settings.drawing, "custom_profile_unity_ui_sprite_dir", tmp_path / "ui")

    missing = main_mod._missing_custom_profile_dirs()
    assert f"custom_profile_assets_dir={tmp_path / 'jp-assets' / 'custom_profile'}" not in missing
    assert f"custom_profile_assets_dir={tmp_path / 'cn-assets' / 'custom_profile'}" in missing
    assert len([m for m in missing if m.startswith("custom_profile_assets_dir=")]) == 4
    assert f"custom_profile_unity_ui_sprite_dir={tmp_path / 'ui'}" in missing
    assert not any(m.startswith(("custom_profile_fonts_dir", "custom_profile_shape_sprite_dir")) for m in missing)

    with caplog.at_level(logging.WARNING, logger=main_mod.__name__):
        main_mod._start_asset_mirror()
    assert "custom-profile directories are missing" in caplog.text

    caplog.clear()
    monkeypatch.setattr(main_mod, "_missing_custom_profile_dirs", lambda: [])
    with caplog.at_level(logging.WARNING, logger=main_mod.__name__):
        main_mod._start_asset_mirror()
    assert not caplog.records


def test_start_asset_mirror_dir_check_failure_is_not_fatal(monkeypatch, caplog) -> None:
    monkeypatch.setattr(settings.assets, "source", "mirror")
    monkeypatch.setattr(mirror_mod, "start_asset_mirror", lambda: None)

    def broken() -> list[str]:
        raise RuntimeError("stat failed")

    monkeypatch.setattr(main_mod, "_missing_custom_profile_dirs", broken)
    with caplog.at_level(logging.WARNING, logger=main_mod.__name__):
        main_mod._start_asset_mirror()
    assert "custom-profile directory check failed" in caplog.text


def test_start_and_shutdown_asset_mirror_lifecycle(fake_mirror: AssetMirror, caplog) -> None:
    with caplog.at_level(logging.INFO, logger="src.assets.mirror"):
        assert mirror_mod.start_asset_mirror() is fake_mirror
    assert fake_mirror.fetch_loop_running
    assert "mirror.started version=v1" in caplog.text
    mirror_mod.shutdown_asset_mirror()
    assert not fake_mirror.fetch_loop_running
    # the closed mirror stays installed: shutdown never lazily builds a new one
    assert mirror_mod.get_asset_mirror() is fake_mirror
    mirror_mod.shutdown_asset_mirror()


def test_start_asset_mirror_local_source_is_null(monkeypatch) -> None:
    mirror_mod.set_asset_mirror(None)
    monkeypatch.setattr(settings.assets, "source", "local")
    try:
        started = mirror_mod.start_asset_mirror()
        assert isinstance(started, NullMirror)
    finally:
        mirror_mod.set_asset_mirror(None)


def test_start_asset_mirror_failure_installs_null_mirror(monkeypatch, caplog) -> None:
    class Exploding(NullMirror):
        def start(self) -> None:
            raise RuntimeError("thread limit")

    mirror_mod.set_asset_mirror(Exploding(source="mirror"))
    try:
        with caplog.at_level(logging.ERROR, logger="src.assets.mirror"):
            started = mirror_mod.start_asset_mirror()
        assert isinstance(started, NullMirror)
        assert not isinstance(started, Exploding)
        assert started.stats.disabled_reason == "RuntimeError: thread limit"
        assert mirror_mod.get_asset_mirror() is started
        assert "mirror.start_failed" in caplog.text
    finally:
        mirror_mod.set_asset_mirror(None)


def test_shutdown_asset_mirror_without_mirror_builds_nothing(monkeypatch) -> None:
    mirror_mod.set_asset_mirror(None)
    monkeypatch.setattr(mirror_mod, "build_asset_mirror", lambda _settings: pytest.fail("built during shutdown"))
    mirror_mod.shutdown_asset_mirror()


def test_shutdown_asset_mirror_close_failure_is_logged(caplog) -> None:
    class Broken(NullMirror):
        def close(self) -> None:
            raise RuntimeError("close failed")

    mirror_mod.set_asset_mirror(Broken())
    try:
        with caplog.at_level(logging.WARNING, logger="src.assets.mirror"):
            mirror_mod.shutdown_asset_mirror()
        assert "mirror.shutdown_failed" in caplog.text
    finally:
        mirror_mod.set_asset_mirror(None)
