"""Shared OpenAPI metadata for HTTP errors raised by drawing endpoints."""

from typing import Final

_IMAGE_GENERATION_FAILED = "Image generation failed."

INTERNAL_SERVER_ERROR_RESPONSES: Final = {
    500: {"description": _IMAGE_GENERATION_FAILED},
}

HEAVY_RENDER_ERROR_RESPONSES: Final = {
    500: {"description": _IMAGE_GENERATION_FAILED},
    503: {"description": "The isolated render worker queue is unavailable."},
    504: {"description": "The isolated render worker timed out."},
}

CUSTOM_PROFILE_ERROR_RESPONSES: Final = {
    400: {"description": "The custom profile payload is invalid or exceeds a configured limit."},
    500: {"description": _IMAGE_GENERATION_FAILED},
}

_NULLABLE_INT: Final = {"anyOf": [{"type": "integer"}, {"type": "null"}]}

ARTIFACT_REF_SCHEMA: Final = {
    "title": "ArtifactRef",
    "type": "object",
    "description": "Where the rendered image was stored (returned only for X-Haruki-Artifact: 1).",
    "required": [
        "kind",
        "hash",
        "cdn_path",
        "storage_backend",
        "bucket",
        "object_key",
        "size_bytes",
        "media_type",
        "width",
        "height",
        "cache_key",
        "ttl_seconds",
        "expires_at",
        "reused",
        "index_written",
        "upload_elapsed",
        "node_name",
    ],
    "properties": {
        "kind": {"type": "string", "enum": ["artifact_ref"]},
        "hash": {"type": "string", "description": "sha256 hex of the image bytes"},
        "cdn_path": {"type": "string"},
        "storage_backend": {"type": "string", "enum": ["garage"]},
        "bucket": {"type": "string"},
        "object_key": {"type": "string"},
        "size_bytes": {"type": "integer"},
        "media_type": {"type": "string"},
        "width": _NULLABLE_INT,
        "height": _NULLABLE_INT,
        "cache_key": {"type": "string"},
        "ttl_seconds": {"type": "integer", "description": "0 means infinite"},
        "expires_at": {
            "anyOf": [{"type": "string", "format": "date-time"}, {"type": "null"}],
            "description": "RFC3339 UTC; null when ttl_seconds is 0",
        },
        "reused": {"type": "boolean"},
        "index_written": {"type": "boolean"},
        "upload_elapsed": {"type": "number"},
        "node_name": {"type": "string"},
    },
}

ARTIFACT_RESPONSES: Final = {
    200: {
        "description": "Rendered image bytes, or an ArtifactRef JSON document when the request carried "
        "X-Haruki-Artifact: 1",
        "content": {
            "image/png": {},
            "image/jpeg": {},
            "application/json": {"schema": ARTIFACT_REF_SCHEMA},
        },
    },
}
