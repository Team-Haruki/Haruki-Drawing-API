"""ArtifactRuntime composition root: fail-open build, lazy accessor, lifecycle (plan §8.5)."""

from __future__ import annotations

import asyncio
import logging
import sys

from pydantic import SecretStr
import pytest

from src.artifact import runtime as mod
from src.artifact.directive import RenderCacheDirective
from src.artifact.stats import ArtifactStats
from src.core.image_payload import EncodedImagePayload
from src.settings import Settings, StorageSettings
from tests.storage_fakes import FakeObjectStore, FakeRenderIndex, build_test_runtime


@pytest.fixture(autouse=True)
def _isolate():
    mod.set_artifact_runtime(None)
    yield
    mod.set_artifact_runtime(None)


def _settings(**storage) -> Settings:
    s = Settings()
    s.storage = StorageSettings(**storage)
    return s


def _payload() -> EncodedImagePayload:
    return EncodedImagePayload(b"png", "image/png", "x.png", 1, 1, "RGBA", 0.0)


def _directive() -> RenderCacheDirective:
    return RenderCacheDirective("0123456789abcdef", 3, 0, True, "pjsk", "api/x", "public")


def test_disabled_by_default_never_imports_opendal(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "opendal", None)  # any import attempt would raise ImportError
    stats = ArtifactStats()
    runtime = mod.build_artifact_runtime(Settings(), stats=stats, store_factory=lambda _s: pytest.fail("built store"))
    assert not runtime.enabled
    assert runtime.reason == "disabled"
    outcome = asyncio.run(runtime.process(_payload(), _directive()))
    assert outcome.ref is None
    assert outcome.degraded
    assert outcome.reason == "disabled"
    snap = stats.snapshot()
    assert snap["enabled"] is False
    assert snap["degraded"]["disabled"] == 1


def test_lazy_accessor_builds_disabled_runtime_without_lifespan(monkeypatch: pytest.MonkeyPatch) -> None:
    from src.settings import settings

    monkeypatch.setattr(settings.storage, "enabled", False)
    monkeypatch.setitem(sys.modules, "opendal", None)
    runtime = mod.get_artifact_runtime()
    assert runtime is mod.get_artifact_runtime()
    assert runtime.reason == "disabled"


def test_enabled_without_dsn_uploads_only(caplog: pytest.LogCaptureFixture) -> None:
    store = FakeObjectStore(bucket="image-cache")
    stats = ArtifactStats()
    with caplog.at_level(logging.WARNING, logger="src.artifact.runtime"):
        runtime = mod.build_artifact_runtime(
            _settings(enabled=True), store_factory=lambda _s: store, node_name="cn09", stats=stats
        )
    assert runtime.enabled
    assert runtime.index is None
    assert "artifact index disabled" in caplog.text
    outcome = asyncio.run(runtime.process(_payload(), _directive()))
    assert outcome.ref is not None
    assert outcome.ref.index_written is False
    assert outcome.ref.node_name == "cn09"
    assert len(store.writes) == 1
    snap = stats.snapshot()
    assert snap["enabled"] is True
    assert snap["bucket"] == "image-cache"
    assert snap["index_skipped"]["disabled"] == 1


def test_index_disabled_by_setting_and_blank_dsn(caplog: pytest.LogCaptureFixture) -> None:
    storage = StorageSettings(enabled=True)
    storage.index.enabled = False
    with caplog.at_level(logging.INFO, logger="src.artifact.runtime"):
        assert mod._default_index_factory(storage) is None
    assert "storage.index.enabled=false" in caplog.text
    storage = StorageSettings(enabled=True)
    storage.index.dsn = SecretStr("   ")
    assert mod._default_index_factory(storage) is None


def test_dsn_builds_asyncpg_index_without_importing_asyncpg(monkeypatch: pytest.MonkeyPatch, caplog) -> None:
    monkeypatch.setitem(sys.modules, "asyncpg", None)
    storage = StorageSettings(enabled=True)
    storage.index.dsn = SecretStr("postgresql://drawing:hunter2@db.internal:5432/haruki")
    with caplog.at_level(logging.INFO, logger="src.artifact.runtime"):
        index = mod._default_index_factory(storage)
    assert type(index).__name__ == "AsyncpgRenderIndex"
    assert "hunter2" not in caplog.text
    assert "drawing@db.internal:5432/haruki" in caplog.text


def test_default_store_factory_validates_and_builds(monkeypatch: pytest.MonkeyPatch) -> None:
    storage = StorageSettings(enabled=True)
    storage.provider.bucket = ""
    with pytest.raises(ValueError, match="bucket"):
        mod._default_store_factory(storage)

    captured = {}

    from src.storage import opendal_store

    def fake_from_provider(provider, **kwargs):
        captured.update(kwargs)
        return FakeObjectStore(bucket="image-cache")

    monkeypatch.setattr(opendal_store.OpendalObjectStore, "from_provider", staticmethod(fake_from_provider))
    storage = StorageSettings(enabled=True)
    store = mod._default_store_factory(storage)
    assert store.bucket == "image-cache"
    assert captured["region"] is None
    assert captured["name"] == "artifact-store"
    assert captured["timeout"] == storage.upload_timeout_seconds
    assert captured["concurrency"] == storage.upload_concurrency


def test_bad_provider_is_unavailable_and_retried_after_window(monkeypatch, caplog) -> None:
    now = [100.0]
    monkeypatch.setattr(mod, "_clock", lambda: now[0])
    from src.settings import settings

    monkeypatch.setattr(settings.storage, "enabled", True)
    monkeypatch.setattr(settings.storage.provider, "bucket", "")
    monkeypatch.setattr(settings.storage.index, "connect_retry_seconds", 30.0)
    with caplog.at_level(logging.ERROR, logger="src.artifact.runtime"):
        runtime = mod.get_artifact_runtime()
    assert not runtime.enabled
    assert runtime.reason == "runtime_unavailable"
    assert "artifact.runtime_unavailable" in caplog.text
    outcome = asyncio.run(runtime.process(_payload(), _directive()))
    assert outcome.reason == "runtime_unavailable"

    now[0] = 129.0
    assert mod.get_artifact_runtime() is runtime  # still inside the window
    now[0] = 131.0
    rebuilt = mod.get_artifact_runtime()
    assert rebuilt is not runtime
    assert rebuilt.reason == "runtime_unavailable"


def test_opendal_import_failure_is_unavailable(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "opendal", None)
    runtime = mod.build_artifact_runtime(_settings(enabled=True), stats=ArtifactStats())
    assert runtime.reason == "runtime_unavailable"
    assert runtime.retry_at is not None


def test_non_empty_root_warns(caplog: pytest.LogCaptureFixture) -> None:
    settings = _settings(enabled=True)
    settings.storage.provider.root = "nested"
    with caplog.at_level(logging.WARNING, logger="src.artifact.runtime"):
        runtime = mod.build_artifact_runtime(
            settings, store_factory=lambda _s: FakeObjectStore(bucket="image-cache"), stats=ArtifactStats()
        )
    assert runtime.enabled
    assert "root='nested' is ignored" in caplog.text


def test_index_factory_failure_keeps_uploads(caplog: pytest.LogCaptureFixture) -> None:
    def broken(_s):
        raise RuntimeError("bad dsn")

    with caplog.at_level(logging.ERROR, logger="src.artifact.runtime"):
        runtime = mod.build_artifact_runtime(
            _settings(enabled=True),
            store_factory=lambda _s: FakeObjectStore(bucket="image-cache"),
            index_factory=broken,
            stats=ArtifactStats(),
        )
    assert runtime.enabled
    assert runtime.index is None
    assert "artifact index construction failed" in caplog.text


def test_startup_preflight_ok_and_failure_and_idempotent(caplog: pytest.LogCaptureFixture) -> None:
    stats = ArtifactStats()
    index = FakeRenderIndex()
    runtime = build_test_runtime(index=index, stats=stats)
    mod.set_artifact_runtime(runtime)
    asyncio.run(mod.startup_artifact_runtime())
    asyncio.run(mod.startup_artifact_runtime())
    assert [c for c, _ in index.calls] == ["preflight", "preflight"]
    assert stats.snapshot()["index"]["usable"] is True

    failing = FakeRenderIndex(preflight_error=RuntimeError("UndefinedTableError"))
    mod.set_artifact_runtime(build_test_runtime(index=failing, stats=stats))
    with caplog.at_level(logging.WARNING, logger="src.artifact.runtime"):
        asyncio.run(mod.startup_artifact_runtime())
    snap = stats.snapshot()["index"]
    assert snap["usable"] is False
    assert snap["last_error"]["stage"] == "preflight"
    assert "preflight failed at startup" in caplog.text


def test_startup_preflight_is_capped(monkeypatch: pytest.MonkeyPatch) -> None:
    class Slow(FakeRenderIndex):
        async def preflight(self) -> None:
            await asyncio.sleep(10)

    monkeypatch.setattr(mod, "STARTUP_PREFLIGHT_TIMEOUT_SECONDS", 0.01)
    stats = ArtifactStats()
    mod.set_artifact_runtime(build_test_runtime(index=Slow(), stats=stats))
    asyncio.run(mod.startup_artifact_runtime())
    assert stats.snapshot()["index"]["usable"] is False


def test_startup_without_index_or_disabled_is_noop(monkeypatch: pytest.MonkeyPatch) -> None:
    mod.set_artifact_runtime(build_test_runtime(stats=ArtifactStats()))
    asyncio.run(mod.startup_artifact_runtime())
    mod.set_artifact_runtime(mod.ArtifactRuntime(reason="disabled"))
    asyncio.run(mod.startup_artifact_runtime())

    def broken():
        raise RuntimeError("accessor bug")

    monkeypatch.setattr(mod, "get_artifact_runtime", broken)
    asyncio.run(mod.startup_artifact_runtime())  # never raises


def test_shutdown_is_idempotent_and_never_raises(caplog: pytest.LogCaptureFixture) -> None:
    asyncio.run(mod.shutdown_artifact_runtime())  # nothing installed: builds nothing

    class BrokenStore(FakeObjectStore):
        async def close(self) -> None:
            raise RuntimeError("close failed")

    index = FakeRenderIndex()
    runtime = build_test_runtime(store=BrokenStore(bucket="image-cache"), index=index, stats=ArtifactStats())
    mod.set_artifact_runtime(runtime)
    with caplog.at_level(logging.WARNING, logger="src.artifact.runtime"):
        asyncio.run(mod.shutdown_artifact_runtime())
        asyncio.run(mod.shutdown_artifact_runtime())
    assert runtime.closed
    assert index.closed
    assert [c for c, _ in index.calls].count("close") == 1
    assert "artifact.store_close_failed" in caplog.text


def test_shutdown_guards_runtime_close_bug(caplog: pytest.LogCaptureFixture) -> None:
    class Exploding(mod.ArtifactRuntime):
        async def close(self) -> None:
            raise RuntimeError("bug")

    mod.set_artifact_runtime(Exploding(reason="disabled"))
    with caplog.at_level(logging.WARNING, logger="src.artifact.runtime"):
        asyncio.run(mod.shutdown_artifact_runtime())
    assert "artifact.shutdown_failed" in caplog.text
