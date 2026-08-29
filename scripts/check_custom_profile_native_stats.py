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
import json
import sys
from typing import Any

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
    total = _integer(endpoint, "total")
    skia = _integer(endpoint, "skia")
    cache_hit = _integer(endpoint, "cache_hit")
    native_pure = _integer(endpoint, "native_pure")
    if total < min_requests:
        failures.append(f"total requests {total} is below required {min_requests}")
    bad_outcomes: dict[str, int] = {}
    for key in _BAD_OUTCOMES:
        value = _integer(endpoint, key)
        bad_outcomes[key] = value
        if value:
            failures.append(f"{key}={value}")
    raw_error_stages = endpoint.get("errors_by_stage")
    if raw_error_stages is not None:
        if not isinstance(raw_error_stages, dict):
            failures.append("error stage diagnostics are malformed")
        else:
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
            if error_stage_total != bad_outcomes["error"]:
                failures.append(f"error stage total={error_stage_total} does not equal error={bad_outcomes['error']}")
    elif bad_outcomes["error"]:
        failures.append("error stage diagnostics are missing")
    for key in _BAD_PURITY:
        value = _integer(endpoint, key)
        if value:
            failures.append(f"{key}={value}")
    if native_pure != skia + cache_hit:
        failures.append(f"native_pure={native_pure} does not equal skia+cache_hit={skia + cache_hit}")
    if total != skia + cache_hit:
        failures.append(f"total={total} does not equal skia+cache_hit={skia + cache_hit}")

    scene = endpoint.get("scene_completeness")
    if not isinstance(scene, dict):
        failures.append("scene completeness is missing")
        scene = {}
    checked = _integer(scene, "checked")
    complete = _integer(scene, "complete")
    incomplete = _integer(scene, "incomplete")
    visible = _integer(scene, "visible_elements")
    native = _integer(scene, "native_elements")
    noop = _integer(scene, "noop_elements")
    if checked != total:
        failures.append(f"scene checked={checked} does not equal total={total}")
    if complete != checked or incomplete:
        failures.append(f"scene complete={complete}, checked={checked}, incomplete={incomplete}")
    if visible != native + noop:
        failures.append(f"visible_elements={visible} does not equal native+noop={native + noop}")
    for key in _BAD_SCENE_COUNTS:
        value = _integer(scene, key)
        if value:
            failures.append(f"scene {key}={value}")

    raw_classifications = scene.get("classifications_by_kind")
    if not isinstance(raw_classifications, dict):
        failures.append("category classifications are missing")
        raw_classifications = {}
    observed_native_kinds: set[str] = set()
    for raw_kind, raw_counts in raw_classifications.items():
        kind = str(raw_kind or "").strip()
        if not kind or not isinstance(raw_counts, dict):
            failures.append("category classifications are malformed")
            continue
        if _integer(raw_counts, "native") > 0:
            observed_native_kinds.add(kind)
        for status in _BAD_CLASSIFICATIONS:
            value = _integer(raw_counts, status)
            if value:
                failures.append(f"category {kind} {status}={value}")

    missing_kinds = sorted((required_kinds or set()) - observed_native_kinds)
    if missing_kinds:
        failures.append(f"required native categories not observed: {','.join(missing_kinds)}")

    summary = {
        "total": total,
        "skia": skia,
        "cache_hit": cache_hit,
        "native_pure": native_pure,
        "scene_complete": complete,
        "native_elements": native,
        "noop_elements": noop,
        "native_categories": len(observed_native_kinds),
    }
    if require_zero_http_5xx:
        http_requests = document.get("http_requests")
        if not isinstance(http_requests, dict):
            failures.append("aggregate HTTP request statistics are missing")
        else:
            server_errors = http_requests.get("server_errors")
            if not isinstance(server_errors, dict):
                failures.append("aggregate HTTP server-error statistics are missing")
            elif "total" not in server_errors:
                failures.append("aggregate HTTP server-error total is missing")
            else:
                http_5xx = _integer(server_errors, "total")
                summary["http_5xx"] = http_5xx
                if http_5xx:
                    failures.append(f"http_5xx={http_5xx}")

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
