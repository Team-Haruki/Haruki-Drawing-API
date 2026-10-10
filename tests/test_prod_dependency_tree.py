"""Pins the production dependency boundary: storage wheels in; legacy renderer, libpq drivers and dev tools out."""

from pathlib import Path
import re
import tomllib

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
PROJECT_NAME = "haruki-drawing-api"
FORBIDDEN = ("pillow", "matplotlib", "pilmoji", "psycopg", "ruff")
STORAGE_PACKAGES = ("opendal", "asyncpg")


def _requirement_name(requirement: str) -> str:
    return re.split(r"[\s<>=!~;\[(]", requirement.strip(), maxsplit=1)[0].lower().replace("_", "-")


def _lock_packages() -> dict[str, dict]:
    lock = tomllib.loads((REPOSITORY_ROOT / "uv.lock").read_text(encoding="utf-8"))
    return {package["name"]: package for package in lock["package"]}


def _production_closure(packages: dict[str, dict]) -> set[str]:
    seen: set[str] = set()
    stack = [dep["name"] for dep in packages[PROJECT_NAME].get("dependencies", [])]
    while stack:
        name = stack.pop()
        if name in seen:
            continue
        seen.add(name)
        stack.extend(dep["name"] for dep in packages[name].get("dependencies", []))
    return seen


def test_project_dependencies_include_storage_and_exclude_legacy():
    pyproject = tomllib.loads((REPOSITORY_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    names = {_requirement_name(requirement) for requirement in pyproject["project"]["dependencies"]}

    for package in STORAGE_PACKAGES:
        assert package in names
    for package in FORBIDDEN:
        assert not any(name.startswith(package) for name in names), package


def test_locked_production_closure_excludes_legacy_renderer():
    closure = _production_closure(_lock_packages())

    assert set(STORAGE_PACKAGES) <= closure
    for package in FORBIDDEN:
        assert not any(name.startswith(package) for name in closure), package


def test_lock_has_free_threaded_manylinux_wheels_for_storage_packages():
    packages = _lock_packages()

    for name in STORAGE_PACKAGES:
        urls = [wheel["url"] for wheel in packages[name].get("wheels", [])]
        cp314t = [url for url in urls if "-cp314-cp314t-manylinux" in url]
        assert any("x86_64" in url for url in cp314t), name
        assert any("aarch64" in url for url in cp314t), name


def test_dockerfile_self_check_imports_storage_packages():
    dockerfile = (REPOSITORY_ROOT / "Dockerfile").read_text(encoding="utf-8")
    self_check = dockerfile[dockerfile.index("<<'PYTHON'") : dockerfile.index("\nPYTHON\n")]

    assert "import opendal" in self_check
    assert "import asyncpg" in self_check
    # the GIL assertion must be re-checked after the extension imports, which could re-enable it
    assert self_check.rindex("assert not sys._is_gil_enabled()") > self_check.index("import asyncpg")


def test_free_threaded_smoke_asserts_storage_imports():
    workflow = (REPOSITORY_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    smoke = (REPOSITORY_ROOT / "scripts/ci/free-threaded-smoke.sh").read_text(encoding="utf-8")

    assert "./scripts/ci/free-threaded-smoke.sh" in workflow
    assert '-X gil=0 -W error::RuntimeWarning -c "import opendal, asyncpg; import src.core.main"' in smoke


def test_dockerfile_layers_keep_version_bumps_small():
    dockerfile = (REPOSITORY_ROOT / "Dockerfile").read_text(encoding="utf-8")
    runtime = dockerfile[dockerfile.index("FROM base AS runtime") :]

    # third-party wheels come from the lock without the project, so a version bump leaves that layer alone
    assert "--no-emit-project" in dockerfile
    # bottom to top: interpreter, third-party wheels, renderer wheel, app code
    order = [
        "COPY --from=python /opt/uv/python",
        "COPY --from=deps",
        "COPY --from=native",
        "COPY --exclude=docker . .",
    ]
    positions = [runtime.index(step) for step in order]
    assert positions == sorted(positions)
    # the self-checks must not leave __pycache__ in the (per-release) top layers
    checks = [line for line in runtime.splitlines() if line.startswith("RUN ") and "/.venv/bin/python" in line]
    assert checks
    assert all(line.startswith("RUN PYTHONDONTWRITEBYTECODE=1 ") for line in checks)


def test_dockerfile_runtime_packages_exclude_unused_libraries():
    dockerfile = (REPOSITORY_ROOT / "Dockerfile").read_text(encoding="utf-8")
    base = dockerfile[dockerfile.index("FROM debian:trixie-slim AS base") : dockerfile.index("AS python")]
    packages = set(re.findall(r"^\s+([a-z0-9][a-z0-9.+-]+) \\$", base, flags=re.MULTILINE))

    assert {"libfreetype6", "fontconfig", "ttf-wqy-zenhei", "libstdc++6", "libexpat1", "zlib1g"} <= packages
    for unused in ("libgl1", "libglib2.0-0", "libsm6", "libxext6", "libxrender1", "fonts-noto-color-emoji", "openntpd"):
        assert unused not in packages, unused
