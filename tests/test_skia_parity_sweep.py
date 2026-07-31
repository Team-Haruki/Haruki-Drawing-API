from __future__ import annotations

from dataclasses import replace
import sys

import pytest

import scripts.skia_parity_sweep as sweep_mod


def _run_main(
    monkeypatch,
    tmp_path,
    rows,
    *,
    strict: bool = False,
    case=None,
    budgets=None,
    extra_fixtures: tuple[str, ...] = (),
) -> int:
    case = case or next(item for item in sweep_mod.CASES if item.name == "profile")
    budgets = {case.name: case.budget} if budgets is None else budgets
    payload_dir = tmp_path / "payloads"
    payload_dir.mkdir()
    (payload_dir / f"{case.name}.json").write_text("{}", encoding="utf-8")
    for name in extra_fixtures:
        (payload_dir / f"{name}.json").write_text("{}", encoding="utf-8")

    async def fake_sweep(_only, _out_dir, _mysekai_real):
        return rows

    monkeypatch.setattr(sweep_mod, "REPO_ROOT", tmp_path)
    monkeypatch.setattr(sweep_mod, "PAYLOAD_DIR", payload_dir)
    monkeypatch.setattr(sweep_mod, "CASES", (case,))
    monkeypatch.setattr(sweep_mod, "PARITY_BUDGETS", budgets)
    monkeypatch.setattr(sweep_mod, "setup", lambda: None)
    monkeypatch.setattr(sweep_mod, "_load_mysekai_real", lambda: None)
    monkeypatch.setattr(sweep_mod, "sweep", fake_sweep)
    argv = ["skia_parity_sweep.py", "--out-dir", str(tmp_path / "out")]
    if strict:
        argv.append("--strict")
    monkeypatch.setattr(sys, "argv", argv)

    return sweep_mod.main()


@pytest.mark.parametrize(
    ("rows", "strict", "expected_exit_code"),
    [
        ([{"endpoint": "profile", "status": "ok"}], False, 0),
        ([{"endpoint": "profile", "status": "size-mismatch"}], False, 1),
        ([{"endpoint": "profile", "status": "no-payload"}], False, 0),
        ([{"endpoint": "profile", "status": "no-payload"}], True, 1),
        ([{"endpoint": "profile", "status": "skipped"}], True, 1),
        ([{"endpoint": "profile", "status": "pillow-only"}], True, 1),
        (
            [{"endpoint": "profile", "status": "skia-none", "note": "known-blocked: fixture"}],
            False,
            0,
        ),
        (
            [{"endpoint": "profile", "status": "skia-none", "note": "known-blocked: fixture"}],
            True,
            1,
        ),
    ],
)
def test_main_exit_code_reflects_mode(monkeypatch, tmp_path, rows, strict, expected_exit_code):
    assert _run_main(monkeypatch, tmp_path, rows, strict=strict) == expected_exit_code


def test_every_case_has_exactly_one_explicit_budget():
    case_names = [case.name for case in sweep_mod.CASES]

    assert len(case_names) == len(set(case_names))
    assert set(case_names) == sweep_mod.PARITY_BUDGETS.keys()
    assert all(case.budget == sweep_mod.PARITY_BUDGETS[case.name] for case in sweep_mod.CASES)
    assert all(mean >= 0.25 and 0 <= p99 <= 255 for mean, p99 in sweep_mod.PARITY_BUDGETS.values())


def test_strict_mode_rejects_missing_budget(monkeypatch, tmp_path):
    case = next(item for item in sweep_mod.CASES if item.name == "profile")
    case = replace(case, budget=None)
    rows = [{"endpoint": case.name, "status": "ok"}]

    assert _run_main(monkeypatch, tmp_path, rows, strict=True, case=case, budgets={}) == 1


def test_strict_mode_rejects_unmapped_fixture(monkeypatch, tmp_path):
    rows = [{"endpoint": "profile", "status": "ok"}]

    assert _run_main(monkeypatch, tmp_path, rows, strict=True, extra_fixtures=("orphan",)) == 1
