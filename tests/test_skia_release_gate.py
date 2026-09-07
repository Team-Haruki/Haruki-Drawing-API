from dataclasses import replace
import json
from pathlib import Path
import sys

import pytest

from scripts import skia_release_gate as gate
from scripts.skia_parity_sweep import CASES
from scripts.skia_warm_parity import strict_warm_issues


def _case(name="profile"):
    return next(case for case in CASES if case.name == name)


def _rows(name="profile"):
    return [
        {
            "endpoint": name,
            "backend": backend,
            "status": "ok",
            "cold": "hash:8x8",
            "warm_fwd": "hash:8x8",
            "warm_rev": "hash:8x8",
            "cold_after": "hash:8x8",
        }
        for backend in ("skia", "pillow")
    ]


def test_warm_release_requires_both_backends_and_real_hashes():
    rows = _rows()
    assert not strict_warm_issues(rows, (_case(),))
    assert strict_warm_issues(rows[:1], (_case(),))
    rows[0]["cold"] = None
    assert strict_warm_issues(rows, (_case(),))


@pytest.mark.parametrize("status", ["no-path", "no-payload", "skipped", "error", "CACHE-DRIFT", "nondeterministic"])
def test_warm_release_rejects_missing_or_drifting_public_content(status):
    rows = _rows()
    rows[0]["status"] = status
    assert strict_warm_issues(rows, (_case(),))


def test_warm_clock_exemption_requires_two_successful_different_cold_renders():
    case = _case("event_planner")
    rows = _rows(case.name)
    rows[0].update(status="nondeterministic", cold_after="later:8x8")
    assert not strict_warm_issues(rows, (case,))
    rows[0]["cold_after"] = None
    assert strict_warm_issues(rows, (case,))


def test_warm_release_rejects_forged_status_or_coverage():
    rows = _rows()
    rows[0]["warm_rev"] = "different:8x8"
    assert strict_warm_issues(rows, (_case(),))
    assert strict_warm_issues(_rows() + _rows()[:1], (_case(),))
    assert strict_warm_issues(_rows() + _rows("orphan"), (_case(),))


def test_warm_release_excludes_only_registered_diagnostics():
    private = _case("mysekai_map")
    assert not strict_warm_issues(_rows(), (_case(), private))
    rows = _rows()
    rows[0].update(status="error", release_required=False)
    assert strict_warm_issues(rows, (_case(),))
    assert not strict_warm_issues(rows, (replace(_case(), release_required=False),))


@pytest.mark.parametrize(
    ("exit_code", "writes_report", "expected"), [(0, True, 0), (1, True, 1), (0, False, 1), (124, False, 1)]
)
def test_release_runs_fresh_phases_and_never_accepts_missing_or_failed_evidence(
    tmp_path, monkeypatch, exit_code, writes_report, expected
):
    monkeypatch.setattr(gate.platform, "system", lambda: "Linux")
    monkeypatch.setattr(sys, "_is_gil_enabled", lambda: False)
    destination = tmp_path / "new-release"
    monkeypatch.setattr(sys, "argv", ["release-gate", "--out-dir", str(destination)])
    commands = []

    def fake_step(command, log, timeout):
        commands.append(command)
        if writes_report:
            report = Path(command[-1]) / "results.json"
            report.parent.mkdir(parents=True)
            report.write_text("{}")
        return exit_code

    monkeypatch.setattr(gate, "run_step", fake_step)
    assert gate.main() == expected
    assert len(commands) == 2
    assert all("--strict" in command for command in commands)
    assert "--backend" in commands[1]
    result = json.loads((destination / "release.json").read_text())
    assert result["passed"] is (expected == 0)
    with pytest.raises(SystemExit):
        gate.main()  # An existing successful report must never be reused.
