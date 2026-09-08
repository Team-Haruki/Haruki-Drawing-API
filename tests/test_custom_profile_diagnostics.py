from __future__ import annotations

import json
import os
from pathlib import Path
import time

import pytest

from src.core.debug import pop_request_context, push_request_context
from src.sekai.profile.custom_profile.diagnostics import (
    capture_safe_exception,
    cleanup_custom_profile_diagnostics,
    persist_custom_profile_diagnostic,
)
from src.sekai.profile.custom_profile.skia import CustomProfileSceneReport, CustomProfileSkiaAttempt
from src.sekai.skia_renderer.render_stats import OUTCOME_ERROR, OUTCOME_FALLBACK, reset_render_stats
from src.settings import settings


@pytest.fixture(autouse=True)
def _reset_stats():
    tokens = push_request_context("test", "/api/pjsk/profile/custom-profile-card", "POST")
    reset_render_stats()
    try:
        yield
    finally:
        reset_render_stats()
        pop_request_context(tokens)


@pytest.fixture
def diagnostic_dir(tmp_path: Path, monkeypatch) -> Path:
    directory = tmp_path / "diagnostics"
    monkeypatch.setattr(settings.drawing, "custom_profile_diagnostic_dir", directory)
    monkeypatch.setattr(settings.drawing, "custom_profile_diagnostic_retention_hours", 168)
    monkeypatch.setattr(settings.drawing, "custom_profile_diagnostic_max_files", 16)
    return directory


def _captured_secret_exception() -> dict:
    try:
        raise RuntimeError("private-user-value /pjskdata/Data/asset/secret.png https://signed.example")
    except RuntimeError as exc:
        return capture_safe_exception(exc)


def _records(directory: Path) -> list[Path]:
    return sorted(directory.glob("custom-profile-diagnostic-*.json"))


def test_persisted_exception_is_owner_only_and_request_free(diagnostic_dir: Path):
    exception = _captured_secret_exception()
    scene = {
        "complete": True,
        "visible_elements": 3,
        "native_elements": 3,
        "issues_by_kind": {},
        "classifications_by_kind": {"general": {"native": 3}},
        "request": {"must": "not survive"},
    }

    assert persist_custom_profile_diagnostic(
        outcome="error",
        stage="native_render",
        error_type="RuntimeError",
        exception=exception,
        scene_metrics=scene,
    )

    paths = _records(diagnostic_dir)
    assert len(paths) == 1
    assert os.stat(diagnostic_dir).st_mode & 0o777 == 0o700
    assert os.stat(paths[0]).st_mode & 0o777 == 0o600
    raw = paths[0].read_text()
    assert "private-user-value" not in raw
    assert "/pjskdata/" not in raw
    assert "signed.example" not in raw
    assert '"request"' not in raw

    record = json.loads(raw)
    assert record["stage"] == "native_render"
    assert record["error_type"] == "RuntimeError"
    assert record["final_http_class"] == "unknown"
    assert record["exception"]["exception_chain"][0]["type"] == "RuntimeError"
    assert "message_sha256" not in record["exception"]["exception_chain"][0]
    assert "message_length" not in record["exception"]["exception_chain"][0]
    assert record["exception"]["exception_chain"][0]["frames"]
    assert record["scene"]["classifications_by_kind"] == {"general": {"native": 3}}


def test_persistence_reapplies_whitelist_to_injected_exception_fields(diagnostic_dir: Path):
    injected = {
        "exception_chain": [
            {
                "relation": "raised",
                "type": "RuntimeError",
                "message_sha256": "a" * 64,
                "message_length": 10,
                "message": "private-user-value",
                "frames": [
                    {
                        "module": "src.sekai.profile.custom_profile.skia",
                        "function": "_render",
                        "line": 42,
                        "file": "/private/request/secret-card.json",
                        "locals": {"signed_url": "https://signed.example"},
                    }
                ],
            }
        ],
        "request": {"user": "private-user-value"},
    }

    assert persist_custom_profile_diagnostic(
        outcome="error",
        stage="native_render",
        error_type="RuntimeError",
        exception=injected,
        final_http_status=200,
    )

    raw = _records(diagnostic_dir)[0].read_text()
    assert "secret-card.json" not in raw
    assert "/private/request" not in raw
    assert "private-user-value" not in raw
    assert "signed.example" not in raw
    record = json.loads(raw)
    assert record["final_http_class"] == "2xx"
    assert record["exception"]["exception_chain"][0]["frames"] == [
        {"function": "_render", "line": 42, "module": "src.sekai.profile.custom_profile.skia"}
    ]


def test_attempt_persists_committed_error_once_but_not_rejected_error(diagnostic_dir: Path):
    committed = CustomProfileSkiaAttempt(
        None,
        OUTCOME_ERROR,
        error_stage="native_render",
        error_type="RuntimeError",
        exception_diagnostic=_captured_secret_exception(),
    )
    committed.record(200)
    committed.record()
    assert len(_records(diagnostic_dir)) == 1
    assert json.loads(_records(diagnostic_dir)[0].read_text())["final_http_class"] == "2xx"

    rejected = CustomProfileSkiaAttempt(
        None,
        OUTCOME_ERROR,
        error_stage="native_render",
        error_type="ValueError",
        exception_diagnostic=_captured_secret_exception(),
    )
    rejected.reject()
    assert len(_records(diagnostic_dir)) == 1


def test_incomplete_scene_fallback_persists_only_aggregate_coverage(diagnostic_dir: Path):
    report = CustomProfileSceneReport(
        elements_total=2,
        visible_elements=2,
        native_elements=1,
        unresolved_elements=1,
        classifications_by_kind={"general": {"native": 1, "unresolved": 1}},
    )
    attempt = CustomProfileSkiaAttempt(None, OUTCOME_FALLBACK, report=report)

    attempt.record()

    record = json.loads(_records(diagnostic_dir)[0].read_text())
    assert record["outcome"] == "fallback"
    assert record["stage"] == "scene_coverage"
    assert record["exception"] is None
    assert record["scene"]["unresolved_elements"] == 1
    assert record["scene"]["issues_by_kind"] == {"general": {"unresolved": 1}}


def test_retention_prunes_expired_and_excess_records(diagnostic_dir: Path, monkeypatch):
    monkeypatch.setattr(settings.drawing, "custom_profile_diagnostic_retention_hours", 1)
    monkeypatch.setattr(settings.drawing, "custom_profile_diagnostic_max_files", 2)
    diagnostic_dir.mkdir(mode=0o700)
    expired = diagnostic_dir / "custom-profile-diagnostic-expired.json"
    expired.write_text("{}")
    old = time.time() - 7200
    os.utime(expired, (old, old))

    for _ in range(3):
        assert persist_custom_profile_diagnostic(
            outcome="error",
            stage="native_render",
            error_type="RuntimeError",
            exception=_captured_secret_exception(),
        )

    assert not expired.exists()
    assert len(_records(diagnostic_dir)) == 2


def test_periodic_cleanup_prunes_without_a_followup_failure(diagnostic_dir: Path, monkeypatch):
    monkeypatch.setattr(settings.drawing, "custom_profile_diagnostic_retention_hours", 1)
    diagnostic_dir.mkdir(mode=0o700)
    expired = diagnostic_dir / "custom-profile-diagnostic-expired.json"
    expired.write_text("{}")
    old = time.time() - 7200
    os.utime(expired, (old, old))

    assert cleanup_custom_profile_diagnostics() == 1
    assert not expired.exists()


def test_persistence_is_noop_when_directory_is_disabled(monkeypatch):
    monkeypatch.setattr(settings.drawing, "custom_profile_diagnostic_dir", None)

    assert not persist_custom_profile_diagnostic(
        outcome="error",
        stage="native_render",
        error_type="RuntimeError",
        exception=_captured_secret_exception(),
    )
