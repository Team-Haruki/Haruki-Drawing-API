from pathlib import Path

import pytest

from src.core.path_safety import resolve_cli_path, validate_git_ref, write_cli_text


def test_resolve_cli_path_accepts_current_tree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "nested" / "result.json"
    assert resolve_cli_path(target) == target.resolve()


def test_resolve_cli_path_rejects_outside_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: tmp_path / "home"))
    with pytest.raises(ValueError, match="outside the permitted CLI roots"):
        resolve_cli_path("/etc/passwd", must_exist=True)


@pytest.mark.parametrize("ref", ["main", "release/v3.0.4", "eb0e4190", "HEAD"])
def test_validate_git_ref_accepts_plain_refs(ref: str) -> None:
    assert validate_git_ref(ref) == ref


@pytest.mark.parametrize("ref", ["--help", "main..evil", "refs/heads/x@{1}", "bad ref", "topic/"])
def test_validate_git_ref_rejects_revision_or_option_syntax(ref: str) -> None:
    with pytest.raises(ValueError, match="unsafe git ref"):
        validate_git_ref(ref)


def test_write_cli_text_validates_and_creates_parent(tmp_path: Path) -> None:
    destination = tmp_path / "nested" / "result.txt"

    assert write_cli_text(destination, "safe") == destination
    assert destination.read_text(encoding="utf-8") == "safe"


def test_write_cli_text_rejects_outside_write_roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)

    with pytest.raises(ValueError, match="outside the permitted CLI write roots"):
        write_cli_text("/etc/haruki-unsafe.txt", "unsafe")
