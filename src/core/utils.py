from collections.abc import Mapping
import io
import logging

from fastapi.responses import JSONResponse, Response

from src.artifact.directive import HEADER_ARTIFACT_MODE, MODE_STORE_REF, RenderCacheDirective
from src.artifact.runtime import get_artifact_runtime
from src.artifact.stats import artifact_node_name, artifact_stats
from src.core.cache_identity import renderer_epoch
from src.core.debug import (
    current_render_backend,
    current_render_directive,
    current_request_context,
    set_request_stage,
    snapshot_process_metrics,
)
from src.core.image_payload import EncodedImagePayload
from src.core.missing_asset_telemetry import current_missing_asset_count
from src.settings import settings

logger = logging.getLogger(__name__)

ARTIFACT_HEADER = "X-Haruki-Artifact"
DEGRADED_HEADER = "X-Haruki-Artifact-Degraded"
CACHE_STORE_HEADER = "X-Haruki-Cache-Store"
NODE_HEADER = "X-Haruki-Node"


# drawing.jpg_subsampling -> Pillow's ``subsampling`` save option.
PIL_JPEG_SUBSAMPLING = {"444": "4:4:4", "422": "4:2:2", "420": "4:2:0"}


def _encode_image(
    image,
    export_format: str,
    jpg_quality: int,
    *,
    jpeg_subsampling: int | str | None = None,
) -> tuple[io.BytesIO, str, str]:
    """Pillow encode (reference tools only; service routes leave through the native encoder).

    JPEG uses ``drawing.jpg_subsampling`` unless ``jpeg_subsampling`` overrides it, so a reference
    JPEG is encoded the way the native renderer encodes production output.
    """
    buffer = io.BytesIO()
    try:
        if export_format == "jpg":
            # JPEG 不支持 alpha 通道，需要转换为 RGB
            if image.mode in ("RGBA", "LA", "PA"):
                rgb = image.convert("RGB")
                image.close()
                image = rgb
            if jpeg_subsampling is None:
                jpeg_subsampling = PIL_JPEG_SUBSAMPLING[settings.drawing.jpg_subsampling]
            image.save(buffer, format="JPEG", quality=jpg_quality, subsampling=jpeg_subsampling)
            media_type = "image/jpeg"
            filename = "image.jpg"
        else:
            image.save(buffer, format="PNG")
            media_type = "image/png"
            filename = "image.png"
    finally:
        close = getattr(image, "close", None)
        if callable(close):
            close()
    buffer.seek(0)
    return buffer, media_type, filename


def _image_response(
    image_bytes: bytes,
    media_type: str,
    filename: str,
    *,
    extra_headers: Mapping[str, str] | None = None,
) -> Response:
    """Send the encoded image as ONE body message.

    This used to be ``StreamingResponse(io.BytesIO(image_bytes))``, which streamed nothing useful:
    the bytes are already whole in memory, so there was no memory to save. What it did instead was
    hand Starlette a *sync* iterable, which Starlette drives through ``iterate_in_threadpool`` --
    and iterating a ``BytesIO`` yields **lines**. A PNG is binary, so it split on every 0x0A byte:
    ~384 bytes per chunk, i.e. ~2,300 thread-pool round-trips and ~2,300 ASGI body messages for a
    single 870 KB image. Under 8 concurrent requests that scheduling storm took a render the server
    finished in 0.12s and made the client wait ~10s for it, with the CPU 95% idle.
    """
    return Response(
        content=image_bytes,
        media_type=media_type,
        headers={"Content-Disposition": f"inline; filename={filename}", **(extra_headers or {})},
    )


def _log_and_return_bytes(
    payload: EncodedImagePayload,
    *,
    artifact: str,
    missing: int,
    headers: Mapping[str, str] | None = None,
    reason: str | None = None,
) -> Response:
    _log_image_response(payload, artifact=artifact, missing=missing, detail=f" reason={reason}" if reason else "")
    set_request_stage("send_response")
    return _image_response(payload.image_bytes, payload.media_type, payload.filename, extra_headers=headers)


def _format_artifact_stages(stages: Mapping[str, float]) -> str:
    """` stages=hash:0.0012,upload:0.0461,...` (seconds per artifact sub-stage), or `""` when none ran."""
    if not stages:
        return ""
    return " stages=" + ",".join(f"{name}:{seconds:.4f}" for name, seconds in stages.items())


def _log_image_response(payload: EncodedImagePayload, *, artifact: str, missing: int, detail: str = "") -> None:
    """The one `image.response` line per request, on every exit branch."""
    request_ctx = current_request_context()
    logger.info(
        "image.response id=%s path=%s method=%s size=%sx%s mode=%s media=%s bytes=%d elapsed=%.3fs "
        "backend=%s artifact=%s%s missing_assets=%d metrics=%s",
        request_ctx["request_id"],
        request_ctx["path"],
        request_ctx["method"],
        payload.image_width,
        payload.image_height,
        payload.image_mode,
        payload.media_type,
        len(payload.image_bytes),
        payload.encode_elapsed,
        # The payload carries its own backend across the heavy-worker process boundary, where a
        # contextvar set in the child is invisible here; in-process renders set the contextvar.
        payload.backend or current_render_backend(),
        artifact,
        detail,
        missing,
        snapshot_process_metrics(include_asyncio=False),
    )


def encoded_image_payload_to_bytes_response(
    payload: EncodedImagePayload,
    *,
    extra_headers: Mapping[str, str] | None = None,
) -> Response:
    """Today's bytes body, verbatim, plus optional extra headers (ONE body message, `Content-Length` set)."""
    missing = current_missing_asset_count() + payload.missing_asset_count
    headers = dict(extra_headers or {})
    if missing or payload.has_missing_resources:
        headers[CACHE_STORE_HEADER] = "0"
    return _log_and_return_bytes(
        payload,
        artifact="0",
        missing=missing,
        headers=headers,
    )


async def encoded_image_payload_to_response(payload: EncodedImagePayload) -> Response:
    """The single route exit: image bytes, or the `artifact_ref` JSON when the request asked for one.

    Every branch carries `X-Haruki-Node` (addendum A1) and emits exactly one `image.response` line. A request
    without a directive gets today's bytes response plus that one header. In artifact mode `Cache-Store: 0`
    returns bytes and stores nothing (E2/A3); any storage failure degrades to bytes with
    `X-Haruki-Artifact-Degraded: 1` (invariant I1). `Cache-Store: 0` with `X-Haruki-Artifact-Mode: store-ref`
    uploads without any index write and returns the ref (docs/artifact-storage.md §12).
    """
    directive = current_render_directive()
    missing = current_missing_asset_count() + payload.missing_asset_count
    node = {NODE_HEADER: artifact_node_name()}
    if directive is None:
        artifact_stats.incr("bytes_no_directive")
        if missing or payload.has_missing_resources:
            node[CACHE_STORE_HEADER] = "0"
        return _log_and_return_bytes(payload, artifact="0", missing=missing, headers=node)
    # `requests_with_directive` is counted once by the debug middleware when it binds the directive.
    if directive.store_ref and not directive.store:
        return await _store_ref_response(payload, directive, missing=missing, node=node)
    stale_renderer = bool(directive.renderer_epoch) and directive.renderer_epoch != renderer_epoch()
    if not directive.store or missing > 0 or payload.has_missing_resources or stale_renderer:
        artifact_stats.incr("store_skipped")
        return _log_and_return_bytes(
            payload,
            artifact="store0",
            missing=missing,
            headers={**node, CACHE_STORE_HEADER: "0"},
        )
    outcome = await get_artifact_runtime().process(payload, directive)
    ref = outcome.ref
    if ref is not None:
        _log_image_response(
            payload,
            artifact="1",
            missing=missing,
            detail=(
                f" hash={ref.hash} reused={int(ref.reused)} index_written={int(ref.index_written)} "
                f"upload={ref.upload_elapsed:.3f}{_format_artifact_stages(outcome.stages)}"
            ),
        )
        set_request_stage("send_response")
        # JSONResponse renders the whole document up front: ONE body message with Content-Length.
        return JSONResponse(ref.to_json(), headers={ARTIFACT_HEADER: "1", **node})
    return _log_and_return_bytes(
        payload,
        artifact="degraded",
        reason=f"{outcome.reason}{_format_artifact_stages(outcome.stages)}",
        missing=missing,
        headers={**node, DEGRADED_HEADER: "1"},
    )


async def _store_ref_response(
    payload: EncodedImagePayload, directive: RenderCacheDirective, *, missing: int, node: Mapping[str, str]
) -> Response:
    """Store-ref branch: the ref JSON, or the bytes with `Degraded: 1`. Always `Cache-Store: 0`.

    The caller asked not to cache this render (`Cache-Store: 0`); it only wants the image delivered without
    carrying the bytes back. So missing assets and a stale renderer epoch still upload: the bytes are what the
    user is shown either way, and `Cache-Store: 0` keeps the caller from keying them under the render request.
    """
    if missing or payload.has_missing_resources:
        artifact_stats.store_ref_incr("missing_assets")
    headers = {**node, CACHE_STORE_HEADER: "0"}
    outcome = await get_artifact_runtime().process_store_ref(payload, directive)
    ref = outcome.ref
    if ref is not None:
        _log_image_response(
            payload,
            artifact="store_ref",
            missing=missing,
            detail=(
                f" hash={ref.hash} writer={ref.node_name} upload={ref.upload_elapsed:.3f}"
                f"{_format_artifact_stages(outcome.stages)}"
            ),
        )
        set_request_stage("send_response")
        return JSONResponse(
            ref.to_json(), headers={ARTIFACT_HEADER: "1", HEADER_ARTIFACT_MODE: MODE_STORE_REF, **headers}
        )
    return _log_and_return_bytes(
        payload,
        artifact="store_ref_degraded",
        reason=f"{outcome.reason}{_format_artifact_stages(outcome.stages)}",
        missing=missing,
        headers={**headers, DEGRADED_HEADER: "1"},
    )
