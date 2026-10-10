#!/usr/bin/env bash
# Import-check the two native extensions in a venv: the chart extension (pjsekai-scores-rs,
# pure Rust since 0.6.0, so there is no bundled FreeType left to repair) and, when installed,
# the Skia renderer against REQUIRED_NATIVE_IR_CAPABILITY.
# Usage: scripts/ci/check-native-wheels.sh [venv]   (default .venv)
set -euo pipefail
venv="${1:-.venv}"
"$venv/bin/python" -c "import pjsekai_scores_rs; from pjsekai_scores_rs import Drawing; print(pjsekai_scores_rs.RASTER_BACKEND, Drawing.jpg)"
if "$venv/bin/python" -c "import haruki_skia_renderer" 2>/dev/null; then
  "$venv/bin/python" -c "import haruki_skia_renderer as m; from src.sekai.skia_renderer.canvas import REQUIRED_NATIVE_IR_CAPABILITY as r; assert m.IR_CAPABILITY >= r, m.IR_CAPABILITY; print('IR_CAPABILITY =', m.IR_CAPABILITY)"
fi
