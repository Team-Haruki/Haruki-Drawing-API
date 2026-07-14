"""Every module that `scripts/` imports must actually be installed.

This exists because of a real break. `aiohttp` was removed from pyproject.toml on the grounds that
"its only user was the dead screenshot helper" -- which was true of `src/`, and only of `src/`.
`scripts/concurrent_fetch_images.py` imports it, the free-threaded-smoke workflow runs that script,
and CI went red while every local gate stayed green: pytest never imports the scripts, and
`compileall` compiles them without importing anything.

So the local gates could not see a missing script dependency at all. This closes that.

It checks imports resolve, not that the scripts run -- most of them need a server, a payload
directory or a built native extension. That is enough: a dependency dropped from pyproject.toml
fails here immediately.
"""

from __future__ import annotations

import ast
import importlib.util
from pathlib import Path
import sys

import pytest

SCRIPTS = sorted((Path(__file__).resolve().parents[1] / "scripts").rglob("*.py"))


def _toplevel_imports(path: Path) -> set[str]:
    """The root module name of every import at the top level of the file."""
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:  # skip relative imports
                roots.add(node.module.split(".")[0])
    return roots


def _is_sibling_module(script: Path, name: str) -> bool:
    """A script run directly has its own directory on sys.path, so `import common` resolves to
    scripts/parity_payloads/common.py. That is not a missing dependency."""
    return (script.parent / f"{name}.py").exists() or (script.parent / name / "__init__.py").exists()


@pytest.mark.parametrize("script", SCRIPTS, ids=lambda p: p.name)
def test_every_script_import_is_installed(script: Path):
    missing = sorted(
        name
        for name in _toplevel_imports(script)
        # `src` and `scripts` are the repo itself; stdlib, siblings and installed packages must resolve.
        if name not in ("src", "scripts")
        and name not in sys.stdlib_module_names
        and not _is_sibling_module(script, name)
        and importlib.util.find_spec(name) is None
    )
    assert not missing, (
        f"scripts/{script.name} imports {missing}, which is not installed. "
        "A dependency was dropped from pyproject.toml without checking scripts/ — "
        "pytest never imports these files and compileall does not resolve their imports, "
        "so nothing else here would have caught it (CI would, loudly)."
    )
