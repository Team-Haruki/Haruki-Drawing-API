"""Backend-neutral honor asset planning and synchronous resolution.

The honor widget owns layout.  This module owns the smaller contract immediately before it:
which request path fields a branch can actually consume, which source is indispensable, and
whether a supplied-but-unavailable overlay may be omitted without changing Pillow semantics.

No image backend is imported here.  Callers inject both path resolution and source construction,
so the result can carry Pillow images, lazy asset references, or another backend-specific source.
Non-ready results deliberately expose no partial source map; HonorDeck can therefore preflight all
slots before mutating its destination scene.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Generic, Literal, TypeVar

from .model import HonorRequest

HonorAssetBranch = Literal["empty", "normal", "birthday", "bonds", "unsupported"]
HonorAssetRequirement = Literal["required", "optional"]
HonorAssetMissingPolicy = Literal["ignore", "hybrid"]
HonorAssetResolutionStatus = Literal["ready", "unrenderable", "hybrid"]
HonorAssetFailureReason = Literal[
    "unsupported_branch",
    "path_absent",
    "path_unresolved",
    "source_unavailable",
]

ResolvedPathT = TypeVar("ResolvedPathT")
SourceT = TypeVar("SourceT")


# This is the single field-name manifest for every source understood by HonorBadgeBox.
# Branch selection below only refers to image keys from this mapping.
HONOR_ASSET_MANIFEST: Mapping[str, str] = MappingProxyType(
    {
        "honor_img": "honor_img_path",
        "rank_img": "rank_img_path",
        "lv_img": "lv_img_path",
        "lv6_img": "lv6_img_path",
        "empty_honor": "empty_honor_path",
        "scroll_img": "scroll_img_path",
        "word_img": "word_img_path",
        "chara_icon_1": "chara_icon_path",
        "chara_icon_2": "chara_icon_path2",
        "bonds_bg": "bonds_bg_path",
        "bonds_bg2": "bonds_bg_path2",
        "mask_img": "mask_img_path",
        "frame_img": "frame_img_path",
        "frame_degree_level_img": "frame_degree_level_img_path",
    }
)

_SCROLL_LEVEL_GROUP_TYPES = frozenset({"fc_ap", "event", "wl_event"})
_STAR_LEVEL_GROUP_TYPES = frozenset({"character", "achievement"})


@dataclass(frozen=True, slots=True)
class HonorAssetSpec:
    """One source that the selected honor branch can consume.

    Optional assets may be omitted from a request.  Once most overlays are explicitly supplied,
    however, losing them would change the badge and must classify the request as ``hybrid``.
    ``rank_img`` is the exception retained from the honor drawer: it is optional even when its
    supplied path cannot be loaded.
    """

    image_key: str
    path_field: str
    requirement: HonorAssetRequirement
    on_supplied_missing: HonorAssetMissingPolicy = "hybrid"

    @property
    def required(self) -> bool:
        return self.requirement == "required"


@dataclass(frozen=True, slots=True)
class HonorAssetFailure:
    """Why a request could not produce an atomic source map."""

    reason: HonorAssetFailureReason
    image_key: str | None = None
    path_field: str | None = None
    raw_path: str | None = None
    detail: str | None = None


@dataclass(frozen=True, slots=True)
class HonorAssetResolution(Generic[SourceT]):
    """Atomic result of resolving one HonorRequest's active asset branch."""

    status: HonorAssetResolutionStatus
    branch: HonorAssetBranch
    specs: tuple[HonorAssetSpec, ...]
    sources: Mapping[str, SourceT | None] | None
    failure: HonorAssetFailure | None = None

    @property
    def ready(self) -> bool:
        return self.status == "ready"


@dataclass(frozen=True, slots=True)
class _HonorAssetLoadFailure:
    status: Literal["unrenderable", "hybrid"]
    reason: HonorAssetFailureReason
    raw_path: str | None = None
    detail: str | None = None


def honor_asset_branch(request: HonorRequest) -> HonorAssetBranch:
    """Return the layout branch selected by ``HonorBadgeBox``."""

    if request.is_empty:
        return "empty"
    honor_type = str(request.honor_type or "").strip().lower()
    if honor_type in {"normal", "birthday", "bonds"}:
        return honor_type
    return "unsupported"


def _required(image_key: str) -> HonorAssetSpec:
    return HonorAssetSpec(image_key, HONOR_ASSET_MANIFEST[image_key], "required")


def _optional(image_key: str, *, on_supplied_missing: HonorAssetMissingPolicy = "hybrid") -> HonorAssetSpec:
    return HonorAssetSpec(
        image_key,
        HONOR_ASSET_MANIFEST[image_key],
        "optional",
        on_supplied_missing,
    )


def _normal_honor_asset_specs(
    request: HonorRequest, branch: Literal["normal", "birthday"]
) -> tuple[HonorAssetSpec, ...]:
    specs = [
        _required("honor_img"),
        _optional("rank_img", on_supplied_missing="ignore"),
        _optional("frame_img"),
    ]
    # HonorBadgeBox returns from _add_frame before consulting the birthday level icon.
    if branch == "birthday" and request.frame_img_path:
        specs.append(_optional("frame_degree_level_img"))
    group_type = str(request.group_type or "").strip().lower()
    if group_type in _SCROLL_LEVEL_GROUP_TYPES:
        specs.append(_optional("scroll_img"))
    elif group_type in _STAR_LEVEL_GROUP_TYPES:
        specs.extend((_optional("lv_img"), _optional("lv6_img")))
    return tuple(specs)


def _bonds_honor_asset_specs(request: HonorRequest) -> tuple[HonorAssetSpec, ...]:
    specs = [_required("bonds_bg"), _required("bonds_bg2")]
    # The shared tree intentionally returns a bare background when either character icon is
    # absent. In that branch it never reads the lone icon, mask, frame, word, or level stars.
    if not request.chara_icon_path or not request.chara_icon_path2:
        return tuple(specs)
    specs.extend(
        (
            _optional("chara_icon_1"),
            _optional("chara_icon_2"),
            _optional("mask_img"),
            _optional("frame_img"),
            _optional("lv_img"),
            _optional("lv6_img"),
        )
    )
    if request.is_main_honor:
        specs.append(_optional("word_img"))
    return tuple(specs)


def honor_asset_specs(request: HonorRequest) -> tuple[HonorAssetSpec, ...]:
    """Return only the source fields that the selected widget branch can consume."""

    branch = honor_asset_branch(request)
    if branch == "empty":
        specs = (_required("empty_honor"),)
    elif branch in {"normal", "birthday"}:
        specs = _normal_honor_asset_specs(request, branch)
    elif branch == "bonds":
        specs = _bonds_honor_asset_specs(request)
    else:
        specs = ()
    return specs


def _failure_result(
    *,
    status: Literal["unrenderable", "hybrid"],
    branch: HonorAssetBranch,
    specs: tuple[HonorAssetSpec, ...],
    reason: HonorAssetFailureReason,
    spec: HonorAssetSpec | None = None,
    raw_path: str | None = None,
    detail: str | None = None,
) -> HonorAssetResolution[SourceT]:
    return HonorAssetResolution(
        status=status,
        branch=branch,
        specs=specs,
        sources=None,
        failure=HonorAssetFailure(
            reason=reason,
            image_key=spec.image_key if spec is not None else None,
            path_field=spec.path_field if spec is not None else None,
            raw_path=raw_path,
            detail=detail,
        ),
    )


def _unresolved_path_status(spec: HonorAssetSpec) -> HonorAssetResolutionStatus:
    if spec.required:
        return "unrenderable"
    if spec.on_supplied_missing == "hybrid":
        return "hybrid"
    return "ready"


def _unavailable_source_status(spec: HonorAssetSpec) -> Literal["ready", "hybrid"]:
    # A resolver exception or failed source factory means the supplied resource was not merely
    # absent: it was rejected (for example, outside the asset root) or cannot be represented by
    # this backend.  Skipping to a lower-priority request would violate request precedence.
    return "ready" if spec.on_supplied_missing == "ignore" else "hybrid"


def _load_failure_or_none(
    spec: HonorAssetSpec,
    *,
    reason: Literal["path_unresolved", "source_unavailable"],
    raw_path: str,
    detail: str | None = None,
    unresolved_path: bool = False,
) -> _HonorAssetLoadFailure | None:
    status = _unresolved_path_status(spec) if unresolved_path else _unavailable_source_status(spec)
    if status == "ready":
        return None
    return _HonorAssetLoadFailure(status, reason, raw_path, detail)


def _load_honor_asset_source(
    request: HonorRequest,
    spec: HonorAssetSpec,
    *,
    path_resolver: Callable[[str], ResolvedPathT | None],
    source_factory: Callable[[ResolvedPathT], SourceT | None],
) -> tuple[SourceT | None, _HonorAssetLoadFailure | None]:
    raw_value = getattr(request, spec.path_field)
    raw_path = str(raw_value) if raw_value else None
    if raw_path is None:
        failure = _HonorAssetLoadFailure("unrenderable", "path_absent") if spec.required else None
        return None, failure

    try:
        resolved_path = path_resolver(raw_path)
    except Exception as exc:
        failure = _load_failure_or_none(
            spec,
            reason="path_unresolved",
            raw_path=raw_path,
            detail=f"{type(exc).__name__}: {exc}",
        )
        return None, failure
    if resolved_path is None:
        failure = _load_failure_or_none(
            spec,
            reason="path_unresolved",
            raw_path=raw_path,
            unresolved_path=True,
        )
        return None, failure

    try:
        source = source_factory(resolved_path)
    except Exception as exc:
        failure = _load_failure_or_none(
            spec,
            reason="source_unavailable",
            raw_path=raw_path,
            detail=f"{type(exc).__name__}: {exc}",
        )
        return None, failure
    if source is None:
        failure = _load_failure_or_none(spec, reason="source_unavailable", raw_path=raw_path)
        return None, failure
    return source, None


def resolve_honor_assets(
    request: HonorRequest,
    *,
    path_resolver: Callable[[str], ResolvedPathT | None],
    source_factory: Callable[[ResolvedPathT], SourceT | None],
) -> HonorAssetResolution[SourceT]:
    """Resolve the active branch without importing or constructing image pixels.

    ``path_resolver`` converts a request path to any caller-owned token. ``source_factory`` turns
    that token into the source consumed by the caller's honor tree. Returning ``None`` or raising
    an exception from either callback is classified using the asset's policy:

    - an absent/unresolved required base is ``unrenderable`` so a lower-priority request key may
      be tried;
    - a missing explicitly supplied overlay is ``hybrid`` because silently omitting it changes
      the selected request;
    - ``rank_img`` remains optional and resolves to ``None`` even when its supplied path is bad.

    Callback exceptions and source-factory failures are different from an unresolved path: they
    mean a supplied resource exists but is unsafe, corrupt, or unsupported by this backend, so the
    selected request is ``hybrid`` rather than eligible to fall through to a lower-priority badge.
    """

    branch = honor_asset_branch(request)
    specs = honor_asset_specs(request)
    if branch == "unsupported":
        return _failure_result(
            status="unrenderable",
            branch=branch,
            specs=specs,
            reason="unsupported_branch",
            detail=f"unsupported honor type: {request.honor_type!r}",
        )

    sources: dict[str, SourceT | None] = {}
    for spec in specs:
        source, failure = _load_honor_asset_source(
            request,
            spec,
            path_resolver=path_resolver,
            source_factory=source_factory,
        )
        if failure is not None:
            return _failure_result(
                status=failure.status,
                branch=branch,
                specs=specs,
                reason=failure.reason,
                spec=spec,
                raw_path=failure.raw_path,
                detail=failure.detail,
            )
        sources[spec.image_key] = source

    return HonorAssetResolution(
        status="ready",
        branch=branch,
        specs=specs,
        sources=MappingProxyType(sources),
    )


__all__ = [
    "HONOR_ASSET_MANIFEST",
    "HonorAssetBranch",
    "HonorAssetFailure",
    "HonorAssetMissingPolicy",
    "HonorAssetRequirement",
    "HonorAssetResolution",
    "HonorAssetResolutionStatus",
    "HonorAssetSpec",
    "honor_asset_branch",
    "honor_asset_specs",
    "resolve_honor_assets",
]
