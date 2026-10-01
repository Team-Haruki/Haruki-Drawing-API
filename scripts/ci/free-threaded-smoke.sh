#!/usr/bin/env bash
# The checks of free-threaded-smoke.yml after the renderer build: no-GIL imports, storage
# imports under -W error, compileall, three bytes-mode granian load runs (the gate), the
# optional artifact-mode degradation run, and the GIL vs no-GIL benchmark.
# Expects .venv with the renderer wheel installed and fonts in data/.
# Usage: scripts/ci/free-threaded-smoke.sh [log-dir]   (default smoke-logs)
set -euo pipefail
cd "$(dirname "$0")/../.."
logs="${1:-smoke-logs}"; mkdir -p "$logs" out
py=.venv/bin/python
export HARUKI_FONT__EMOJI=TwemojiMozilla
trap 'cp -f /tmp/haruki_granian*.log "$logs"/ 2>/dev/null || true; cp -R out "$logs"/ 2>/dev/null || true' EXIT

"$py" -X gil=0 - <<'PY'
import sys
assert hasattr(sys, "_is_gil_enabled"), "Python does not expose _is_gil_enabled"
assert sys._is_gil_enabled() is False, "GIL is still enabled"
import src.core.main
import src.sekai.sk.drawer
print("free-threaded import smoke passed")
PY
# opendal/asyncpg without Py_MOD_GIL_NOT_USED would re-enable the GIL with a RuntimeWarning.
"$py" -X gil=0 -W error::RuntimeWarning -c "import opendal, asyncpg; import src.core.main"
"$py" -m compileall -q src
"$py" scripts/generate_sk_trend_sample.py --output-dir out/ci-sk-trend --points 20

boot() { # port log [env...]
  local port="$1" log="$2"; shift 2
  env "$@" "$py" -X gil=0 -m granian --interface asgi --host 127.0.0.1 --port "$port" src.core.main:app > "$log" 2>&1 &
  server_pid=$!
  for _ in $(seq 1 60); do curl -fsS "http://127.0.0.1:$port/health" >/dev/null 2>&1 && return 0; sleep 0.25; done
  curl -fsS "http://127.0.0.1:$port/health" >/dev/null || { cat "$log"; return 1; }
}
load() { # port endpoint payload outdir [extra args...]
  local port="$1" endpoint="$2" payload="$3" outdir="$4"; shift 4
  "$py" scripts/concurrent_fetch_images.py --base-url "http://127.0.0.1:$port" --endpoint "$endpoint" \
    --payload-file "$payload" --requests 20 --concurrency 4 --timeout 60 --output-dir "$outdir" --save-errors "$@"
}

boot 18080 /tmp/haruki_granian.log
load 18080 /api/pjsk/sk/player-trace out/ci-sk-trend/sk_player_trace_payload.json out/ci-sk-load-player-trace
load 18080 /api/pjsk/sk/query out/ci-sk-trend/sk_query_payload.json out/ci-sk-load-query
load 18080 /api/pjsk/honor/ out/ci-sk-trend/honor_payload.json out/ci-sk-load-honor
kill "$server_pid" 2>/dev/null || true
"$py" - <<'PY'
import json
for path in ("out/ci-sk-load-player-trace/summary.json", "out/ci-sk-load-query/summary.json", "out/ci-sk-load-honor/summary.json"):
    summary = json.load(open(path, encoding="utf-8"))
    assert summary["ok_images"] == summary["requests"], summary
print("nogil concurrency smoke passed")
PY

# Optional (continue-on-error before): artifact runtime on an in-memory store without PostgreSQL.
if ( boot 18081 /tmp/haruki_granian_artifact.log HARUKI_STORAGE__ENABLED=true \
       HARUKI_STORAGE__PROVIDER__SCHEME=memory HARUKI_STORAGE__NODE_NAME=ci-smoke &&
     load 18081 /api/pjsk/honor/ out/ci-sk-trend/honor_payload.json out/ci-artifact-honor --expect image \
       --header X-Haruki-Artifact:1 --header X-Haruki-Cache-Key:0123456789abcdef \
       --header X-Haruki-Cache-Key-Version:3 --header X-Haruki-Cache-TTL:600 \
       --header X-Haruki-Api-Path:api/pjsk/honor &&
     "$py" -c 'import json; s = json.load(open("out/ci-artifact-honor/summary.json")); assert s["expect"] == "image" and s["ok_images"] == s["requests"], s'
     rc=$?; kill "$server_pid" 2>/dev/null || true; exit $rc ); then
  echo "artifact missing-index bytes smoke passed"
else
  echo "::warning::artifact missing-index degradation smoke failed (non-blocking)"
fi

PYTHONPATH=. "$py" -X gil=1 scripts/benchmark_gil_compare.py --output out/bench-gil.json
PYTHONPATH=. "$py" -X gil=0 scripts/benchmark_gil_compare.py --output out/bench-nogil.json
"$py" - <<'PY'
import json
gil = json.load(open("out/bench-gil.json", encoding="utf-8"))
nogil = json.load(open("out/bench-nogil.json", encoding="utf-8"))
def case(report, section, name):
    return next(c for c in report[section] if c["name"] == name)
for section, names in (("sk", ("sk_c2", "sk_c8")), ("trace", ("trace_c2", "trace_c4"))):
    for name in names:
        g, n = case(gil, section, name), case(nogil, section, name)
        assert g["fail"] == 0 and n["fail"] == 0, (section, name, g, n)
        ratio = n["throughput_rps"] / g["throughput_rps"] if g["throughput_rps"] else 0.0
        print(f" - {section}/{name}: throughput {g['throughput_rps']} -> {n['throughput_rps']} ({ratio:.2f}x)")
PY
