#!/usr/bin/env bash
# Build haruki_skia_renderer once: Rust tests (linked against libpython, as quick-check's
# native-tests job did), then one cp314t wheel with the uv-managed interpreter pinned in
# .python-version (as skia-wheels.yml did, minus the second interpreter).
# Usage: scripts/ci/build-renderer.sh [out-dir]   (default dist-renderer)
set -euo pipefail
cd "$(dirname "$0")/../.."
out="${1:-dist-renderer}"
manifest=rust/haruki_skia_renderer/Cargo.toml
python_request="$(cat .python-version)"
uv python install "$python_request"
py="$(uv python find "$python_request")"
pylib="$("$py" -c 'import sysconfig; print(sysconfig.get_config_var("LIBDIR"))')"
RUSTFLAGS="-L ${pylib} -C link-arg=-lpython3.14t" \
CARGO_TARGET_DIR=rust/haruki_skia_renderer/target-test \
  cargo test --release --manifest-path "$manifest"
maturin_version="$(awk '/^name = "maturin"$/ { getline; gsub(/version = |"/, ""); print; exit }' uv.lock)"
uvx "maturin==${maturin_version}" build --release --manifest-path "$manifest" -i "$py" --out "$out"
"$py" - "$out" <<'PY'
import glob, sys, sysconfig
wheels = glob.glob(f"{sys.argv[1]}/*.whl")
tag = "cp" + sysconfig.get_config_var("py_version_nodot") + ("t" if sysconfig.get_config_var("Py_GIL_DISABLED") else "")
assert len(wheels) == 1 and f"-{tag}-" in wheels[0], (wheels, tag)
print("wheel OK:", wheels[0])
PY
