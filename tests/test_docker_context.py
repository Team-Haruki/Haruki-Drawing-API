from pathlib import Path

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]


def test_docker_context_excludes_runtime_data_and_private_drawer():
    patterns = {
        line.strip()
        for line in (REPOSITORY_ROOT / ".dockerignore").read_text(encoding="utf-8").splitlines()
        if line.strip() and not line.lstrip().startswith("#")
    }

    assert {
        ".env",
        "/*.json",
        ".venv/",
        "data/",
        "out/",
        "src/sekai/mysekai/drawer.real.py",
    } <= patterns
