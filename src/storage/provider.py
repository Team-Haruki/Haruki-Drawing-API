"""Provider settings -> opendal operator options, with Asset-Updater parity (`storage.rs:560-650`).

Pure functions; importing this module imports neither `opendal` nor `src.settings`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover
    from src.settings import StorageProviderSettings


def resolve_template(value: str, region: str | None) -> str:
    """Replace `{server}` and `{region}` with `region`; `None` leaves the template untouched."""
    if region is None:
        return value
    return value.replace("{server}", region).replace("{region}", region)


def construct_endpoint(endpoint: str, tls: bool) -> str:
    endpoint = endpoint.strip().rstrip("/")
    if endpoint.startswith(("http://", "https://")):
        return endpoint
    return f"{'https' if tls else 'http'}://{endpoint}"


def _root(p: StorageProviderSettings, region: str | None) -> str:
    raw = (p.root or "").strip() or (p.prefix or "").strip()
    if not raw:
        return ""
    raw = resolve_template(raw.replace("\\", "/"), region)
    if p.scheme == "fs":
        # A filesystem root is a directory path: keep a leading "/" so absolute paths stay absolute.
        stripped = raw.rstrip("/")
        return stripped or "/"
    return raw.strip("/")


def opendal_kwargs(p: StorageProviderSettings, region: str | None) -> dict[str, str]:
    """Build the opendal constructor options. `options` win: derived keys only fill gaps."""
    if p.scheme == "memory":
        return {}

    out: dict[str, str] = {key: resolve_template(str(value), region) for key, value in p.options.items()}

    def fill(key: str, value: str) -> None:
        out.setdefault(key, value)

    root = _root(p, region)
    if root:
        fill("root", root)

    if p.scheme == "fs":
        return out  # the S3-only keys (bucket, endpoint, credentials, ACL, addressing) are never derived

    if p.bucket.strip():
        fill("bucket", resolve_template(p.bucket.strip(), region))
    if p.endpoint.strip():
        fill("endpoint", construct_endpoint(p.endpoint, p.tls))
    if p.region:
        fill("region", p.region)
    if p.access_key_id is not None and p.access_key_id.get_secret_value():
        fill("access_key_id", p.access_key_id.get_secret_value())
    if p.secret_access_key is not None and p.secret_access_key.get_secret_value():
        fill("secret_access_key", p.secret_access_key.get_secret_value())
    if p.scheme == "s3" and p.public_read:
        fill("default_acl", "public-read")
    if not p.path_style:
        fill("enable_virtual_host_style", "true")

    if p.scheme == "s3" and not out.get("bucket", "").strip():
        raise ValueError(f"missing bucket for provider {p.provider or p.scheme!r}")
    return out
