"""Execute real native entrypoints in fresh processes that cannot import Pillow.

This module deliberately imports only the standard library. The parity harness can
call ``run_clean_case`` after comparing pixels; ``--strict`` additionally requires
this independent check before considering an endpoint ready for Pillow removal.
"""

from __future__ import annotations

import argparse
from dataclasses import asdict
import importlib
import importlib.abc
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import traceback

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from src.core.path_safety import resolve_cli_path


class _NoPillow(importlib.abc.MetaPathFinder):
    def __init__(self):
        self.rejected = set()

    def find_spec(self, fullname, path=None, target=None):
        if fullname == "PIL" or fullname.startswith("PIL."):
            self.rejected.add(fullname)
            raise RuntimeError(f"Pillow retirement gate rejected import: {fullname}")
        return None


def _worker(spec: dict) -> dict:
    # A fresh interpreter is essential: a finder cannot intercept an already imported
    # module, and warm caches must never conceal Python-side image/text work.
    if any(name == "PIL" or name.startswith("PIL.") for name in sys.modules):
        raise RuntimeError("Pillow was imported before the retirement gate")
    guard = _NoPillow()
    sys.meta_path.insert(0, guard)
    sys.path.insert(0, str(ROOT))
    import asyncio

    import haruki_skia_renderer as native

    renders = 0
    render_scene = native.render_scene

    def counted_render(*args, **kwargs):
        nonlocal renders
        result = render_scene(*args, **kwargs)
        renders += 1
        return result

    native.render_scene = counted_render
    case = spec["case"]
    module_name = case.get("try_render_module") or case["drawer"]
    if module_name == "mysekai-real":
        path = ROOT / "src/sekai/mysekai/drawer.real.py"
        if not path.is_file():
            raise RuntimeError("private MySekai implementation is absent")
        module_spec = importlib.util.spec_from_file_location("src.sekai.mysekai._native_retirement", path)
        module = importlib.util.module_from_spec(module_spec)
        sys.modules[module_spec.name] = module
        module_spec.loader.exec_module(module)
    else:
        module = importlib.import_module(module_name)
    model = getattr(importlib.import_module(case["model_module"]), case["model_cls"])
    raw = json.loads(resolve_cli_path(spec["payload_path"], must_exist=True).read_text())
    from scripts.parity_payloads.retirement_fixture_contract import validate_retirement_branch

    validate_retirement_branch(case["name"], raw)
    if case["is_list"]:
        request = [model.model_validate(item) for item in (raw if isinstance(raw, list) else [raw])]
    else:
        request = model.model_validate(raw[0] if isinstance(raw, list) else raw)
    if not case["try_render"]:
        raise RuntimeError("entrypoint has no native renderer")
    payload = asyncio.run(getattr(module, case["try_render"])(request))
    if guard.rejected:
        raise RuntimeError(f"Pillow imports were attempted and suppressed: {sorted(guard.rejected)}")
    if payload is None:
        raise RuntimeError("native entrypoint returned fallback")
    if renders < 1:
        raise RuntimeError("entrypoint did not successfully execute native.render_scene")
    info = native.encoded_image_info(payload.image_bytes)
    if (info["width"], info["height"]) != (payload.image_width, payload.image_height):
        raise RuntimeError("encoded result dimensions disagree with payload")
    if any(name == "PIL" or name.startswith("PIL.") for name in sys.modules):
        raise RuntimeError("Pillow was loaded during the native render")
    return {"status": "ok", "native_renders": renders, "size": [info["width"], info["height"]]}


def run_clean_case(case, payload_path: Path, *, timeout: float = 180) -> dict:
    """Run one case with imports blocked, all fragment/payload caches disabled."""
    env = dict(os.environ)
    env.update(
        HARUKI_DRAWING__USE_SKIA_PLOT="true",
        HARUKI_DRAWING__COMPOSED_IMAGE_CACHE_SIZE="0",
        HARUKI_DRAWING__COMPOSED_IMAGE_CACHE_MAX_MB="0",
        HARUKI_DRAWING__CUSTOM_PROFILE_GLYPH_CACHE_SIZE="0",
        HARUKI_SKIA_TEXT_MASK_CACHE_MB="0",
        HARUKI_BG_TEST_HOUR="12",
    )
    with tempfile.TemporaryDirectory(prefix="haruki-no-pillow-") as directory:
        spec_path = Path(directory) / "input.json"
        output_path = Path(directory) / "result.json"
        spec_path.write_text(json.dumps({"case": asdict(case), "payload_path": str(payload_path.resolve())}))
        try:
            process = subprocess.run(
                [
                    sys.executable,
                    "-X",
                    "gil=0",
                    str(Path(__file__).resolve()),
                    "--worker",
                    str(spec_path),
                    str(output_path),
                ],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return {"status": "timeout", "error": f"native render exceeded {timeout:g}s"}
        if output_path.is_file():
            result = json.loads(output_path.read_text())
            if process.returncode == 0 or result.get("status") != "ok":
                return result
        return {"status": "process-error", "error": f"exit={process.returncode}: {process.stderr[-2000:]}"}


def main():
    if len(sys.argv) > 1 and sys.argv[1] == "--worker":
        input_path = resolve_cli_path(sys.argv[2], must_exist=True)
        output_path = resolve_cli_path(sys.argv[3])
        try:
            result = _worker(json.loads(input_path.read_text()))
        except Exception as exc:
            result = {
                "status": "blocked",
                "error": f"{type(exc).__name__}: {exc}",
                "trace": traceback.format_exc(limit=-12),
            }
        with output_path.open("w", encoding="utf-8") as stream:
            json.dump(result, stream, ensure_ascii=False, indent=2)
        return 0 if result["status"] == "ok" else 1

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--only", help="development-only comma-separated endpoint names")
    parser.add_argument("--out-dir", type=Path, default=ROOT / "out/no-pillow")
    args = parser.parse_args()
    sys.path.insert(0, str(ROOT))
    from scripts.skia_parity_sweep import CASES, PAYLOAD_DIR

    selected = set(args.only.split(",")) if args.only else None
    if selected and selected - {case.name for case in CASES}:
        parser.error("--only contains unknown endpoints")
    rows = []
    for case in CASES:
        if selected and case.name not in selected:
            continue
        path = PAYLOAD_DIR / f"{case.name}.json"
        result = run_clean_case(case, path) if path.is_file() else {"status": "no-payload"}
        rows.append({"endpoint": case.name, **result})
        print(f"{case.name}: {result['status']} {result.get('error', '')}", flush=True)  # noqa: T201
    out_dir = resolve_cli_path(args.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    resolve_cli_path(out_dir / "results.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2))
    return 0 if rows and all(row["status"] == "ok" for row in rows) else 1


if __name__ == "__main__":
    raise SystemExit(main())
