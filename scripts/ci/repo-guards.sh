#!/usr/bin/env bash
# "Validate configuration and repository guards" + "Validate docker compose config"
# from quick-check.yml. Runs in the synced project venv.
set -euo pipefail
cd "$(dirname "$0")/../.."
if git ls-files --error-unmatch src/sekai/mysekai/drawer.real.py >/dev/null 2>&1; then
  echo "::error file=src/sekai/mysekai/drawer.real.py::drawer.real.py must stay untracked"
  exit 1
fi
uv run --no-sync --no-build python - <<'PY'
from pathlib import Path

from src.settings import Settings

for config_path in (Path("configs.yaml"), Path("configs.docker.yaml")):
    settings = Settings.from_yaml(config_path)
    assert settings.drawing.export_image_format in {"png", "jpg"}, config_path
    assert 1 <= settings.drawing.jpg_quality <= 100, config_path
print("configuration validation passed")
PY
docker compose config >/dev/null
