from __future__ import annotations

from dataclasses import replace
import sys

import pytest

import scripts.skia_parity_sweep as sweep_mod


@pytest.mark.parametrize("save_images", [False, True])
def test_comparison_artifacts_preserve_alpha_and_transparent_rgb(tmp_path, monkeypatch, save_images):
    from PIL import Image

    reference = Image.new("RGBA", (2, 1))
    reference.putdata([(11, 22, 33, 0), (44, 55, 66, 127)])
    native = Image.new("RGB", (2, 1), (99, 88, 77))
    monkeypatch.setattr(sweep_mod, "SAVE_IMAGES", save_images)
    name = sweep_mod._save_sbs(tmp_path, "fixture", reference, native)
    assert (tmp_path / name).is_file()
    if save_images:
        with Image.open(tmp_path / "fixture_reference.png") as result:
            assert result.mode == "RGBA"
            assert result.tobytes() == reference.tobytes()
        with Image.open(tmp_path / "fixture_native.png") as result:
            assert result.mode == "RGBA"
            assert result.tobytes() == native.convert("RGBA").tobytes()
    else:
        assert {path.name for path in tmp_path.iterdir()} == {name}
    assert reference.getpixel((0, 0)) == (11, 22, 33, 0)
    assert native.mode == "RGB"


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
    monkeypatch.setattr(sweep_mod, "_registered_route_issues", lambda: [])
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


@pytest.mark.parametrize("check_status", ["blocked", "timeout", "process-error"])
def test_pixel_parity_cannot_pass_retirement_gate_with_pillow(monkeypatch, tmp_path, check_status):
    monkeypatch.setattr(sweep_mod, "run_clean_case", lambda *args: {"status": check_status})
    assert _run_main(monkeypatch, tmp_path, [{"endpoint": "profile", "status": "ok"}], strict=True) == 1


def test_pixel_parity_and_no_pillow_success_pass_retirement_gate(monkeypatch, tmp_path):
    monkeypatch.setattr(sweep_mod, "run_clean_case", lambda *args: {"status": "ok", "native_renders": 1})
    monkeypatch.setattr(sweep_mod, "run_service_case", lambda *args: {"status": "ok"})
    assert _run_main(monkeypatch, tmp_path, [{"endpoint": "profile", "status": "ok"}], strict=True) == 0


def test_missing_no_pillow_evidence_cannot_pass():
    assert sweep_mod._is_failure({"status": "ok"}, strict=True)


@pytest.mark.parametrize("status", ["skipped", "no-payload", "pillow-error", "ok"])
def test_private_mysekai_is_diagnostic_but_public_housing_remains_required(status):
    assert not sweep_mod._is_failure({"endpoint": "mysekai_map", "status": status}, strict=True)
    assert sweep_mod._is_failure({"endpoint": "mysekai_housing_competition", "status": status}, strict=True)


def test_report_row_cannot_opt_a_public_case_out_of_release_acceptance():
    assert sweep_mod._is_failure({"endpoint": "profile", "status": "ok", "release_required": False}, strict=True)


def test_missing_private_rows_and_budgets_do_not_block_public_release(monkeypatch):
    private = next(case for case in sweep_mod.CASES if case.name == "mysekai_map")
    public = next(case for case in sweep_mod.CASES if case.name == "profile")
    monkeypatch.setattr(sweep_mod, "CASES", (public, replace(private, budget=None)))
    monkeypatch.setattr(sweep_mod, "PARITY_BUDGETS", {public.name: public.budget})
    assert sweep_mod._strict_gate_issues([{"endpoint": public.name}], {public.name}, None) == []
    assert sweep_mod._strict_gate_issues([], set(), None) == ["CASES without result rows: ['profile']"]


def test_drawer_success_without_service_evidence_cannot_pass():
    assert sweep_mod._is_failure({"status": "ok", "no_pillow": {"status": "ok"}}, strict=True)


def test_service_failure_cannot_pass_even_when_drawer_is_pure(monkeypatch, tmp_path):
    monkeypatch.setattr(sweep_mod, "run_clean_case", lambda *args: {"status": "ok", "native_renders": 1})
    monkeypatch.setattr(sweep_mod, "run_service_case", lambda *args: {"status": "blocked"})
    assert _run_main(monkeypatch, tmp_path, [{"endpoint": "profile", "status": "ok"}], strict=True) == 1


def test_raqm_reference_cannot_pass_through_two_shared_basic_backends(monkeypatch, tmp_path):
    import json

    from PIL import ImageFont

    monkeypatch.setattr(ImageFont.core, "HAVE_RAQM", True)
    monkeypatch.setattr(sweep_mod, "run_clean_case", lambda *args: {"status": "ok", "native_renders": 1})
    monkeypatch.setattr(sweep_mod, "run_service_case", lambda *args: {"status": "ok"})
    assert _run_main(monkeypatch, tmp_path, [{"endpoint": "profile", "status": "ok"}], strict=True) == 1
    results = json.loads((tmp_path / "out/results.json").read_text())
    assert any("RAQM" in issue for issue in results["strict_issues"])


@pytest.mark.parametrize("name", ["custom_profile_card_symbol", "custom_profile_card_stamps"])
def test_uncaptured_user_excluded_branches_are_diagnostic(name):
    case = next(case for case in sweep_mod.CASES if case.name == name)
    assert not case.release_required
    assert not sweep_mod._is_failure({"endpoint": name, "status": "no-payload"}, strict=True)


def test_strict_gate_matches_the_actual_registered_drawing_routes(monkeypatch):
    assert sweep_mod._registered_route_issues() == []
    monkeypatch.setattr(sweep_mod, "CASES", tuple(case for case in sweep_mod.CASES if case.name != "profile"))
    assert sweep_mod._registered_route_issues() == ["drawing route without a registered Case: /api/pjsk/profile"]
