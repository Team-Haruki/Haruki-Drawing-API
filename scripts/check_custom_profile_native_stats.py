"""Validate aggregate Custom Profile native-render statistics.

The command consumes a saved ``GET /render-stats`` JSON response (or stdin) and emits only
aggregate counts.  It never accepts or inspects render requests.  Use it as the strict RC soak
gate after counters have accumulated enough traffic::

    curl -fsS http://127.0.0.1:8000/render-stats | \
      uv run python scripts/check_custom_profile_native_stats.py --min-requests 100 \
        --require-zero-http-5xx
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass
import json
from pathlib import Path
import sys
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.path_safety import resolve_cli_path

ENDPOINT = "custom_profile_card"
_BAD_OUTCOMES = ("fallback", "disabled", "error")
_BAD_PURITY = ("native_hybrid", "native_unclassified")
_BAD_SCENE_COUNTS = (
    "hybrid_elements",
    "missing_elements",
    "unresolved_elements",
    "mem_images",
    "mem_bytes",
)
_BAD_CLASSIFICATIONS = ("hybrid", "missing", "unresolved")
_ERROR_STAGES = frozenset(
    {
        "renderer_init",
        "scene_build",
        "native_render",
        "payload_decode",
        "pool_dispatch",
        "unknown",
    }
)


@dataclass(frozen=True)
class _CoreCounts:
    total: int
    skia: int
    cache_hit: int
    native_pure: int


@dataclass(frozen=True)
class _SceneCounts:
    complete: int
    native: int
    noop: int


def _integer(mapping: dict[str, Any], key: str) -> int:
    value = mapping.get(key, 0)
    if isinstance(value, bool):
        raise ValueError(f"{key} must be an integer")
    try:
        parsed = int(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{key} must be an integer") from exc
    if parsed < 0:
        raise ValueError(f"{key} must not be negative")
    return parsed


def _endpoint_stats(document: dict[str, Any]) -> dict[str, Any]:
    renders = document.get("renders", document)
    if not isinstance(renders, dict):
        raise ValueError("render statistics root must be an object")
    endpoints = renders.get("endpoints")
    if not isinstance(endpoints, dict):
        raise ValueError("render statistics do not contain endpoints")
    endpoint = endpoints.get(ENDPOINT)
    if endpoint is None:
        # Render counters are created lazily.  A freshly started process therefore has no
        # Custom Profile entry yet; represent that legitimate state as zero traffic so the
        # independent aggregate HTTP 5xx gate can still be evaluated.
        return {"scene_completeness": {"classifications_by_kind": {}}}
    if not isinstance(endpoint, dict):
        raise ValueError(f"render statistics do not contain {ENDPOINT}")
    return endpoint


def _append_nonzero_failures(
    mapping: dict[str, Any],
    keys: tuple[str, ...],
    failures: list[str],
    *,
    prefix: str = "",
) -> dict[str, int]:
    values: dict[str, int] = {}
    for key in keys:
        value = _integer(mapping, key)
        values[key] = value
        if value:
            failures.append(f"{prefix}{key}={value}")
    return values


def _validate_error_stages(endpoint: dict[str, Any], error_count: int, failures: list[str]) -> None:
    raw_error_stages = endpoint.get("errors_by_stage")
    if raw_error_stages is None:
        if error_count:
            failures.append("error stage diagnostics are missing")
        return
    if not isinstance(raw_error_stages, dict):
        failures.append("error stage diagnostics are malformed")
        return

    error_stage_total = 0
    for raw_stage, raw_count in sorted(raw_error_stages.items()):
        stage = str(raw_stage or "").strip()
        if stage not in _ERROR_STAGES:
            failures.append("error stage diagnostics contain an unknown category")
            continue
        count = _integer({stage: raw_count}, stage)
        error_stage_total += count
        if count:
            failures.append(f"error stage {stage}={count}")
    if error_stage_total != error_count:
        failures.append(f"error stage total={error_stage_total} does not equal error={error_count}")


def _validate_core_counts(endpoint: dict[str, Any], min_requests: int, failures: list[str]) -> _CoreCounts:
    counts = _CoreCounts(
        total=_integer(endpoint, "total"),
        skia=_integer(endpoint, "skia"),
        cache_hit=_integer(endpoint, "cache_hit"),
        native_pure=_integer(endpoint, "native_pure"),
    )
    if counts.total < min_requests:
        failures.append(f"total requests {counts.total} is below required {min_requests}")

    outcomes = _append_nonzero_failures(endpoint, _BAD_OUTCOMES, failures)
    _validate_error_stages(endpoint, outcomes["error"], failures)
    _append_nonzero_failures(endpoint, _BAD_PURITY, failures)

    rendered = counts.skia + counts.cache_hit
    if counts.native_pure != rendered:
        failures.append(f"native_pure={counts.native_pure} does not equal skia+cache_hit={rendered}")
    if counts.total != rendered:
        failures.append(f"total={counts.total} does not equal skia+cache_hit={rendered}")
    return counts


def _validate_scene_counts(
    endpoint: dict[str, Any], total: int, failures: list[str]
) -> tuple[dict[str, Any], _SceneCounts]:
    scene = endpoint.get("scene_completeness")
    if not isinstance(scene, dict):
        failures.append("scene completeness is missing")
        scene = {}

    checked = _integer(scene, "checked")
    counts = _SceneCounts(
        complete=_integer(scene, "complete"),
        native=_integer(scene, "native_elements"),
        noop=_integer(scene, "noop_elements"),
    )
    incomplete = _integer(scene, "incomplete")
    visible = _integer(scene, "visible_elements")
    if checked != total:
        failures.append(f"scene checked={checked} does not equal total={total}")
    if counts.complete != checked or incomplete:
        failures.append(f"scene complete={counts.complete}, checked={checked}, incomplete={incomplete}")
    if visible != counts.native + counts.noop:
        failures.append(f"visible_elements={visible} does not equal native+noop={counts.native + counts.noop}")
    _append_nonzero_failures(scene, _BAD_SCENE_COUNTS, failures, prefix="scene ")
    return scene, counts


def _observed_native_kinds(scene: dict[str, Any], failures: list[str]) -> set[str]:
    raw_classifications = scene.get("classifications_by_kind")
    if not isinstance(raw_classifications, dict):
        failures.append("category classifications are missing")
        return set()

    observed: set[str] = set()
    for raw_kind, raw_counts in raw_classifications.items():
        kind = str(raw_kind or "").strip()
        if not kind or not isinstance(raw_counts, dict):
            failures.append("category classifications are malformed")
            continue
        if _integer(raw_counts, "native") > 0:
            observed.add(kind)
        _append_nonzero_failures(raw_counts, _BAD_CLASSIFICATIONS, failures, prefix=f"category {kind} ")
    return observed


def _validate_required_kinds(observed: set[str], required: set[str] | None, failures: list[str]) -> None:
    missing = sorted((required or set()) - observed)
    if missing:
        failures.append(f"required native categories not observed: {','.join(missing)}")


def _append_http_5xx(document: dict[str, Any], summary: dict[str, int], failures: list[str]) -> None:
    http_requests = document.get("http_requests")
    if not isinstance(http_requests, dict):
        failures.append("aggregate HTTP request statistics are missing")
        return
    server_errors = http_requests.get("server_errors")
    if not isinstance(server_errors, dict):
        failures.append("aggregate HTTP server-error statistics are missing")
        return
    if "total" not in server_errors:
        failures.append("aggregate HTTP server-error total is missing")
        return

    http_5xx = _integer(server_errors, "total")
    summary["http_5xx"] = http_5xx
    if http_5xx:
        failures.append(f"http_5xx={http_5xx}")


def validate_custom_profile_stats(
    document: dict[str, Any],
    *,
    min_requests: int,
    required_kinds: set[str] | None = None,
    require_zero_http_5xx: bool = False,
) -> tuple[dict[str, int], list[str]]:
    """Return a sanitized summary and strict-gate failures."""

    endpoint = _endpoint_stats(document)
    failures: list[str] = []
    core = _validate_core_counts(endpoint, min_requests, failures)
    scene, scene_counts = _validate_scene_counts(endpoint, core.total, failures)
    observed_native_kinds = _observed_native_kinds(scene, failures)
    _validate_required_kinds(observed_native_kinds, required_kinds, failures)

    summary = {
        "total": core.total,
        "skia": core.skia,
        "cache_hit": core.cache_hit,
        "native_pure": core.native_pure,
        "scene_complete": scene_counts.complete,
        "native_elements": scene_counts.native,
        "noop_elements": scene_counts.noop,
        "native_categories": len(observed_native_kinds),
    }
    if require_zero_http_5xx:
        _append_http_5xx(document, summary, failures)

    return summary, failures


def _load_document(path: str) -> dict[str, Any]:
    if path == "-":
        value = json.load(sys.stdin)
    else:
        value = json.loads(resolve_cli_path(path, must_exist=True).read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError("render statistics document must be an object")
    return value


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("input", nargs="?", default="-", help="/render-stats JSON file, or - for stdin")
    parser.add_argument("--min-requests", type=int, default=1)
    parser.add_argument("--require-kind", action="append", default=[], dest="required_kinds")
    parser.add_argument("--require-zero-http-5xx", action="store_true")
    args = parser.parse_args()
    if args.min_requests < 1:
        parser.error("--min-requests must be at least 1")

    try:
        summary, failures = validate_custom_profile_stats(
            _load_document(args.input),
            min_requests=args.min_requests,
            required_kinds=set(args.required_kinds),
            require_zero_http_5xx=args.require_zero_http_5xx,
        )
    except (OSError, json.JSONDecodeError, ValueError) as exc:
        print(f"status=invalid reason={exc}")  # noqa: T201
        return 2

    print("status=" + ("failed" if failures else "ok") + " " + " ".join(f"{k}={v}" for k, v in summary.items()))  # noqa: T201
    for failure in failures:
        print(f"failure={failure}")  # noqa: T201
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
