from __future__ import annotations

import json
from types import SimpleNamespace

from PIL import Image

import scripts.skia_legacy_baseline as legacy


def _case(*, name: str = "fixture", drawer: str = "drawer"):
    return SimpleNamespace(name=name, drawer=drawer)


def test_diff_and_comparison_status_cover_size_drift_and_tolerance() -> None:
    reference = Image.new("RGBA", (2, 2), (10, 20, 30, 255))
    identical = legacy._diff(reference, reference.copy())
    assert identical == {
        "size_match": True,
        "differing_px": 0,
        "differing_pct": 0.0,
        "max_delta": 0,
        "mean_delta": 0.0,
    }
    assert legacy._comparison_status(identical, 0) == "ok"

    changed = reference.copy()
    changed.putpixel((0, 0), (13, 20, 30, 255))
    drift = legacy._diff(reference, changed)
    assert drift["differing_px"] == 1
    assert drift["differing_pct"] == 25.0
    assert drift["max_delta"] == 3
    assert legacy._comparison_status(drift, 2) == "drift"
    assert legacy._comparison_status(drift, 3) == "ok"

    mismatch = legacy._diff(reference, Image.new("RGBA", (1, 1)))
    assert mismatch == {"size_a": (2, 2), "size_b": (1, 1), "size_match": False}
    assert legacy._comparison_status(mismatch, 255) == "size-mismatch"


def test_preflight_case_classifies_missing_and_unavailable_baselines(tmp_path, monkeypatch) -> None:
    payload = tmp_path / "fixture.json"
    case = _case()
    assert legacy._preflight_case(tmp_path, case, payload) == {"case": "fixture", "status": "no-payload"}

    payload.write_text("{}", encoding="utf-8")
    mysekai = _case(drawer=legacy.MYSEKAI_REAL)
    assert legacy._preflight_case(tmp_path, mysekai, payload) == {
        "case": "fixture",
        "status": "no-baseline",
        "detail": "mysekai drawer.real.py",
    }

    monkeypatch.setattr(legacy, "_exists_on_baseline", lambda *_args: False)
    assert legacy._preflight_case(tmp_path, case, payload) == {
        "case": "fixture",
        "status": "no-baseline",
        "detail": "endpoint is new",
    }
    monkeypatch.setattr(legacy, "_exists_on_baseline", lambda *_args: True)
    assert legacy._preflight_case(tmp_path, case, payload) is None


def test_run_case_reports_baseline_current_errors_and_success(tmp_path, monkeypatch) -> None:
    payload_dir = tmp_path / "payloads"
    payload_dir.mkdir()
    (payload_dir / "fixture.json").write_text("{}", encoding="utf-8")
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    case = _case()
    monkeypatch.setattr(legacy, "PAYLOAD_DIR", payload_dir)
    monkeypatch.setattr(legacy, "_exists_on_baseline", lambda *_args: True)
    monkeypatch.setattr(legacy, "_render_baseline", lambda *_args: "baseline failed")

    assert legacy._run_case(tmp_path, case, out_dir, {}, 0) == {
        "case": "fixture",
        "status": "baseline-error",
        "detail": "baseline failed",
    }

    def render_baseline(_tree, _case_value, _payload, output, _env):
        Image.new("RGBA", (2, 2), (10, 20, 30, 255)).save(output)
        return None

    async def render_error(_case_value, _raw):
        raise ValueError("current failed")

    monkeypatch.setattr(legacy, "_render_baseline", render_baseline)
    monkeypatch.setattr(legacy, "_load_payload", lambda _name: {})
    monkeypatch.setattr(legacy, "_render_current", render_error)
    assert legacy._run_case(tmp_path, case, out_dir, {}, 0) == {
        "case": "fixture",
        "status": "current-error",
        "detail": "ValueError: current failed",
    }

    async def render_current(_case_value, _raw):
        return Image.new("RGBA", (2, 2), (11, 20, 30, 255))

    monkeypatch.setattr(legacy, "_render_current", render_current)
    row = legacy._run_case(tmp_path, case, out_dir, {}, 0)
    assert row["status"] == "drift"
    assert row["max_delta"] == 1
    assert (out_dir / "fixture_current.png").is_file()


def test_report_results_writes_summary_and_flags_drift(tmp_path, capsys) -> None:
    rows = [
        {"case": "ok", "status": "ok"},
        {"case": "drift", "status": "drift", "max_delta": 2},
        {"case": "size", "status": "size-mismatch"},
    ]
    assert legacy._report_results("HEAD", tmp_path, rows)
    saved = json.loads((tmp_path / "results.json").read_text(encoding="utf-8"))
    assert saved == {"ref": "HEAD", "cases": rows}
    output = capsys.readouterr().out
    assert "'ok': 1" in output
    assert "DRIFT vs baseline" in output

    assert not legacy._report_results("main", tmp_path, [{"case": "ok", "status": "ok"}])
