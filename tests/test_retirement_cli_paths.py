"""Retirement workers must validate both boundaries before running user input."""

import importlib
import sys

import pytest

from src.core import path_safety


@pytest.mark.parametrize("module_name", ["scripts.skia_no_pillow", "scripts.skia_service_no_pillow"])
@pytest.mark.parametrize("boundary", ["input", "output"])
def test_workers_reject_symlinks_outside_allowed_roots(tmp_path, monkeypatch, module_name, boundary):
    module = importlib.import_module(module_name)
    allowed = tmp_path / "allowed"
    allowed.mkdir()
    outside = tmp_path / "outside.json"
    outside.write_text("unchanged")
    source = allowed / "input.json"
    source.write_text("{}")
    destination = allowed / "result.json"
    target = source if boundary == "input" else destination
    target.unlink(missing_ok=True)
    target.symlink_to(outside)
    monkeypatch.setattr(path_safety, "cli_path_roots", lambda: (allowed,))
    monkeypatch.setattr(sys, "argv", ["gate", "--worker", str(source), str(destination)])

    def forbidden(*args):
        pytest.fail("worker executed before path validation")

    monkeypatch.setattr(module, "_worker", forbidden)
    with pytest.raises(ValueError, match="outside the permitted CLI roots"):
        module.main()
    assert outside.read_text() == "unchanged"
    if boundary == "input":
        assert not destination.exists()


@pytest.mark.parametrize("module_name", ["scripts.skia_no_pillow", "scripts.skia_service_no_pillow"])
def test_workers_write_validated_error_report(tmp_path, monkeypatch, module_name):
    module = importlib.import_module(module_name)
    source = tmp_path / "input.json"
    source.write_text("invalid json")
    destination = tmp_path / "result.json"
    monkeypatch.setattr(path_safety, "cli_path_roots", lambda: (tmp_path,))
    monkeypatch.setattr(sys, "argv", ["gate", "--worker", str(source), str(destination)])
    assert module.main() == 1
    assert '"blocked"' in destination.read_text()
