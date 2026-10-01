#!/usr/bin/env bash
# pjsekai-scores-rs-skia-image bundles an auditwheel FreeType that is too old; point it at the
# system one (same step as in quick-check.yml / free-threaded-smoke.yml / sonar.yml).
# Usage: scripts/ci/repair-freetype.sh [venv]   (default .venv)
set -euo pipefail
venv="${1:-.venv}"
system_freetype="$(ldconfig -p | awk '/libfreetype\.so\.6 / { print $NF; exit }')"
bundled_freetype="$(find "$venv/lib" -path '*/pjsekai_scores_rs_skia_image.libs/libfreetype-*.so.6' -print -quit)"
if [ -n "$bundled_freetype" ]; then
  test -n "$system_freetype"
  rm "$bundled_freetype"
  ln -s "$system_freetype" "$bundled_freetype"
fi
"$venv/bin/python" -c "import pjsekai_scores_rs; from pjsekai_scores_rs import Drawing; print(Drawing.jpg)"
if "$venv/bin/python" -c "import haruki_skia_renderer" 2>/dev/null; then
  "$venv/bin/python" -c "import haruki_skia_renderer as m; from src.sekai.skia_renderer.canvas import REQUIRED_NATIVE_IR_CAPABILITY as r; assert m.IR_CAPABILITY >= r, m.IR_CAPABILITY; print('IR_CAPABILITY =', m.IR_CAPABILITY)"
fi
