"""ArtifactStats counters and artifact_node_name() (plan §8.7)."""

from __future__ import annotations

import socket
import threading

import pytest

from src.artifact import stats as mod
from src.settings import settings


@pytest.fixture(autouse=True)
def _reset():
    mod.reset_artifact_node_name()
    yield
    mod.reset_artifact_node_name()


def test_node_name_prefers_setting_and_is_cached(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.storage, "node_name", "  cn09 ")
    assert mod.artifact_node_name() == "cn09"
    monkeypatch.setattr(settings.storage, "node_name", "cn01")
    assert mod.artifact_node_name() == "cn09"  # computed once
    mod.reset_artifact_node_name()
    assert mod.artifact_node_name() == "cn01"


def test_node_name_falls_back_to_hostname(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.storage, "node_name", "")
    monkeypatch.setattr(socket, "gethostname", lambda: "host-a")
    assert mod.artifact_node_name() == "host-a"


def test_node_name_unknown_when_hostname_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    def boom() -> str:
        raise OSError("no host")

    monkeypatch.setattr(settings.storage, "node_name", "")
    monkeypatch.setattr(socket, "gethostname", boom)
    assert mod.artifact_node_name() == "unknown"


def test_counters_and_snapshot(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(settings.storage, "node_name", "cn09")
    stats = mod.ArtifactStats()
    stats.incr("uploads")
    stats.incr("upload_bytes", 1024)
    stats.add_upload_elapsed(0.25)
    stats.add_upload_elapsed(-3)
    stats.index_skipped("schema_missing")
    stats.degraded("upload_timeout")
    stats.directive_rejected("X-Haruki-Api-Path")
    stats.directive_rejected("X-Haruki-Api-Path")
    stats.record_stage("upload", 0.5)
    stats.record_stage("upload", 0.25)
    stats.record_stage("custom", 1.0)
    stats.record_error("upload", RuntimeError("x" * 400))
    stats.set_runtime_state(enabled=True, bucket="image-cache-test")
    stats.set_index_state(configured=True, usable=False, error=("preflight", "schema missing"))

    snap = stats.snapshot()
    assert snap["enabled"] is True
    assert snap["node_name"] == "cn09"
    assert snap["bucket"] == "image-cache-test"
    assert snap["index"]["configured"] is True
    assert snap["index"]["usable"] is False
    assert snap["index"]["last_error"]["stage"] == "preflight"
    assert snap["index"]["last_error"]["exc"] == "schema missing"
    assert snap["uploads"] == 1
    assert snap["upload_bytes"] == 1024
    assert snap["upload_elapsed_total"] == 0.25
    assert snap["index_skipped"] == {"disabled": 0, "schema_missing": 1, "unavailable": 0}
    assert snap["degraded"]["upload_timeout"] == 1
    assert snap["directive_rejected"] == {"X-Haruki-Api-Path": 2}
    assert snap["stages"]["upload"] == {"count": 2, "total": 0.75}
    assert snap["stages"]["hash"] == {"count": 0, "total": 0.0}
    assert list(snap["stages"])[:4] == ["hash", "index_lookup", "upload", "index_write"]
    assert snap["stages"]["custom"]["count"] == 1
    assert snap["last_error"]["stage"] == "upload"
    assert snap["last_error"]["exc"].startswith("RuntimeError: x")
    assert len(snap["last_error"]["exc"]) == 300

    stats.set_index_state(configured=False, usable=False)
    assert stats.snapshot()["index"]["last_error"] is not None  # kept until reset

    stats.reset()
    snap = stats.snapshot()
    assert snap["enabled"] is False
    assert snap["bucket"] == settings.storage.provider.bucket
    assert snap["uploads"] == 0
    assert snap["directive_rejected"] == {}
    assert snap["last_error"] is None
    assert snap["index"] == {"configured": False, "usable": False, "last_error": None}


def test_unknown_counter_name_raises() -> None:
    with pytest.raises(KeyError):
        mod.ArtifactStats().incr("nope")


def test_module_singleton_helpers() -> None:
    mod.artifact_stats.incr("reused")
    assert mod.get_artifact_stats()["reused"] >= 1
    mod.reset_artifact_stats()
    assert mod.get_artifact_stats()["reused"] == 0


def test_thread_safe_increments() -> None:
    stats = mod.ArtifactStats()

    def work() -> None:
        for _ in range(1000):
            stats.incr("published")
            stats.directive_rejected("X-Haruki-Cache-Key")

    threads = [threading.Thread(target=work) for _ in range(8)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    snap = stats.snapshot()
    assert snap["published"] == 8000
    assert snap["directive_rejected"] == {"X-Haruki-Cache-Key": 8000}
