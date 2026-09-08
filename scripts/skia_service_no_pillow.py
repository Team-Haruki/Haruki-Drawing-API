"""Check FastAPI startup, ASGI requests and worker processes without Pillow.

This is a service smoke check, not full corpus coverage. Pass --requests with a JSON
array of {path, payload} objects to exercise additional real routes. Each request
must produce fresh native bytes and trustworthy pure-render telemetry. A temporary
sitecustomize installs the import guard in the service and every spawned worker.
"""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import signal
import subprocess
import sys
import tempfile
import traceback

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REQUESTS = [{"path": "/api/pjsk/help/render", "payload": {"markdown": "# 帮助\n## 查询\n- 名称: Haruki 😀"}}]


def _event(kind: str, **values):
    directory = Path(os.environ["HARUKI_RETIREMENT_EVENTS"])
    with (directory / f"{os.getpid()}.jsonl").open("a") as stream:
        stream.write(json.dumps({"kind": kind, "pid": os.getpid(), **values}) + "\n")


def install_process_guard():
    """Called by sitecustomize before application imports, including in spawn children."""
    from scripts.skia_no_pillow import _NoPillow

    class Guard(_NoPillow):
        def find_spec(self, fullname, path=None, target=None):
            if fullname == "PIL" or fullname.startswith("PIL."):
                _event("pillow", module=fullname)
            return super().find_spec(fullname, path, target)

    if any(name == "PIL" or name.startswith("PIL.") for name in sys.modules):
        _event("pillow", module="already imported")
        raise RuntimeError("Pillow was loaded before service guard")
    sys.meta_path.insert(0, Guard())
    import haruki_skia_renderer as native

    render = native.render_scene

    def counted_render(*args, **kwargs):
        result = render(*args, **kwargs)
        _event("render")
        return result

    native.render_scene = counted_render
    _event("guard")


def _events(directory: Path):
    return [json.loads(line) for path in directory.glob("*.jsonl") for line in path.read_text().splitlines()]


def _worker(requests):
    import haruki_skia_renderer as native
    from starlette.testclient import TestClient

    from src.core.main import app
    from src.sekai.skia_renderer.render_stats import get_render_stats
    from src.settings import settings

    event_dir = Path(os.environ["HARUKI_RETIREMENT_EVENTS"])
    if not any(e["kind"] == "guard" and e["pid"] == os.getpid() for e in _events(event_dir)):
        raise RuntimeError("service guard was not installed")
    results = []
    with TestClient(app) as client:
        if not settings.drawing.use_skia_plot:
            raise RuntimeError("startup disabled the native renderer")
        for request in requests:
            before = get_render_stats()["totals"]
            rendered_before = sum(e["kind"] == "render" for e in _events(event_dir))
            response = client.post(request["path"], json=request["payload"])
            if response.status_code != 200:
                raise RuntimeError(f"{request['path']}: HTTP {response.status_code}: {response.text[:1000]}")
            info = native.encoded_image_info(response.content)
            if int(response.headers.get("content-length", "0")) != len(response.content):
                raise RuntimeError("response Content-Length does not match native bytes")
            after = get_render_stats()["totals"]
            delta = {
                key: after[key] - before[key]
                for key in (
                    "skia",
                    "cache_hit",
                    "fallback",
                    "disabled",
                    "error",
                    "native_pure",
                    "native_hybrid",
                    "native_unclassified",
                )
            }
            if (
                delta["skia"] < 1
                or delta["native_pure"] != delta["skia"]
                or any(
                    delta[key]
                    for key in ("cache_hit", "fallback", "disabled", "error", "native_hybrid", "native_unclassified")
                )
            ):
                raise RuntimeError(f"{request['path']}: impure or non-rendered response: {delta}")
            events = _events(event_dir)
            if any(e["kind"] == "pillow" for e in events):
                raise RuntimeError("Pillow import attempted in service or worker")
            if sum(e["kind"] == "render" for e in events) <= rendered_before:
                raise RuntimeError("request did not execute native.render_scene in service or worker")
            results.append({"path": request["path"], "size": [info["width"], info["height"]], "counts": delta})
    return {"status": "ok", "requests": results}


def run_service_check(requests=None, *, timeout=180):
    requests = DEFAULT_REQUESTS if requests is None else requests
    if not requests:
        return {"status": "blocked", "error": "at least one rendering request is required"}
    with tempfile.TemporaryDirectory(prefix="haruki-service-retirement-") as directory:
        root = Path(directory)
        events = root / "events"
        events.mkdir()
        (root / "sitecustomize.py").write_text(
            "from scripts.skia_service_no_pillow import install_process_guard\ninstall_process_guard()\n"
        )
        spec = root / "requests.json"
        output = root / "result.json"
        spec.write_text(json.dumps(requests))
        env = dict(os.environ)
        env.update(
            PYTHONPATH=os.pathsep.join((directory, str(ROOT), env.get("PYTHONPATH", ""))),
            HARUKI_RETIREMENT_EVENTS=str(events),
            HARUKI_DRAWING__USE_SKIA_PLOT="true",
            HARUKI_DRAWING__ISOLATED_WORKER_POOL_SIZE="1",
            HARUKI_DRAWING__COMPOSED_IMAGE_CACHE_SIZE="0",
            HARUKI_DRAWING__COMPOSED_IMAGE_CACHE_MAX_MB="0",
            HARUKI_DRAWING__CUSTOM_PROFILE_GLYPH_CACHE_SIZE="0",
            HARUKI_SKIA_TEXT_MASK_CACHE_MB="0",
            HARUKI_BG_TEST_HOUR="12",
        )
        process = subprocess.Popen(
            [sys.executable, "-X", "gil=0", str(Path(__file__).resolve()), "--worker", str(spec), str(output)],
            cwd=ROOT,
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            start_new_session=True,
        )
        try:
            _stdout, stderr = process.communicate(timeout=timeout)
        except subprocess.TimeoutExpired:
            os.killpg(process.pid, signal.SIGKILL)
            process.communicate()
            return {"status": "timeout", "error": f"service check exceeded {timeout}s"}
        result = json.loads(output.read_text()) if output.is_file() else {"status": "blocked", "error": stderr[-3000:]}
        audit = _events(events)
        violations = [e for e in audit if e["kind"] == "pillow"]
        if violations:
            result = {"status": "blocked", "error": "Pillow imports attempted", "violations": violations}
        elif process.returncode and result.get("status") == "ok":
            result = {"status": "blocked", "error": f"service exited {process.returncode}: {stderr[-3000:]}"}
        result["guarded_processes"] = len({e["pid"] for e in audit if e["kind"] == "guard"})
        result["render_processes"] = len({e["pid"] for e in audit if e["kind"] == "render"})
        return result


def route_for_case(case, openapi: dict) -> str:
    """Resolve the registered route from its request schema; ambiguity must fail."""
    matches = []
    for path, operations in openapi["paths"].items():
        body = operations.get("post", {}).get("requestBody", {}).get("content", {}).get("application/json", {})
        schema = body.get("schema", {})
        reference = schema.get("items", {}).get("$ref", "") if case.is_list else schema.get("$ref", "")
        if reference.rsplit("/", 1)[-1] == case.model_cls:
            matches.append(path)
    if len(matches) != 1:
        raise ValueError(f"{case.name}: expected one registered request schema, found {matches}")
    return matches[0]


def run_service_case(case, payload_path: Path):
    """Additional strict parity evidence covering the serving path of the same fixture."""
    try:
        from scripts.parity_payloads.retirement_fixture_contract import validate_retirement_branch
        from src.core.main import app

        path = route_for_case(case, app.openapi())
        raw = json.loads(payload_path.read_text())
        validate_retirement_branch(case.name, raw)
        payload = (
            (raw if isinstance(raw, list) else [raw]) if case.is_list else (raw[0] if isinstance(raw, list) else raw)
        )
        return run_service_check([{"path": path, "payload": payload}])
    except Exception:
        return {"status": "blocked", "error": traceback.format_exc()}


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        try:
            result = _worker(json.loads(Path(sys.argv[2]).read_text()))
        except Exception:
            result = {"status": "blocked", "error": traceback.format_exc()}
        Path(sys.argv[3]).write_text(json.dumps(result, ensure_ascii=False))
        return 0 if result["status"] == "ok" else 1
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--requests", type=Path, help="JSON array of {path, payload} requests")
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    result = run_service_check(json.loads(args.requests.read_text()) if args.requests else None)
    encoded = json.dumps(result, ensure_ascii=False, indent=2)
    print(encoded)  # noqa: T201
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(encoded)
    return 0 if result["status"] == "ok" else 1


if __name__ == "__main__":
    raise SystemExit(main())
