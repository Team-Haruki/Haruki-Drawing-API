"""Small, explicit path and command-boundary validators for developer CLIs."""

from __future__ import annotations

from pathlib import Path
import re
import tempfile

_SAFE_GIT_REF = re.compile(r"[A-Za-z0-9][A-Za-z0-9._/-]*\Z")
_REPO_ROOT = Path(__file__).resolve().parents[2]


def cli_path_roots() -> tuple[Path, ...]:
    """Return directories in which repository CLIs may intentionally read or write."""

    return (Path.cwd(), Path.home(), Path(tempfile.gettempdir()))


def resolve_cli_path(path: str | Path, *, must_exist: bool = False) -> Path:
    """Resolve a CLI path and reject traversal outside the explicit developer roots."""

    candidate = Path(path).expanduser().resolve(strict=must_exist)
    roots = tuple(root.expanduser().resolve() for root in cli_path_roots())
    if not any(candidate == root or candidate.is_relative_to(root) for root in roots):
        raise ValueError(f"path is outside the permitted CLI roots: {candidate}")
    return candidate


def write_cli_text(path: str | Path, contents: str, *, encoding: str = "utf-8") -> Path:
    """Write text below the repository or temporary directory.

    Writes deliberately use a narrower boundary than reads: a developer may read
    assets elsewhere below their home directory, but a faulty CLI argument must
    not be able to overwrite an arbitrary home-directory file.
    """

    destination = Path(path).expanduser().resolve()
    write_roots = (_REPO_ROOT, Path(tempfile.gettempdir()).resolve())
    if not any(destination == root or destination.is_relative_to(root) for root in write_roots):
        raise ValueError(f"path is outside the permitted CLI write roots: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(contents, encoding=encoding)
    return destination


def validate_git_ref(ref: str) -> str:
    """Reject ref strings that can be parsed as options or revision expressions."""

    if (
        not _SAFE_GIT_REF.fullmatch(ref)
        or ref.startswith("-")
        or ref.endswith((".", "/"))
        or ".." in ref
        or "//" in ref
        or "@{" in ref
    ):
        raise ValueError(f"unsafe git ref: {ref!r}")
    return ref
