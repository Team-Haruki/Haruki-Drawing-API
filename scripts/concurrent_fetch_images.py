#!/usr/bin/env python3
"""
Concurrent image fetcher for Haruki Drawing API.

Example:
  python scripts/concurrent_fetch_images.py \
    --base-url http://127.0.0.1:8000 \
    --endpoint /api/pjsk/profile/ \
    --payload-file payloads/profile.json \
    --requests 100 \
    --concurrency 16 \
    --output-dir ./out/profile-load

Artifact mode (a Drawing node with `HARUKI_STORAGE__ENABLED=true`):
  python scripts/concurrent_fetch_images.py \
    --base-url http://127.0.0.1:8000 \
    --endpoint /api/pjsk/honor/ \
    --payload-file payloads/honor.json \
    --expect artifact \
    --fetch-cdn http://image-cache.example.internal

`--expect artifact` sends a complete render cache directive (docs/artifact-storage.md) unless the caller
overrides a header with `--header`, and counts a 200 `application/json` body whose `kind` is
`artifact_ref` as an ok image. `--fetch-cdn` additionally GETs `<base>/<cdn_path>` for every ref and
requires the bytes to match `size_bytes`.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
from dataclasses import dataclass
from datetime import datetime
import hashlib
import json
from pathlib import Path
import statistics
import sys
import time
from typing import Any

import aiohttp

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.core.path_safety import resolve_cli_path


@dataclass(slots=True)
class RequestResult:
    index: int
    status: int | None
    elapsed_ms: float
    ok: bool
    response_path: str | None
    response_bytes: int
    content_type: str | None
    error: str | None
    cdn_path: str | None = None
    reused: bool | None = None
    cdn_status: int | None = None


EXPECT_IMAGE = "image"
EXPECT_ARTIFACT = "artifact"
ARTIFACT_REF_KIND = "artifact_ref"

# The directive `--expect artifact` sends by default (plan §8.1). A `--header` with the same name wins.
DEFAULT_DIRECTIVE_TTL_SECONDS = "3600"
DEFAULT_DIRECTIVE_KEY_VERSION = "3"
DEFAULT_DIRECTIVE_GROUP = "pjsk"
DEFAULT_DIRECTIVE_USER_ID = "public"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Concurrent fetcher for image endpoints.")
    parser.add_argument("--base-url", default="http://127.0.0.1:8000", help="API base URL.")
    parser.add_argument("--endpoint", required=True, help="Endpoint path, e.g. /api/pjsk/profile/.")
    parser.add_argument("--method", default="POST", choices=("POST", "GET"), help="HTTP method.")
    parser.add_argument(
        "--payload-file",
        required=True,
        help="JSON file path. Supports a single object or a list of objects.",
    )
    parser.add_argument("--requests", type=int, default=100, help="Total request count.")
    parser.add_argument("--concurrency", type=int, default=16, help="Concurrent workers.")
    parser.add_argument("--timeout", type=float, default=30.0, help="Request timeout seconds.")
    parser.add_argument("--output-dir", default="", help="Output directory for images and reports.")
    parser.add_argument(
        "--header",
        action="append",
        default=[],
        help="Extra header in KEY=VALUE format. Can be repeated.",
    )
    parser.add_argument(
        "--save-errors",
        action="store_true",
        help="Save non-image/error responses to disk for debugging.",
    )
    parser.add_argument(
        "--insecure",
        action="store_true",
        help="Disable TLS verification for HTTPS targets.",
    )
    parser.add_argument(
        "--expect",
        default=EXPECT_IMAGE,
        choices=(EXPECT_IMAGE, EXPECT_ARTIFACT),
        help="What counts as ok: image bytes (default) or an artifact_ref JSON document.",
    )
    parser.add_argument(
        "--fetch-cdn",
        default="",
        help="With --expect artifact: GET <base>/<cdn_path> for every ref and require size_bytes to match.",
    )
    return parser.parse_args(argv)


def parse_headers(header_items: list[str]) -> dict[str, str]:
    headers: dict[str, str] = {}
    for item in header_items:
        if "=" not in item:
            raise ValueError(f"Invalid --header value: {item}. Use KEY=VALUE.")
        k, v = item.split("=", 1)
        headers[k.strip()] = v.strip()
    return headers


def load_payloads(payload_file: str) -> list[dict[str, Any]]:
    path = resolve_cli_path(payload_file, must_exist=True)
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict):
        return [raw]
    if isinstance(raw, list) and all(isinstance(x, dict) for x in raw):
        if not raw:
            raise ValueError("payload list is empty")
        return raw
    raise ValueError("payload file must be a JSON object or a list of JSON objects")


def get_output_dir(cli_value: str) -> Path:
    if cli_value:
        out = resolve_cli_path(cli_value)
    else:
        ts = datetime.now().strftime("%Y%m%d_%H%M%S")
        out = Path("out") / f"load_images_{ts}"
    out.mkdir(parents=True, exist_ok=True)
    return out


def build_artifact_headers(endpoint: str, headers: dict[str, str]) -> dict[str, str]:
    """The full directive for `--expect artifact`; any header the caller already set (case-insensitive) wins."""
    api_path = endpoint.strip("/") or "api"
    defaults = {
        "X-Haruki-Artifact": "1",
        "X-Haruki-Cache-Key": hashlib.sha256(api_path.encode("utf-8")).hexdigest(),
        "X-Haruki-Cache-Key-Version": DEFAULT_DIRECTIVE_KEY_VERSION,
        "X-Haruki-Cache-TTL": DEFAULT_DIRECTIVE_TTL_SECONDS,
        "X-Haruki-Cache-Group": DEFAULT_DIRECTIVE_GROUP,
        "X-Haruki-Api-Path": api_path,
        "X-Haruki-User-Id": DEFAULT_DIRECTIVE_USER_ID,
        "X-Haruki-Cache-Store": "1",
    }
    present = {name.lower() for name in headers}
    merged = {name: value for name, value in defaults.items() if name.lower() not in present}
    merged.update(headers)
    return merged


def parse_artifact_ref(body: bytes) -> dict[str, Any] | None:
    """The decoded ref when `body` is a JSON object with `kind == "artifact_ref"`, else `None`."""
    try:
        doc = json.loads(body)
    except (UnicodeDecodeError, ValueError):
        return None
    if not isinstance(doc, dict) or doc.get("kind") != ARTIFACT_REF_KIND:
        return None
    if not isinstance(doc.get("cdn_path"), str) or not doc["cdn_path"]:
        return None
    return doc


def build_url(base_url: str, endpoint: str) -> str:
    return base_url.rstrip("/") + "/" + endpoint.lstrip("/")


def pick_payload(payloads: list[dict[str, Any]], index: int) -> dict[str, Any]:
    return payloads[index % len(payloads)]


def percentile(values: list[float], p: float) -> float:
    if not values:
        return 0.0
    if len(values) == 1:
        return values[0]
    values_sorted = sorted(values)
    pos = (len(values_sorted) - 1) * p
    lower = int(pos)
    upper = min(lower + 1, len(values_sorted) - 1)
    ratio = pos - lower
    return values_sorted[lower] * (1.0 - ratio) + values_sorted[upper] * ratio


def extension_from_content_type(content_type: str | None) -> str:
    if not content_type:
        return "bin"
    ct = content_type.lower()
    if "png" in ct:
        return "png"
    if "jpeg" in ct or "jpg" in ct:
        return "jpg"
    if "webp" in ct:
        return "webp"
    if "gif" in ct:
        return "gif"
    if "json" in ct:
        return "json"
    if "text" in ct:
        return "txt"
    return "bin"


async def fire_one(
    session: aiohttp.ClientSession,
    *,
    index: int,
    method: str,
    url: str,
    payload: dict[str, Any],
    out_dir: Path,
    save_errors: bool,
    expect: str = EXPECT_IMAGE,
    fetch_cdn: str = "",
) -> RequestResult:
    started = time.perf_counter()
    try:
        if method == "GET":
            async with session.get(url, params=payload) as resp:
                body = await resp.read()
                elapsed_ms = (time.perf_counter() - started) * 1000
                result = await save_response(index, resp, body, elapsed_ms, out_dir, save_errors, expect=expect)
        else:
            async with session.post(url, json=payload) as resp:
                body = await resp.read()
                elapsed_ms = (time.perf_counter() - started) * 1000
                result = await save_response(index, resp, body, elapsed_ms, out_dir, save_errors, expect=expect)
        if result.ok and fetch_cdn and result.cdn_path:
            await verify_cdn_object(session, result, fetch_cdn, body, out_dir)
        return result
    except Exception as exc:
        elapsed_ms = (time.perf_counter() - started) * 1000
        return RequestResult(
            index=index,
            status=None,
            elapsed_ms=elapsed_ms,
            ok=False,
            response_path=None,
            response_bytes=0,
            content_type=None,
            error=str(exc),
        )


async def save_response(
    index: int,
    resp: aiohttp.ClientResponse,
    body: bytes,
    elapsed_ms: float,
    out_dir: Path,
    save_errors: bool,
    *,
    expect: str = EXPECT_IMAGE,
) -> RequestResult:
    content_type = resp.headers.get("Content-Type", "")
    if expect == EXPECT_ARTIFACT:
        return save_artifact_response(index, resp.status, content_type, body, elapsed_ms, out_dir, save_errors)
    is_image = 200 <= resp.status < 300 and content_type.startswith("image/")
    file_path: str | None = None
    if is_image:
        ext = extension_from_content_type(content_type)
        path = out_dir / "images" / f"{index:06d}_{resp.status}.{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        file_path = str(path)
    elif save_errors:
        ext = extension_from_content_type(content_type)
        path = out_dir / "errors" / f"{index:06d}_{resp.status}.{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        file_path = str(path)

    return RequestResult(
        index=index,
        status=resp.status,
        elapsed_ms=elapsed_ms,
        ok=is_image,
        response_path=file_path,
        response_bytes=len(body),
        content_type=content_type,
        error=None if is_image else f"status={resp.status}, content-type={content_type}",
    )


def save_artifact_response(
    index: int,
    status: int,
    content_type: str,
    body: bytes,
    elapsed_ms: float,
    out_dir: Path,
    save_errors: bool,
) -> RequestResult:
    """`--expect artifact`: ok only for 200 + application/json + `kind == "artifact_ref"`."""
    ref = None
    if status == 200 and content_type.lower().startswith("application/json"):
        ref = parse_artifact_ref(body)
    ok = ref is not None
    file_path: str | None = None
    if ok or save_errors:
        ext = extension_from_content_type(content_type)
        folder = "refs" if ok else "errors"
        path = out_dir / folder / f"{index:06d}_{status}.{ext}"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(body)
        file_path = str(path)
    return RequestResult(
        index=index,
        status=status,
        elapsed_ms=elapsed_ms,
        ok=ok,
        response_path=file_path,
        response_bytes=len(body),
        content_type=content_type,
        error=None if ok else f"expected artifact_ref: status={status}, content-type={content_type}",
        cdn_path=ref["cdn_path"] if ref else None,
        reused=bool(ref.get("reused")) if ref else None,
    )


async def verify_cdn_object(
    session: aiohttp.ClientSession,
    result: RequestResult,
    base: str,
    ref_body: bytes,
    out_dir: Path,
) -> None:
    """GET `<base>/<cdn_path>`; a non-200 or a size mismatch against `size_bytes` turns the result into a failure."""
    ref = parse_artifact_ref(ref_body) or {}
    url = build_url(base, result.cdn_path or "")
    try:
        async with session.get(url) as resp:
            data = await resp.read()
            result.cdn_status = resp.status
    except Exception as exc:
        result.ok = False
        result.error = f"cdn fetch failed: {exc}"
        return
    expected = ref.get("size_bytes")
    if result.cdn_status != 200:
        result.ok = False
        result.error = f"cdn status={result.cdn_status} url={url}"
        return
    if isinstance(expected, int) and expected != len(data):
        result.ok = False
        result.error = f"cdn size mismatch: ref={expected} fetched={len(data)} url={url}"
        return
    suffix = Path(result.cdn_path or "").suffix or ".bin"
    path = out_dir / "images" / f"{result.index:06d}_cdn{suffix}"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(data)


async def run(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if args.requests <= 0:
        raise ValueError("--requests must be > 0")
    if args.concurrency <= 0:
        raise ValueError("--concurrency must be > 0")

    if args.fetch_cdn and args.expect != EXPECT_ARTIFACT:
        raise ValueError("--fetch-cdn requires --expect artifact")

    headers = parse_headers(args.header)
    if args.expect == EXPECT_ARTIFACT:
        headers = build_artifact_headers(args.endpoint, headers)
    payloads = load_payloads(args.payload_file)
    out_dir = get_output_dir(args.output_dir)
    url = build_url(args.base_url, args.endpoint)

    timeout = aiohttp.ClientTimeout(total=args.timeout)
    connector = aiohttp.TCPConnector(limit=args.concurrency, ssl=not args.insecure)
    sem = asyncio.Semaphore(args.concurrency)

    async with aiohttp.ClientSession(timeout=timeout, connector=connector, headers=headers) as session:

        async def wrapped(idx: int) -> RequestResult:
            async with sem:
                payload = pick_payload(payloads, idx)
                return await fire_one(
                    session,
                    index=idx,
                    method=args.method,
                    url=url,
                    payload=payload,
                    out_dir=out_dir,
                    save_errors=args.save_errors,
                    expect=args.expect,
                    fetch_cdn=args.fetch_cdn,
                )

        tasks = [asyncio.create_task(wrapped(i)) for i in range(args.requests)]
        results = await asyncio.gather(*tasks)

    results.sort(key=lambda x: x.index)
    elapsed_list = [r.elapsed_ms for r in results if r.status is not None]
    ok_count = sum(1 for r in results if r.ok)
    fail_count = len(results) - ok_count

    status_counts: dict[str, int] = {}
    for r in results:
        key = "EXC" if r.status is None else str(r.status)
        status_counts[key] = status_counts.get(key, 0) + 1

    summary = {
        "url": url,
        "method": args.method,
        "requests": args.requests,
        "concurrency": args.concurrency,
        "expect": args.expect,
        "ok_images": ok_count,
        "failed": fail_count,
        "status_counts": status_counts,
        "artifact_reused": sum(1 for r in results if r.reused),
        "latency_ms": {
            "avg": round(statistics.mean(elapsed_list), 2) if elapsed_list else 0.0,
            "p50": round(percentile(elapsed_list, 0.50), 2),
            "p95": round(percentile(elapsed_list, 0.95), 2),
            "p99": round(percentile(elapsed_list, 0.99), 2),
            "max": round(max(elapsed_list), 2) if elapsed_list else 0.0,
        },
        "output_dir": str(out_dir.resolve()),
    }

    (out_dir / "summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    with (out_dir / "results.csv").open("w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["index", "status", "elapsed_ms", "ok", "bytes", "content_type", "response_path", "error"])
        for r in results:
            writer.writerow(
                [
                    r.index,
                    r.status if r.status is not None else "",
                    f"{r.elapsed_ms:.2f}",
                    int(r.ok),
                    r.response_bytes,
                    r.content_type or "",
                    r.response_path or "",
                    r.error or "",
                ]
            )

    sys.stdout.write(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    return 0 if ok_count > 0 else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(run()))
