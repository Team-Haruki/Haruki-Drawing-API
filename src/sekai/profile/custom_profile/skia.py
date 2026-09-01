"""Progressive native Render-IR path for ``/profile/custom-profile-card``.

The Unity card JSON remains the layout carrier. Asset-backed ``UnityImage`` and ``SdfShape``
elements lower directly to Rust/Skia without a Pillow decode or NumPy raster; decorative text
uses native ``SdfQuad`` shading, and normal/birthday/bonds/empty honors reuse the shared
``HonorBadgeBox`` asset-backed subtree inside an isolated native subscene. Shared General/Card
display lists replay native ``SlicedImage``/sprite/Text/viewport/card operations with strict Rust
font metrics and Pillow-compatible Lanczos stages. Plain dynamic-font TMP text also emits native IR Text;
all remaining TMP/SDF text is offered to the asset-backed ``SdfAtlasQuad`` / ``SdfFontQuad``
sparse-glyph path. Incomplete content is explicitly classified and
rejected by a scene coverage report before Rust runs, so ``backend=skia`` cannot mean
"successfully encoded a partial card".

Honor dimensions come from the native asset-info API. An older wheel declines the native scene
before rasterization; it cannot use a Pillow header probe and masquerade as native-pure.

Parity-critical mirrors of the Pillow path:
- Unrotated, unscaled layers are pasted at ROUNDED integer positions (the ``angle ~ 0`` branch of
  ``prepare_canvas_clipped_transformed_layer``); the scene emits those as plain integer-placed
  images with no Transform, so they stay pixel-crisp instead of drifting subpixel.
- Unity asset nodes reproduce the compatibility path's two-step dimension rounding and sampling
  without materializing request-local Pillow layers.
- Every TMP/SDF text is either asset/font-backed native IR or classified unresolved before Rust;
  the Skia attempt never transports request pixels through ``mem:`` images.

Fail-open: this function NEVER raises — every failure records exactly one outcome and returns
``None`` so the route falls back to Pillow, which raises the canonical user-visible errors.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
import hashlib
import json
import logging
import math
from pathlib import Path
import threading
import time
from typing import Any

from src.core.image_payload import EncodedImagePayload
from src.sekai.base.utils import AssetImageRef, ImageSource, run_in_pool
from src.sekai.honor.assets import resolve_honor_assets
from src.sekai.honor.model import HonorRequest
from src.sekai.honor.widget import build_honor_badge_canvas
from src.sekai.profile.custom_profile.card_prefab import (
    CardAlphaMaskOp,
    CardCoverArtOp,
    CardDisplayList,
    CardRectOp,
    CardSpriteOp,
    CardTextOp,
)
from src.sekai.profile.custom_profile.collection_prefab import (
    OmikujiAssetOp,
    OmikujiRectOp,
    OmikujiTextOp,
    build_omikuji_display_list,
)
from src.sekai.profile.custom_profile.diagnostics import (
    capture_safe_exception,
    persist_custom_profile_diagnostic,
)
from src.sekai.profile.custom_profile.general_prefab import (
    GeneralAssetImageOp,
    GeneralFontRef,
    GeneralPrefabDisplayList,
    GeneralPrefabOp,
    GeneralRoundedRectOp,
    GeneralSpriteChoiceOp,
    GeneralSpriteOp,
    GeneralTextOp,
    GeneralViewportOp,
    build_general_prefab_display_list,
    story_favorite_asset_key,
)
from src.sekai.profile.custom_profile.honor_deck_prefab import (
    build_honor_deck_plan,
    honor_deck_request_candidates,
)
from src.sekai.profile.custom_profile.limits import ensure_raster_size
from src.sekai.profile.custom_profile.renderer import (
    CHARA_LIST,
    GENERAL_DECK_CARD_RENDER_SIZE,
    GENERAL_MUSIC_DIFFICULTIES,
    GENERAL_NATIVE_SIZES,
    GENERAL_PREFAB_PALETTE,
    PROFILE_RENDER_VIEW_H,
    PROFILE_RENDER_VIEW_W,
    SHAPE_NATIVE_OUTLINE_FILL_RATIO_FACTOR,
    STATIC_IMAGE_CONTENT_KINDS,
    DirectSdfAtlasQuad,
    DirectSdfFontQuad,
    PNGRenderer,
    bool_from_profile,
    content_data_id,
    hex_to_rgba,
    unity_tint_rgba,
)
from src.sekai.profile.custom_profile.svg import unity_rotation_degrees
from src.sekai.profile.custom_profile.tmp_text_prefab import build_simple_tmp_text_display_list
from src.sekai.profile.model import CustomProfileCardRenderRequest
from src.sekai.skia_renderer.canvas import load_native_renderer, payload_from_native, skia_plot_enabled
from src.sekai.skia_renderer.ir_builder import IRBuilder, clip_pillow_rrect, image_tint
from src.sekai.skia_renderer.render_stats import (
    OUTCOME_DISABLED,
    OUTCOME_ERROR,
    OUTCOME_FALLBACK,
    OUTCOME_SKIA,
    backend_for_outcome,
    record_native_metrics,
    record_render,
    record_scene_completeness,
)
from src.sekai.skia_renderer.subtree import NativeSubtree, NativeSubtreeError, lower_canvas_subtree
from src.settings import (
    ASSETS_BASE_DIR,
    CUSTOM_PROFILE_MAX_LAYER_PIXELS,
    CUSTOM_PROFILE_MAX_SCENE_BYTES,
    DEFAULT_BOLD_FONT,
    DEFAULT_FONT,
    FONT_DIR,
    JPG_QUALITY,
)

logger = logging.getLogger("custom_profile.draw.perf")

# /render-stats + the ``backend=`` log field key. Like honor and chart, this scene is hand-built
# (no plot.py tree exists to hand to render_canvas_payload), so it records its own outcome —
# exactly one per attempt.
CUSTOM_PROFILE_ENDPOINT = "custom_profile_card"

_REQUIRED_NATIVE_ASSET_INFO_CAPABILITY = 1
_REQUIRED_NATIVE_TEXT_METRICS_CAPABILITY = 1
_NATIVE_GENERAL_PREFABS = frozenset(
    {
        "X",
        "EditUserName",
        "Comment",
        "TotalPower",
        "MultiLive",
        "ChallengeLive",
        "CharacterRankAndChallengeStage",
        "CharacterRankAndChallengeStageScroll",
        "MusicClearInfo",
        "MusicClearSelectTabInfo",
        "StoryFavorite",
    }
)
_GENERAL_FONT_IR_NAME = "custom_profile_general"
_OMIKUJI_FONT_IR_NAME = "custom_profile_omikuji"
_NATIVE_CARD_GENERAL_PREFABS = frozenset({"LeaderCard", "Deck"})


class _NativeAssetInfoUnavailable(RuntimeError):
    """The installed wheel cannot provide Pillow-free asset dimensions."""


@dataclass(slots=True)
class CustomProfileSceneReport:
    elements_total: int = 0
    visible_elements: int = 0
    native_elements: int = 0
    hybrid_elements: int = 0
    noop_elements: int = 0
    hidden_elements: int = 0
    missing_elements: int = 0
    unresolved_elements: int = 0
    mem_images: int = 0
    mem_bytes: int = 0
    classifications_by_kind: dict[str, dict[str, int]] = field(default_factory=dict)

    @property
    def complete(self) -> bool:
        classified_visible = (
            self.native_elements
            + self.hybrid_elements
            + self.noop_elements
            + self.missing_elements
            + self.unresolved_elements
        )
        return (
            self.visible_elements == classified_visible
            and self.elements_total == self.visible_elements + self.hidden_elements
            and self.hybrid_elements == 0
            and self.missing_elements == 0
            and self.unresolved_elements == 0
            and self.mem_images == 0
            and self.mem_bytes == 0
        )

    def observe(self, content: Any, classification: str) -> None:
        resolved_classification = (
            classification
            if classification
            in {
                "hidden",
                "native",
                "hybrid",
                "noop",
                "missing",
                "unresolved",
            }
            else "unresolved"
        )
        kind_bucket = self.classifications_by_kind.setdefault(str(content.kind), {})
        kind_bucket[resolved_classification] = kind_bucket.get(resolved_classification, 0) + 1
        if resolved_classification == "hidden":
            self.hidden_elements += 1
            return
        self.visible_elements += 1
        counter = {
            "native": "native_elements",
            "hybrid": "hybrid_elements",
            "noop": "noop_elements",
            "missing": "missing_elements",
            "unresolved": "unresolved_elements",
        }.get(resolved_classification, "unresolved_elements")
        setattr(self, counter, getattr(self, counter) + 1)

    def metrics(self) -> dict[str, Any]:
        issues_by_kind: dict[str, dict[str, int]] = {}
        for kind, classifications in self.classifications_by_kind.items():
            missing = int(classifications.get("missing", 0))
            unresolved = int(classifications.get("unresolved", 0))
            if missing or unresolved:
                issues_by_kind[kind] = {"missing": missing, "unresolved": unresolved}
        return {
            "complete": int(self.complete),
            "elements_total": self.elements_total,
            "visible_elements": self.visible_elements,
            "native_elements": self.native_elements,
            "hybrid_elements": self.hybrid_elements,
            "noop_elements": self.noop_elements,
            "hidden_elements": self.hidden_elements,
            "missing_elements": self.missing_elements,
            "unresolved_elements": self.unresolved_elements,
            "mem_images": self.mem_images,
            "mem_bytes": self.mem_bytes,
            "issues_by_kind": issues_by_kind,
            "classifications_by_kind": {
                kind: dict(sorted(counts.items())) for kind, counts in sorted(self.classifications_by_kind.items())
            },
        }

    def native_metrics(self) -> dict[str, int]:
        return {
            "custom_profile_complete": int(self.complete),
            "custom_profile_visible_elements": self.visible_elements,
            "custom_profile_native_elements": self.native_elements,
            "custom_profile_hybrid_elements": self.hybrid_elements,
            "custom_profile_noop_elements": self.noop_elements,
            "custom_profile_mem_images": self.mem_images,
            "custom_profile_mem_bytes": self.mem_bytes,
        }


def _record(
    outcome: str,
    payload: EncodedImagePayload | None = None,
    *,
    error_stage: str | None = None,
) -> None:
    """Record one render attempt for /render-stats and tag the request context.

    Mirrors ``skia_renderer.canvas._record`` / ``honor.skia._record``: this path cannot reuse the
    canvas helper, so it records through the same public primitives instead.
    """
    record_render(CUSTOM_PROFILE_ENDPOINT, outcome, error_stage=error_stage)
    _tag_backend(outcome, payload)
    if payload is not None:
        record_native_metrics(payload.native_metrics)


def _tag_backend(outcome: str, payload: EncodedImagePayload | None = None) -> None:
    """Set request/log backend metadata without committing aggregate counters."""
    from src.core.debug import set_render_backend

    backend = backend_for_outcome(outcome)
    set_render_backend(backend)
    if payload is not None:
        payload.backend = backend


class CustomProfileSkiaAttempt:
    """One deferred Custom Profile backend outcome.

    The route commits this only after it knows the final HTTP result. A request rejected with
    the canonical 400 is not production render traffic and must not poison the pure-Skia gate;
    a Skia failure recovered by a successful Pillow response is still recorded as ``error``.
    Direct render/parity callers use :func:`try_render_custom_profile_card_payload`, which
    commits immediately and preserves the historical payload-or-None contract.
    """

    def __init__(
        self,
        payload: EncodedImagePayload | None,
        outcome: str,
        *,
        report: CustomProfileSceneReport | None = None,
        error_stage: str | None = None,
        error_type: str | None = None,
        exception_diagnostic: dict[str, Any] | None = None,
    ) -> None:
        self.payload = payload
        self.outcome = outcome
        self.report = report
        self.error_stage = error_stage
        self.error_type = error_type
        self.exception_diagnostic = exception_diagnostic
        self._record_lock = threading.Lock()
        self._recorded = False

    def tag_backend(self) -> None:
        """Make response/performance logs reflect this attempt before encoding starts."""

        _tag_backend(self.outcome, self.payload)

    def record(self, final_http_status: int | None = None) -> None:
        """Commit aggregate metrics exactly once."""

        with self._record_lock:
            if self._recorded:
                return
            self._recorded = True
        if self.outcome == OUTCOME_ERROR:
            logger.error(
                "custom_profile_card backend=skia committed_error stage=%s error_type=%s",
                self.error_stage or "unknown",
                self.error_type or "unknown",
            )
        if self.outcome == OUTCOME_ERROR or (
            self.outcome == OUTCOME_FALLBACK
            and (self.exception_diagnostic is not None or (self.report is not None and not self.report.complete))
        ):
            persist_custom_profile_diagnostic(
                outcome=self.outcome,
                stage=self.error_stage or ("scene_coverage" if self.report is not None else "unknown"),
                error_type=self.error_type,
                exception=self.exception_diagnostic,
                scene_metrics=self.report.metrics() if self.report is not None else None,
                final_http_status=final_http_status,
            )
        if self.report is not None:
            record_scene_completeness(CUSTOM_PROFILE_ENDPOINT, self.report.metrics())
        _record(
            self.outcome,
            self.payload,
            error_stage=self.error_stage,
        )

    def reject(self) -> None:
        """Finalize a rejected request without counting it as production render traffic."""

        with self._record_lock:
            if self._recorded:
                return
            self._recorded = True
        if self.outcome == OUTCOME_ERROR:
            logger.warning(
                "custom_profile_card backend=skia rejected_request stage=%s error_type=%s",
                self.error_stage or "unknown",
                self.error_type or "unknown",
            )


class _CustomProfileSkiaStageError(Exception):
    """Internal carrier for a sanitized failure stage and any completed scene report."""

    def __init__(self, stage: str, *, report: CustomProfileSceneReport | None = None) -> None:
        super().__init__(stage)
        self.stage = stage
        self.report = report


def _new_builder(width: int, height: int, *, general_font_path: Path | None = None) -> IRBuilder:
    # export_format is HARDCODED png: the route pins PNG (the card is RGBA with real
    # transparency), regardless of the global EXPORT_IMAGE_FORMAT.
    return IRBuilder(
        width,
        height,
        assets_base_dir=str(ASSETS_BASE_DIR),
        font_dir=str(FONT_DIR),
        default_font=DEFAULT_FONT,
        bold_font=DEFAULT_BOLD_FONT,
        extra_fonts={_GENERAL_FONT_IR_NAME: str(general_font_path)} if general_font_path is not None else None,
        export_format="png",
        jpg_quality=JPG_QUALITY,
        max_node_pixels=CUSTOM_PROFILE_MAX_LAYER_PIXELS,
        max_scene_bytes=CUSTOM_PROFILE_MAX_SCENE_BYTES,
    )


class _SceneAssembler:
    """Accumulate an asset/font-backed scene without request-local Pillow pixels."""

    def __init__(self, builder: IRBuilder, canvas_size: tuple[int, int], max_mem_bytes: int) -> None:
        del canvas_size, max_mem_bytes
        self.builder = builder
        self.mem_bytes = 0
        # Subtree splice still accepts a registry, but Custom Profile native success requires
        # it to stay empty. Any node that would need request pixels is declined atomically.
        self.mem_images: dict[str, tuple] = {}

    def emit_sdf_quads(self, quads) -> bool:
        """Atomically emit a text element only when every glyph is asset/font-backed."""

        if any(not isinstance(quad, (DirectSdfFontQuad, DirectSdfAtlasQuad)) for quad in quads):
            return False
        for quad in quads:
            scalars = quad.scalars
            underlay = None
            if scalars.underlay is not None:
                u = scalars.underlay
                underlay = {
                    "color": list(u.color),
                    "scale": u.scale,
                    "w": u.w,
                    "shift": [u.shift_x, u.shift_y],
                }
            if isinstance(quad, DirectSdfFontQuad):
                asset_path = _relative_asset_path(quad.font_path)
                if asset_path is None:
                    raise ValueError("custom profile TMP source font is outside the configured asset root")
                resolved_path = (ASSETS_BASE_DIR / asset_path).resolve(strict=True)
                font_name = f"custom_profile_sdf_{hashlib.sha256(str(resolved_path).encode()).hexdigest()[:16]}"
                self.builder.register_extra_font(font_name, resolved_path)
                self.builder.sdf_font_quad(
                    font_name=font_name,
                    codepoint=quad.codepoint,
                    sample_size=quad.sample_size,
                    bbox=quad.bbox,
                    padding=quad.padding,
                    crop_padding=quad.crop_padding,
                    field_size=quad.field_size,
                    spread=quad.spread,
                    pos=(quad.left, quad.top),
                    size=quad.size,
                    affine=quad.affine,
                    face_color=scalars.face_color,
                    face_scale=scalars.face_scale,
                    face_w=scalars.face_w,
                    alpha=scalars.alpha,
                    underlay=underlay,
                )
                continue
            if isinstance(quad, DirectSdfAtlasQuad):
                asset_path = _relative_asset_path(quad.atlas_path)
                if asset_path is None:
                    raise ValueError("custom profile TMP atlas is outside the configured asset root")
                self.builder.sdf_atlas_quad(
                    path=asset_path,
                    atlas_size=quad.atlas_size,
                    crop=quad.crop,
                    field_size=quad.field_size,
                    pos=(quad.left, quad.top),
                    size=quad.size,
                    affine=quad.affine,
                    face_color=scalars.face_color,
                    face_scale=scalars.face_scale,
                    face_w=scalars.face_w,
                    alpha=scalars.alpha,
                    underlay=underlay,
                )
                continue
        return True


def _direct_text_quads(renderer: PNGRenderer, content: Any):
    """Sparse native glyph records for any TMP/SDF text element, or ``None``.

    ``prepare_direct_sdf_quads`` is already the renderer-neutral sparse path used by Pillow for
    decorative and oversized text. Restricting it here to ``is_decorative_text_item`` left
    ordinary rich/effected TMP text as whole RGBA ``mem:`` layers even though the exact same
    layout, warp, and shading descriptors were native-capable. Plain text still gets the cheaper
    IR ``Text`` path first; this is the final native TMP option before the element is declined.
    """
    if content.kind != "text" or not content.object_data.get("visible", False):
        return None
    if renderer.text_layout != "tmp" or renderer.tmp_text_render_mode != "sdf":
        return None
    return renderer.prepare_direct_sdf_quads(
        content.item,
        content.object_data,
        defer_static_atlas=True,
        defer_dynamic_font=True,
        source_metrics_only=True,
    )


def _is_empty_text_noop(renderer: PNGRenderer, content: Any) -> bool:
    """Return whether a visible text item has no drawable source characters.

    The compatibility renderer returns ``None`` for an empty/whitespace-only item. Treating that
    as a missing element made an otherwise complete native scene fall back to Pillow, which then
    drew exactly nothing. Keep tagged whitespace out of this shortcut because underline/strike
    tags can make spaces visible; the raw whitespace-only case is unambiguously a no-op.
    """

    if content.kind != "text" or not content.object_data.get("visible", False):
        return False
    text = str(content.item.get("text", ""))
    return not text.strip() and "<" not in text and ">" not in text


def _emit_native_simple_tmp_text(renderer: PNGRenderer, content: Any, scene: _SceneAssembler) -> bool:
    """Emit the strictly plain TMP subset as source-font Skia Text nodes.

    Python remains the TMP layout oracle; no Pillow/NumPy glyph field, local RGBA surface,
    resize, or ``mem:`` transport is created. Rich/decorative/effected text returns ``False`` and
    continues through the sparse asset/font-backed glyph path.
    """

    if content.kind != "text" or not content.object_data.get("visible", False):
        return False
    display_list = build_simple_tmp_text_display_list(renderer, content.item, content.object_data)
    if display_list is None:
        return False
    try:
        font_path = display_list.font.path.resolve(strict=True)
    except OSError:
        return False
    if not font_path.is_file():
        return False
    font_name = f"custom_profile_tmp_{hashlib.sha256(str(font_path).encode()).hexdigest()[:16]}"

    # Register only after the display-list builder has completed every eligibility and layout
    # check. An unsupported text must leave the scene untouched before the strict fallback.
    scene.builder.register_extra_font(font_name, font_path)
    with scene.builder.transform(display_list.transform.matrix):
        for op in display_list.ops:
            scene.builder.text(
                op.text,
                op.pos,
                "default",
                op.size,
                align=op.align,
                baseline=op.baseline,
                fill=op.fill,
                letter_spacing=op.letter_spacing,
                font_name=font_name,
            )
    return True


def _relative_asset_path(path) -> str | None:
    try:
        return path.resolve().relative_to(ASSETS_BASE_DIR.resolve()).as_posix()
    except (AttributeError, ValueError):
        return None


def _native_asset_info(asset_path: str) -> dict[str, Any] | None:
    """Rust-side asset metadata, or ``None`` for an older wheel.

    A wheel claiming the capability but returning a malformed result is broken and raises; only
    a genuinely absent API returns ``None`` so the whole native scene can fail open before any
    Pillow header or pixel operation.
    """

    native = load_native_renderer()
    info_fn = getattr(native, "asset_image_info", None)
    capability = int(getattr(native, "ASSET_INFO_CAPABILITY", 0) or 0)
    if capability < _REQUIRED_NATIVE_ASSET_INFO_CAPABILITY or not callable(info_fn):
        return None
    info = dict(info_fn(str(ASSETS_BASE_DIR), asset_path))
    width = int(info.get("width", 0))
    height = int(info.get("height", 0))
    ensure_raster_size(
        (width, height),
        max_pixels=CUSTOM_PROFILE_MAX_LAYER_PIXELS,
        label=f"custom profile native asset {asset_path}",
    )
    if int(info.get("mtime_ns", 0)) < 0 or int(info.get("file_size", 0)) < 0:
        raise ValueError(f"native honor asset info returned an invalid file identity: {asset_path}")
    return info


def _header_only_asset_ref(path, asset_path: str) -> AssetImageRef:
    """Build the shared honor source strictly from Rust-side image metadata."""

    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"custom profile honor asset is not a regular file: {resolved}")
    native_info = _native_asset_info(asset_path)
    if native_info is None:
        raise _NativeAssetInfoUnavailable("native asset-info capability is required")
    return AssetImageRef(
        path=resolved,
        size=(int(native_info["width"]), int(native_info["height"])),
        mode=str(native_info.get("mode") or "RGBA"),
        mtime_ns=int(native_info.get("mtime_ns", 0)),
        file_size=int(native_info.get("file_size", 0)),
    )


class _NativeGeneralTextMetrics:
    """Strict Skia font metrics used by the renderer-neutral General prefab builder."""

    def __init__(self, measure, font_path: Path, expected_font_name: str = "FOT-RodinNTLGPro-DB") -> None:
        self._measure_batch = measure
        self.font_path = font_path
        self.expected_font_name = expected_font_name
        self._cache: dict[tuple[str, int], dict[str, Any]] = {}

    @classmethod
    def create(
        cls,
        font_path: Path | None,
        *,
        expected_font_name: str = "FOT-RodinNTLGPro-DB",
    ) -> _NativeGeneralTextMetrics | None:
        if font_path is None:
            return None
        try:
            resolved = font_path.resolve(strict=True)
        except OSError:
            return None
        if not resolved.is_file():
            return None
        native = load_native_renderer()
        measure = getattr(native, "measure_text_batch", None)
        capability = int(getattr(native, "TEXT_METRICS_CAPABILITY", 0) or 0)
        if capability < _REQUIRED_NATIVE_TEXT_METRICS_CAPABILITY or not callable(measure):
            return None
        return cls(measure, resolved, expected_font_name)

    def measure(self, text: str, font: GeneralFontRef, size: int) -> dict[str, Any]:
        if font.name != self.expected_font_name:
            raise ValueError(f"unsupported native General prefab font: {font.name}")
        key = (str(text), int(size))
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        results = list(
            self._measure_batch(
                str(self.font_path.parent),
                str(self.font_path),
                [(key[0], float(key[1]))],
            )
        )
        if len(results) != 1:
            raise ValueError("native text metrics returned the wrong batch length")
        metric = dict(results[0])
        bbox = tuple(float(value) for value in metric.get("pillow_bbox", ()))
        if len(bbox) != 4 or not all(math.isfinite(value) for value in bbox):
            raise ValueError("native text metrics returned an invalid Pillow-relative bbox")
        ascent = float(metric.get("ascent", math.nan))
        descent = float(metric.get("descent", math.nan))
        if not math.isfinite(ascent) or not math.isfinite(descent) or ascent < 0.0 or descent < 0.0:
            raise ValueError("native text metrics returned invalid ascent/descent values")
        metric["pillow_bbox"] = bbox
        metric["ascent"] = ascent
        metric["descent"] = descent
        self._cache[key] = metric
        return metric

    def text_bbox(
        self,
        text: str,
        font: GeneralFontRef,
        size: int,
    ) -> tuple[float, float, float, float]:
        return self.measure(text, font, size)["pillow_bbox"]

    def anchor_placement(
        self,
        *,
        text: str,
        pos: tuple[float, float],
        size: int,
        anchor: str | None,
        font: GeneralFontRef = GeneralFontRef(),
    ) -> tuple[str, float]:
        """Translate the Pillow ``draw.text`` anchor into IR alignment + alphabetic baseline."""

        anchor = anchor or "la"
        if len(anchor) != 2 or anchor[0] not in "lmr" or anchor[1] not in "ams":
            raise ValueError(f"unsupported native General text anchor: {anchor!r}")
        align = {"l": "left", "m": "center", "r": "right"}[anchor[0]]
        metric = self.measure(text, font, size)
        y = float(pos[1])
        if anchor[1] == "a":
            baseline = y + metric["ascent"]
        elif anchor[1] == "m":
            baseline = y + (metric["ascent"] - metric["descent"]) * 0.5
        else:
            baseline = y
        return align, baseline

    def text_placement(self, op: GeneralTextOp) -> tuple[str, float]:
        return self.anchor_placement(
            text=op.text,
            pos=op.pos,
            size=op.size,
            anchor=op.anchor,
            font=op.font,
        )


def _existing_native_asset(path: Any) -> tuple[str, str | None]:
    """Classify an optional filesystem resource before an IR scene is mutated.

    ``outside`` is distinct from ``missing``: an explicitly supplied path that escapes the
    configured asset root must decline the whole element instead of being silently omitted.
    """

    if path is None:
        return "missing", None
    candidate = Path(path)
    asset_path = _relative_asset_path(candidate)
    if asset_path is None:
        return "outside", None
    try:
        resolved = candidate.resolve(strict=True)
    except OSError:
        return "missing", None
    if not resolved.is_file():
        return "missing", None
    return "ready", asset_path


@dataclass(frozen=True, slots=True)
class _PreparedCardDisplayList:
    display_list: CardDisplayList
    asset_paths: dict[int, str]
    text_placements: dict[int, tuple[str, float]]


_CARD_SAMPLING_MAP = {
    "nearest": "nearest",
    "bilinear": "linear",
    "bicubic": "catmull_rom",
    "lanczos": "pillow_lanczos",
}


def _prepare_native_card_cover(op: CardCoverArtOp, asset_paths: dict[int, str]) -> bool:
    status, asset_path = _existing_native_asset(op.path)
    if status != "ready" or asset_path is None:
        return False
    if op.cover_align != (0.5, 0.5):
        # Render IR Cover currently centers its source crop.
        return False
    asset_paths[id(op)] = asset_path
    return True


def _prepare_native_card_sprite(op: CardSpriteOp, asset_paths: dict[int, str]) -> bool:
    status, asset_path = _existing_native_asset(op.resource.path)
    if status == "outside":
        return False
    if status != "ready":
        fallback_status, asset_path = _existing_native_asset(op.resource.fallback_path)
        if fallback_status == "outside":
            return False
        status = fallback_status
    if status == "ready" and asset_path is not None:
        asset_paths[id(op)] = asset_path
        return True
    return op.resource.resource_policy != "required"


def _prepare_native_card_text(
    op: CardTextOp,
    metrics: _NativeGeneralTextMetrics | None,
    text_placements: dict[int, tuple[str, float]],
) -> bool:
    if metrics is None or op.font.name != "general" or not op.font.bold:
        return False
    text_placements[id(op)] = metrics.anchor_placement(
        text=op.text,
        pos=op.pos,
        size=op.size,
        anchor=op.anchor,
    )
    return True


def _prepare_native_card_rect(op: Any) -> bool:
    if not isinstance(op, CardRectOp):
        return False
    if op.radius <= 0.0 or op.blend != "src":
        return True
    translucent = (op.fill is not None and op.fill[3] < 255) or (op.outline is not None and op.outline[3] < 255)
    # RoundRect has no Porter-Duff Src switch yet.
    return not translucent


def _prepare_native_card_op(
    op: Any,
    metrics: _NativeGeneralTextMetrics | None,
    asset_paths: dict[int, str],
    text_placements: dict[int, tuple[str, float]],
) -> bool:
    if isinstance(op, CardAlphaMaskOp):
        # No active card path uses the legacy mask hook. Its rounded fallback and
        # alpha-multiply contract need a dedicated shared native primitive.
        return False
    if isinstance(op, CardCoverArtOp):
        return _prepare_native_card_cover(op, asset_paths)
    if isinstance(op, CardSpriteOp):
        return _prepare_native_card_sprite(op, asset_paths)
    if isinstance(op, CardTextOp):
        return _prepare_native_card_text(op, metrics, text_placements)
    return _prepare_native_card_rect(op)


def _prepare_native_card_display_list(
    display_list: CardDisplayList,
    metrics: _NativeGeneralTextMetrics | None,
) -> _PreparedCardDisplayList | None:
    """Resolve every card dependency without opening or decoding an image in Pillow."""

    asset_paths: dict[int, str] = {}
    text_placements: dict[int, tuple[str, float]] = {}
    for op in display_list.ops:
        if not _prepare_native_card_op(op, metrics, asset_paths, text_placements):
            return None
    return _PreparedCardDisplayList(display_list, asset_paths, text_placements)


def _emit_prepared_card_cover_art(
    scene: _SceneAssembler,
    prepared: _PreparedCardDisplayList,
    op: CardCoverArtOp,
) -> None:
    display_list = prepared.display_list
    cover_w = max(1, round(op.cover_size[0]))
    cover_h = max(1, round(op.cover_size[1]))
    crop_left = max(0, round((cover_w - display_list.size[0]) * op.crop_align[0]))
    crop_top = max(0, round((cover_h - display_list.size[1]) * op.crop_align[1]))
    scene.builder.image(
        prepared.asset_paths[id(op)],
        (-crop_left, -crop_top),
        (cover_w, cover_h),
        fit="cover",
        sampling=_CARD_SAMPLING_MAP[op.sampling],
        blend=op.blend,
    )


def _emit_prepared_card_rect(scene: _SceneAssembler, op: CardRectOp) -> None:
    left, top, right, bottom = op.rect
    if op.round_coordinates:
        left, top, right, bottom = (round(value) for value in (left, top, right, bottom))
    size = (max(0.0, right - left), max(0.0, bottom - top))
    if op.radius > 0.0:
        scene.builder.roundrect(
            (left, top),
            size,
            op.radius,
            fill=op.fill,
            stroke=op.outline,
            stroke_width=op.width,
        )
        return
    scene.builder.rect(
        (left, top),
        size,
        fill=op.fill,
        stroke=op.outline,
        stroke_width=op.width,
        blend=op.blend,
    )


def _emit_prepared_card_text(
    scene: _SceneAssembler,
    prepared: _PreparedCardDisplayList,
    op: CardTextOp,
) -> None:
    align, baseline = prepared.text_placements[id(op)]
    scene.builder.text(
        op.text,
        (float(op.pos[0]), baseline),
        "bold",
        float(op.size),
        align=align,
        baseline="alphabetic",
        fill=op.fill,
        font_name=_GENERAL_FONT_IR_NAME,
    )


def _emit_prepared_card_sprite(
    scene: _SceneAssembler,
    prepared: _PreparedCardDisplayList,
    op: CardSpriteOp,
) -> None:
    asset_path = prepared.asset_paths.get(id(op))
    if asset_path is None:
        return
    left, top, right, bottom = op.rect
    scene.builder.image(
        asset_path,
        (round(left), round(top)),
        (max(1, round(right - left)), max(1, round(bottom - top))),
        sampling=_CARD_SAMPLING_MAP[op.sampling],
    )


def _emit_prepared_card_ops(scene: _SceneAssembler, prepared: _PreparedCardDisplayList) -> None:
    """Replay one natural-size card display list into the current isolated surface."""

    for op in prepared.display_list.ops:
        if isinstance(op, CardCoverArtOp):
            _emit_prepared_card_cover_art(scene, prepared, op)
            continue
        if isinstance(op, CardRectOp):
            _emit_prepared_card_rect(scene, op)
            continue
        if isinstance(op, CardTextOp):
            _emit_prepared_card_text(scene, prepared, op)
            continue
        if not isinstance(op, CardSpriteOp):
            raise TypeError(f"unsupported native card display-list op: {type(op).__name__}")
        _emit_prepared_card_sprite(scene, prepared, op)


def _native_card_general_name(renderer: PNGRenderer, content: Any) -> str | None:
    if content.kind != "general" or not content.object_data.get("visible", False):
        return None
    resource_for = getattr(renderer, "image_resource_for", None)
    if not callable(resource_for) or not callable(getattr(renderer, "general_font_path", None)):
        return None
    file_name = str(resource_for("general", content.item).get("fileName", "") or "")
    return file_name if file_name in _NATIVE_CARD_GENERAL_PREFABS else None


def _prepare_native_leader_card(
    renderer: PNGRenderer,
    metrics: _NativeGeneralTextMetrics | None,
) -> list[tuple[_PreparedCardDisplayList, tuple[int, int]]] | None:
    deck = renderer.profile_context.get("userDeck") or {}
    card_id = int(deck.get("leader", 0) or 0) if isinstance(deck, dict) else 0
    if card_id <= 0:
        return None
    display_list = renderer.build_profile_leader_card_display_list(card_id)
    prepared = _prepare_native_card_display_list(display_list, metrics) if display_list is not None else None
    return [(prepared, (0, 0))] if prepared is not None else None


def _profile_deck_display_lists(renderer: PNGRenderer) -> list[CardDisplayList] | None:
    deck = renderer.profile_context.get("userDeck") or {}
    if not isinstance(deck, dict):
        return None
    display_lists: list[CardDisplayList] = []
    for index in range(5):
        card_id = int(deck.get(f"member{index + 1}", 0) or 0)
        display_list = renderer.build_profile_deck_card_display_list(card_id, leader=index == 0)
        display_lists.append(
            display_list or renderer.build_empty_profile_deck_card_display_list(GENERAL_DECK_CARD_RENDER_SIZE)
        )
    return display_lists


def _prepare_native_deck_cards(
    renderer: PNGRenderer,
    metrics: _NativeGeneralTextMetrics | None,
) -> list[tuple[_PreparedCardDisplayList, tuple[int, int]]] | None:
    display_lists = _profile_deck_display_lists(renderer)
    if display_lists is None:
        return None
    card_w, card_h = display_lists[0].render_size or display_lists[0].size
    gap = max(0.0, (GENERAL_NATIVE_SIZES["Deck"][0] - card_w * 5) / 4.0)
    start_x = max(0.0, (GENERAL_NATIVE_SIZES["Deck"][0] - (card_w * 5 + gap * 4)) / 2.0)
    top = GENERAL_NATIVE_SIZES["Deck"][1] - card_h
    prepared_cards: list[tuple[_PreparedCardDisplayList, tuple[int, int]]] = []
    for index, display_list in enumerate(display_lists):
        prepared = _prepare_native_card_display_list(display_list, metrics)
        if prepared is None:
            return None
        prepared_cards.append((prepared, (round(start_x + index * (card_w + gap)), round(top))))
    return prepared_cards


def _emit_native_card_general_contents(
    scene: _SceneAssembler,
    outer_size: tuple[int, int],
    prepared_cards: list[tuple[_PreparedCardDisplayList, tuple[int, int]]],
) -> None:
    for prepared, (left, top) in prepared_cards:
        display_list = prepared.display_list
        render_size = display_list.render_size or display_list.size
        if display_list.size == outer_size and (left, top) == (0, 0):
            _emit_prepared_card_ops(scene, prepared)
            continue
        with scene.builder.unity_subscene(
            size=display_list.size,
            anchor=(left + render_size[0] / 2.0, top + render_size[1] / 2.0),
            object_scale=(
                render_size[0] / display_list.size[0],
                render_size[1] / display_list.size[1],
            ),
            post_scale=(1.0, 1.0),
            rotation=0.0,
            sampling={
                "nearest": "nearest",
                "bilinear": "linear",
                "bicubic": "catmull_rom",
                "lanczos": "pillow_lanczos",
            }[display_list.final_sampling],
        ):
            _emit_prepared_card_ops(scene, prepared)


def _emit_native_card_general(renderer: PNGRenderer, content: Any, scene: _SceneAssembler) -> str | None:
    """Compose LeaderCard/Deck from their shared CardDisplayList without Pillow pixels."""

    file_name = _native_card_general_name(renderer, content)
    if file_name is None:
        return None
    metrics = _NativeGeneralTextMetrics.create(renderer.general_font_path())
    prepared_cards = (
        _prepare_native_leader_card(renderer, metrics)
        if file_name == "LeaderCard"
        else _prepare_native_deck_cards(renderer, metrics)
    )
    if prepared_cards is None:
        return None
    transform = _native_content_transform(renderer, content)
    if transform is None:
        return None
    sx, sy, angle = transform
    outer_size = GENERAL_NATIVE_SIZES[file_name]
    with scene.builder.unity_subscene(
        size=outer_size,
        anchor=renderer.unity_point(content.object_data.get("position", {})),
        object_scale=(sx, sy),
        post_scale=(renderer.position_scale_x, renderer.position_scale_y),
        rotation=angle,
    ):
        _emit_native_card_general_contents(scene, outer_size, prepared_cards)
    return "native"


def _emit_native_card_member(renderer: PNGRenderer, content: Any, scene: _SceneAssembler) -> bool:
    """Replay full/clip CardContentView from the same CardDisplayList as Pillow."""

    if content.kind != "card_member" or not content.object_data.get("visible", False):
        return False
    display_list_for = getattr(renderer, "build_card_member_display_list", None)
    font_path_for = getattr(renderer, "general_font_path", None)
    if not callable(display_list_for) or not callable(font_path_for):
        return False
    display_list = display_list_for(content.item)
    if display_list is None or display_list.render_size is not None:
        return False
    metrics = _NativeGeneralTextMetrics.create(font_path_for())
    prepared = _prepare_native_card_display_list(display_list, metrics)
    if prepared is None:
        return False

    scale = content.object_data.get("scale") or {}
    sx = float(scale.get("x") or 1.0)
    sy = float(scale.get("y") or sx or 1.0)
    if not all(math.isfinite(value) and value > 0.0 for value in (sx, sy)):
        return False
    angle = renderer.rotation_sign * unity_rotation_degrees(content.object_data.get("rotation", {}))
    with scene.builder.unity_subscene(
        size=display_list.size,
        anchor=renderer.unity_point(content.object_data.get("position", {})),
        object_scale=(sx, sy),
        post_scale=(renderer.position_scale_x, renderer.position_scale_y),
        rotation=angle,
    ):
        _emit_prepared_card_ops(scene, prepared)
    return True


@dataclass(frozen=True, slots=True)
class _PreparedGeneralDisplayList:
    display_list: GeneralPrefabDisplayList
    resource_paths: dict[int, str | None]
    text_placements: dict[int, tuple[str, float]]


_GENERAL_SAMPLING_MAP = {
    "nearest": "nearest",
    "bilinear": "linear",
    "bicubic": "catmull_rom",
    "lanczos": "catmull_rom",
}


def _native_general_asset_paths(renderer: PNGRenderer, file_name: str) -> dict[str, Path | None]:
    asset_paths: dict[str, Path | None] = {}
    if file_name == "ChallengeLive":
        data = renderer.profile_context.get("userChallengeLiveSoloResult") or {}
        if isinstance(data, dict):
            character_id = int(data.get("characterId", 0) or 0)
            asset_paths["challenge_character_icon"] = renderer.chara_icon_path(character_id)
    elif file_name in {"CharacterRankAndChallengeStage", "CharacterRankAndChallengeStageScroll"}:
        for _nickname, character_id in CHARA_LIST:
            if character_id is not None:
                asset_paths[f"character_rank_icon:{character_id}"] = renderer.chara_icon_path(character_id)
    elif file_name == "StoryFavorite":
        stories = renderer.profile_context.get("userStoryFavorites") or []
        if isinstance(stories, list):
            for story in stories:
                if isinstance(story, dict):
                    asset_paths[story_favorite_asset_key(story)] = renderer.story_favorite_image_path(story)
    return asset_paths


def _native_general_labels(renderer: PNGRenderer) -> dict[str, str]:
    return {
        "comment_title": renderer.general_text("comment_title"),
        "total_power": renderer.general_text("total_power"),
        "multi_live_title": renderer.general_text("multi_live_title"),
        "multi_live_count_suffix": renderer.general_text("multi_live_count_suffix"),
        "challenge_live_title": renderer.general_text("challenge_live_title"),
        "challenge_live_solo": renderer.general_text("challenge_live_solo"),
        "character_rank_tab": renderer.general_text("character_rank_tab"),
        "challenge_stage_tab": renderer.general_text("challenge_stage_tab"),
        "music_clear": renderer.general_text("music_clear"),
        "music_full_combo": renderer.general_text("music_full_combo"),
        "music_all_perfect": renderer.general_text("music_all_perfect"),
        "story_favorite_title": renderer.general_text("story_favorite_title"),
        "not_set": renderer.general_text("not_set"),
    }


def _build_native_general_display_list(
    renderer: PNGRenderer,
    file_name: str,
    metrics: _NativeGeneralTextMetrics,
) -> GeneralPrefabDisplayList | None:
    return build_general_prefab_display_list(
        file_name,
        size=GENERAL_NATIVE_SIZES[file_name],
        profile_context=renderer.profile_context,
        labels=_native_general_labels(renderer),
        metrics=metrics,
        palette=GENERAL_PREFAB_PALETTE,
        asset_paths=_native_general_asset_paths(renderer, file_name),
        music_difficulties=GENERAL_MUSIC_DIFFICULTIES,
        story_favorite_resources=renderer.story_favorite_resources,
    )


def _walk_general_ops(ops: tuple[GeneralPrefabOp, ...]):
    for op in ops:
        yield op
        if isinstance(op, GeneralViewportOp):
            yield from _walk_general_ops(op.children)


def _resolve_native_general_sprite(renderer: PNGRenderer, op: GeneralSpriteOp) -> tuple[bool, str | None]:
    path = renderer.unity_ui_sprite_path(op.name)
    if path is None:
        return op.resource_policy != "required", None
    asset_path = _relative_asset_path(path)
    return asset_path is not None, asset_path


def _resolve_native_general_sprite_choice(
    renderer: PNGRenderer,
    op: GeneralSpriteChoiceOp,
) -> tuple[bool, str | None]:
    for name in op.names:
        path = renderer.unity_ui_sprite_path(name)
        if path is None:
            continue
        asset_path = _relative_asset_path(path)
        return asset_path is not None, asset_path
    return True, None


def _resolve_native_general_asset(op: GeneralAssetImageOp) -> tuple[bool, str | None]:
    if op.fit == "cover" and op.align != (0.5, 0.5):
        # IR Image cover is deliberately centered. A future non-centered display-list
        # operation must decline instead of silently changing its crop.
        return False, None
    status, asset_path = _existing_native_asset(op.path)
    if status == "ready":
        return True, asset_path
    if status == "outside" or op.resource_policy == "required":
        return False, None
    return True, None


def _prepare_native_general_op(
    renderer: PNGRenderer,
    metrics: _NativeGeneralTextMetrics,
    op: GeneralPrefabOp,
    resource_paths: dict[int, str | None],
    text_placements: dict[int, tuple[str, float]],
) -> bool:
    op_key = id(op)
    if isinstance(op, GeneralSpriteOp):
        ready, resource_paths[op_key] = _resolve_native_general_sprite(renderer, op)
        return ready
    if isinstance(op, GeneralSpriteChoiceOp):
        ready, resource_paths[op_key] = _resolve_native_general_sprite_choice(renderer, op)
        if ready and resource_paths[op_key] is None and op.fallback_text is not None:
            text_placements[id(op.fallback_text)] = metrics.text_placement(op.fallback_text)
        return ready
    if isinstance(op, GeneralAssetImageOp):
        ready, resource_paths[op_key] = _resolve_native_general_asset(op)
        return ready
    if isinstance(op, GeneralTextOp):
        text_placements[op_key] = metrics.text_placement(op)
        return True
    return isinstance(op, (GeneralRoundedRectOp, GeneralViewportOp))


def _prepare_native_general_display_list(
    renderer: PNGRenderer,
    metrics: _NativeGeneralTextMetrics,
    display_list: GeneralPrefabDisplayList,
) -> _PreparedGeneralDisplayList | None:
    resource_paths: dict[int, str | None] = {}
    text_placements: dict[int, tuple[str, float]] = {}
    for op in _walk_general_ops(display_list.ops):
        if not _prepare_native_general_op(renderer, metrics, op, resource_paths, text_placements):
            return None
    return _PreparedGeneralDisplayList(display_list, resource_paths, text_placements)


def _general_op_geometry(rect: tuple[float, float, float, float]) -> tuple[tuple[int, int], tuple[int, int]]:
    left, top, right, bottom = rect
    return (round(left), round(top)), (max(1, round(right - left)), max(1, round(bottom - top)))


def _emit_native_general_rounded_rect(scene: _SceneAssembler, op: GeneralRoundedRectOp) -> None:
    left, top, right, bottom = op.rect
    if op.round_coordinates:
        left, top, right, bottom = (round(value) for value in (left, top, right, bottom))
    scene.builder.roundrect(
        (left, top),
        (max(0.0, right - left), max(0.0, bottom - top)),
        op.radius,
        fill=op.fill,
        stroke=op.outline,
        stroke_width=op.width,
    )


def _emit_native_general_text(
    scene: _SceneAssembler,
    prepared: _PreparedGeneralDisplayList,
    op: GeneralTextOp,
) -> None:
    align, baseline = prepared.text_placements[id(op)]
    scene.builder.text(
        op.text,
        (float(op.pos[0]), baseline),
        "bold",
        float(op.size),
        align=align,
        baseline="alphabetic",
        fill=op.fill,
        font_name=_GENERAL_FONT_IR_NAME,
    )


def _emit_native_general_sprite(
    scene: _SceneAssembler,
    prepared: _PreparedGeneralDisplayList,
    op: GeneralSpriteOp,
) -> None:
    asset_path = prepared.resource_paths[id(op)]
    if asset_path is None:
        if op.fallback is not None:
            _emit_native_general_rounded_rect(scene, op.fallback)
        return
    pos, size = _general_op_geometry(op.rect)
    tint = image_tint(unity_tint_rgba(op.tint), "recolor") if op.tint is not None else None
    if op.sliced_border is not None:
        scene.builder.sliced_image(path=asset_path, pos=pos, size=size, border=op.sliced_border, tint=tint)
        return
    scene.builder.image(asset_path, pos, size, sampling=_GENERAL_SAMPLING_MAP[op.sampling], tint=tint)


def _emit_native_general_sprite_choice(
    scene: _SceneAssembler,
    prepared: _PreparedGeneralDisplayList,
    op: GeneralSpriteChoiceOp,
) -> None:
    asset_path = prepared.resource_paths[id(op)]
    if asset_path is None:
        if op.fallback_text is not None:
            _emit_native_general_text(scene, prepared, op.fallback_text)
        return
    pos, size = _general_op_geometry(op.rect)
    tint = image_tint(unity_tint_rgba(op.tint), "recolor") if op.tint is not None else None
    scene.builder.image(asset_path, pos, size, sampling=_GENERAL_SAMPLING_MAP[op.sampling], tint=tint)


def _emit_native_general_asset(
    scene: _SceneAssembler,
    prepared: _PreparedGeneralDisplayList,
    op: GeneralAssetImageOp,
) -> None:
    asset_path = prepared.resource_paths[id(op)]
    if asset_path is None:
        if op.fallback is not None:
            _emit_native_general_rounded_rect(scene, op.fallback)
        return
    pos, size = _general_op_geometry(op.rect)
    sampling = "pillow_lanczos" if op.sampling == "lanczos" else _GENERAL_SAMPLING_MAP[op.sampling]
    if op.clip_radius is None:
        scene.builder.image(asset_path, pos, size, fit=op.fit, sampling=sampling)
        return
    # The legacy composer multiplies a discrete ImageDraw L mask into the resized alpha.
    # ``pillow_rrect`` reproduces that contract without a request-local Pillow raster.
    with scene.builder.group(offset=pos, size=size, clip=clip_pillow_rrect(op.clip_radius)):
        scene.builder.image(asset_path, (0, 0), size, fit=op.fit, sampling=sampling)


def _emit_native_general_op(
    scene: _SceneAssembler,
    prepared: _PreparedGeneralDisplayList,
    op: GeneralPrefabOp,
) -> None:
    if isinstance(op, GeneralSpriteOp):
        _emit_native_general_sprite(scene, prepared, op)
        return
    if isinstance(op, GeneralSpriteChoiceOp):
        _emit_native_general_sprite_choice(scene, prepared, op)
        return
    if isinstance(op, GeneralRoundedRectOp):
        _emit_native_general_rounded_rect(scene, op)
        return
    if isinstance(op, GeneralAssetImageOp):
        _emit_native_general_asset(scene, prepared, op)
        return
    if isinstance(op, GeneralViewportOp):
        with scene.builder.group(offset=op.offset, size=op.viewport_size, clip={"kind": "rect"}):
            _emit_native_general_ops(scene, prepared, op.children)
        return
    if not isinstance(op, GeneralTextOp):
        raise TypeError(f"unsupported GeneralContentView display-list op: {type(op).__name__}")
    _emit_native_general_text(scene, prepared, op)


def _emit_native_general_ops(
    scene: _SceneAssembler,
    prepared: _PreparedGeneralDisplayList,
    ops: tuple[GeneralPrefabOp, ...],
) -> None:
    for op in ops:
        _emit_native_general_op(scene, prepared, op)


def _emit_native_general(renderer: PNGRenderer, content: Any, scene: _SceneAssembler) -> str | None:
    """Replay shared GeneralContentView prefabs without a Pillow pixel surface.

    ``"native"`` means a subscene was appended, ``"noop"`` preserves a prefab's historical
    legal no-render result, and ``None`` declines to the compatibility renderer.
    """

    if content.kind != "general" or not content.object_data.get("visible", False):
        return None
    resource_for = getattr(renderer, "image_resource_for", None)
    font_path_for = getattr(renderer, "general_font_path", None)
    if not callable(resource_for) or not callable(font_path_for):
        return None
    resource = resource_for("general", content.item)
    file_name = str(resource.get("fileName", "") or "")
    if file_name not in _NATIVE_GENERAL_PREFABS:
        return None
    metrics = _NativeGeneralTextMetrics.create(font_path_for())
    if metrics is None:
        return None

    display_list = _build_native_general_display_list(renderer, file_name, metrics)
    if display_list is None:
        return "noop"
    prepared = _prepare_native_general_display_list(renderer, metrics, display_list)
    if prepared is None:
        return None
    transform = _native_content_transform(renderer, content)
    if transform is None:
        return None
    sx, sy, angle = transform
    with scene.builder.unity_subscene(
        size=display_list.size,
        anchor=renderer.unity_point(content.object_data.get("position", {})),
        object_scale=(sx, sy),
        post_scale=(renderer.position_scale_x, renderer.position_scale_y),
        rotation=angle,
    ):
        _emit_native_general_ops(scene, prepared, display_list.ops)
    return "native"


def _native_honor_sources(
    renderer: PNGRenderer,
    request: HonorRequest,
) -> tuple[str, dict[str, ImageSource | None] | None]:
    """Resolve one honor branch to lazy, root-confined image refs.

    The backend-neutral manifest owns branch selection and required/optional semantics.
    ``unrenderable`` may try a lower-priority request key; ``hybrid`` preserves the selected
    request's precedence and declines the whole element.
    """

    def source_factory(path):
        asset_path = _relative_asset_path(path)
        if asset_path is None:
            raise ValueError(f"honor asset is outside ASSETS_BASE_DIR: {path}")
        return _header_only_asset_ref(path, asset_path)

    try:
        resolution = resolve_honor_assets(
            request,
            path_resolver=renderer.resolve_request_asset_path,
            source_factory=source_factory,
        )
    except _NativeAssetInfoUnavailable:
        return "hybrid", None
    return resolution.status, None if resolution.sources is None else dict(resolution.sources)


def _native_honor_candidates(
    renderer: PNGRenderer,
    content: Any,
) -> Iterator[HonorRequest | dict[str, Any] | None] | None:
    """Honor requests in Pillow precedence order, with masterdata derivation last."""

    full_size = bool_from_profile(content.item.get("fullSize", False))
    if content.kind == "honor":
        honor_id = content_data_id("honor", content.item)
        level = renderer.user_honor_level_for(honor_id)
        keys = (renderer.honor_slot_key(honor_id, level, full_size), str(honor_id))

        def ordinary_candidates():
            yield from (renderer.honor_requests.get(key) for key in keys)
            build_request = getattr(renderer, "build_masterdata_honor_request", None)
            yield build_request(honor_id, level, full_size) if callable(build_request) else None

        return ordinary_candidates()
    if content.kind != "bonds_honor":
        return None

    honor_id = content_data_id("bonds_honor", content.item)
    level = renderer.user_bonds_honor_level_for(honor_id)
    word_id = int(content.item.get("wordId", 0) or 0)
    inverse = bool_from_profile(content.item.get("inverse", False))
    use_unit_virtual_singer = bool_from_profile(content.item.get("useUnitVirtualSinger", False))
    keys = [
        renderer.bonds_honor_slot_key(
            honor_id,
            level,
            full_size,
            word_id,
            inverse,
            use_unit_virtual_singer,
        )
    ]
    if use_unit_virtual_singer:
        keys.append(renderer.bonds_honor_slot_key(honor_id, level, full_size, word_id, inverse))
    keys.append(str(honor_id))

    def bonds_candidates():
        yield from (renderer.bonds_honor_requests.get(key) for key in keys)
        build_request = getattr(renderer, "build_masterdata_bonds_honor_request", None)
        yield build_request(content.item, full_size) if callable(build_request) else None

    return bonds_candidates()


def _lower_native_honor_request(
    renderer: PNGRenderer,
    request: HonorRequest,
) -> tuple[str, NativeSubtree | None]:
    source_status, images = _native_honor_sources(renderer, request)
    if source_status == "unrenderable":
        return "unrenderable", None
    if source_status != "ready" or images is None:
        return "hybrid", None
    canvas = build_honor_badge_canvas(request, images)
    if canvas is None:
        return "missing", None
    try:
        badge = lower_canvas_subtree(canvas, require_asset_backed=True, export_format="png")
    except NativeSubtreeError:
        return "hybrid", None
    return "ready", badge


def _honor_request_from_candidate(candidate: Any) -> HonorRequest | None:
    if isinstance(candidate, HonorRequest):
        return candidate
    if isinstance(candidate, dict):
        return HonorRequest.model_validate(candidate)
    return None


def _emit_native_honor_badge(
    renderer: PNGRenderer,
    content: Any,
    scene: _SceneAssembler,
    badge: NativeSubtree,
) -> bool:
    scale = content.object_data.get("scale") or {}
    sx = float(scale.get("x", 1.0))
    sy = float(scale.get("y", sx))
    if not all(math.isfinite(value) and value > 0.0 for value in (sx, sy)):
        return False
    angle = renderer.rotation_sign * unity_rotation_degrees(content.object_data.get("rotation", {}))
    with scene.builder.unity_subscene(
        size=badge.size,
        anchor=renderer.unity_point(content.object_data.get("position", {})),
        object_scale=(sx, sy),
        post_scale=(renderer.position_scale_x, renderer.position_scale_y),
        rotation=angle,
    ):
        badge.splice_into(
            scene.builder,
            scene.mem_images,
            namespace="custom.honor.content",
            require_asset_backed=True,
        )
    return True


def _emit_native_honor(renderer: PNGRenderer, content: Any, scene: _SceneAssembler) -> bool:
    """Lower normal, birthday, bonds, and empty badges through ``HonorBadgeBox``.

    The tree first renders at the badge's natural size inside ``UnitySubscene``. Rust then
    applies the same two sequential Unity scale stages and center-pivot placement as the Pillow
    custom-profile path. Bonds full-resize/destination-clip operations stay asset-backed.
    """

    if not content.object_data.get("visible", False):
        return False
    candidates = _native_honor_candidates(renderer, content)
    if candidates is None:
        return False

    seen_candidates: set[int] = set()

    for candidate in candidates:
        if candidate is None or id(candidate) in seen_candidates:
            continue
        seen_candidates.add(id(candidate))
        request = _honor_request_from_candidate(candidate)
        if request is None:
            return False
        status, badge = _lower_native_honor_request(renderer, request)
        if status == "unrenderable":
            continue
        if status != "ready" or badge is None:
            return False
        return _emit_native_honor_badge(renderer, content, scene, badge)
    return False


def _native_profile_honor_badge(
    renderer: PNGRenderer,
    row: dict[str, Any],
    *,
    full_size: bool,
) -> tuple[str, NativeSubtree | None]:
    """Resolve one HonorDeck slot with the exact Pillow request-key precedence.

    ``"ready"`` carries an entirely asset-backed badge tree. ``"missing"`` means every profile
    and ordinary-honor candidate was absent or lacked its required base. ``"hybrid"`` means a
    renderable higher-priority request needs a badge mode or supplied resource the native path
    cannot reproduce. Both non-ready states make the whole deck decline: an expected profile
    slot may never disappear behind a native-success classification.
    """

    seq = int(row.get("seq", 0) or 0)
    honor_id = int(row.get("honorId", 0) or 0)
    level = int(row.get("honorLevel", 0) or 0)
    candidates = honor_deck_request_candidates(
        seq=seq,
        honor_id=honor_id,
        honor_level=level,
        full_size=full_size,
    )
    for payload in _native_profile_honor_payloads(renderer, candidates):
        request = HonorRequest.model_validate(payload)
        status, badge = _lower_native_honor_request(renderer, request)
        if status in {"unrenderable", "missing"}:
            continue
        if status != "ready" or badge is None:
            return "hybrid", None
        return "ready", badge
    return "missing", None


def _native_profile_honor_payloads(renderer: PNGRenderer, candidates: Any) -> Iterator[dict[str, Any]]:
    seen_payloads: set[int] = set()
    request_maps = (
        (renderer.profile_honor_requests, candidates.profile_keys),
        (renderer.honor_requests, candidates.ordinary_keys),
    )
    for request_map, keys in request_maps:
        for key in keys:
            payload = request_map.get(key)
            if not isinstance(payload, dict) or id(payload) in seen_payloads:
                continue
            seen_payloads.add(id(payload))
            yield payload


def _prepare_native_honor_deck_slots(
    renderer: PNGRenderer,
    plan: Any,
) -> list[tuple[NativeSubtree, tuple[int, int, int, int], int]] | None:
    slots: list[tuple[NativeSubtree, tuple[int, int, int, int], int]] = []
    for slot in plan.slots:
        status, badge = _native_profile_honor_badge(
            renderer,
            dict(slot.profile_row),
            full_size=slot.full_size,
        )
        if status != "ready" or badge is None:
            return None
        # Legacy paste_in_rect uses Pillow LANCZOS when a supplied badge has the wrong natural
        # size. Native custom-profile does not claim that filter yet; decline rather than
        # silently substituting Catmull-Rom through a nested subscene.
        if badge.size != slot.target_size:
            return None
        slots.append((badge, (*slot.target_xy, *slot.target_size), slot.index))
    return slots


def _native_honor_deck_background(renderer: PNGRenderer, plan: Any) -> tuple[bool, str | None]:
    assert plan.panel is not None
    background_path = renderer.unity_ui_sprite_path(plan.panel.sprite_name)
    if background_path is None:
        return True, None
    background_asset = _relative_asset_path(background_path)
    return background_asset is not None, background_asset


def _native_content_transform(renderer: PNGRenderer, content: Any) -> tuple[float, float, float] | None:
    scale = content.object_data.get("scale") or {}
    sx = float(scale.get("x") or 1.0)
    sy = float(scale.get("y") or sx or 1.0)
    if not all(math.isfinite(value) and value > 0.0 for value in (sx, sy)):
        return None
    angle = renderer.rotation_sign * unity_rotation_degrees(content.object_data.get("rotation", {}))
    return sx, sy, angle


def _emit_native_honor_deck_contents(
    scene: _SceneAssembler,
    plan: Any,
    slots: list[tuple[NativeSubtree, tuple[int, int, int, int], int]],
    background_asset: str | None,
) -> None:
    if background_asset is not None:
        scene.builder.sliced_image(
            path=background_asset,
            pos=(round(plan.panel.target_rect[0]), round(plan.panel.target_rect[1])),
            size=(
                round(plan.panel.target_rect[2] - plan.panel.target_rect[0]),
                round(plan.panel.target_rect[3] - plan.panel.target_rect[1]),
            ),
            border=plan.panel.sliced_border,
            tint=image_tint(unity_tint_rgba(plan.panel.tint), "recolor"),
        )
    for badge, (left, top, width, height), slot_index in slots:
        with scene.builder.unity_subscene(
            size=badge.size,
            anchor=(left + width / 2.0, top + height / 2.0),
            object_scale=(1.0, 1.0),
            post_scale=(1.0, 1.0),
            rotation=0.0,
        ):
            badge.splice_into(
                scene.builder,
                scene.mem_images,
                namespace=f"custom.honor.deck.{slot_index}",
                require_asset_backed=True,
            )


def _emit_native_honor_deck(renderer: PNGRenderer, content: Any, scene: _SceneAssembler) -> str | None:
    """Compose the profile HonorDeck prefab without a Pillow surface.

    Slot requests are resolved before the scene is mutated. Every row in ``userProfileHonors``
    is an expected slot: a missing, unsupported, or corrupt slot declines the whole element so
    scene-completeness telemetry cannot call a partial deck native.
    """

    if content.kind != "general" or not content.object_data.get("visible", False):
        return None
    resource_for = getattr(renderer, "image_resource_for", None)
    if not callable(resource_for):
        return None
    resource = resource_for("general", content.item)
    if str(resource.get("fileName", "") or "") != "HonorDeck":
        return None
    plan = build_honor_deck_plan(renderer.profile_context.get("userProfileHonors", []) or [])
    if plan is None:
        return None

    slots = _prepare_native_honor_deck_slots(renderer, plan)
    if slots is None:
        return None
    background_ready, background_asset = _native_honor_deck_background(renderer, plan)
    if not background_ready:
        return None
    transform = _native_content_transform(renderer, content)
    if transform is None:
        return None
    sx, sy, angle = transform
    size = plan.natural_size
    with scene.builder.unity_subscene(
        size=size,
        anchor=renderer.unity_point(content.object_data.get("position", {})),
        object_scale=(sx, sy),
        post_scale=(renderer.position_scale_x, renderer.position_scale_y),
        rotation=angle,
    ):
        _emit_native_honor_deck_contents(scene, plan, slots, background_asset)
    return "native"


@dataclass(frozen=True)
class _PreparedNativeOmikuji:
    display_list: Any
    font_path: Path
    asset_paths: dict[int, str]
    text_placements: dict[int, tuple[str, float]]


def _prepare_native_omikuji(renderer: PNGRenderer, content: Any) -> _PreparedNativeOmikuji | None:
    if content.kind != "collection" or not content.object_data.get("visible", False):
        return None
    resource = renderer.image_resource_for("collection", content.item)
    if str(resource.get("customProfileResourceCollectionType", "none") or "none") != "omikuji":
        return None
    target_id = int(content.item.get("targetId", 0) or 0)
    omikuji = renderer.omikujis.get(target_id)
    if not isinstance(omikuji, dict):
        return None
    background_path = renderer.omikuji_background_asset_path(omikuji)
    fortune_path = renderer.omikuji_asset_path(omikuji, "fortune")
    if background_path is None or fortune_path is None:
        return None
    background_asset = _relative_asset_path(background_path)
    fortune_asset = _relative_asset_path(fortune_path)
    if background_asset is None or fortune_asset is None:
        return None
    background_info = _native_asset_info(background_asset)
    fortune_info = _native_asset_info(fortune_asset)
    if background_info is None or fortune_info is None:
        return None
    font_path = renderer.omikuji_font_path(decorative=False)
    metrics = _NativeGeneralTextMetrics.create(font_path, expected_font_name="omikuji")
    if metrics is None or font_path is None:
        return None

    display_list = build_omikuji_display_list(
        omikuji,
        background_path=background_path,
        background_size=(int(background_info["width"]), int(background_info["height"])),
        fortune_path=fortune_path,
        fortune_size=(int(fortune_info["width"]), int(fortune_info["height"])),
    )
    prepared_ops = _prepare_native_omikuji_ops(display_list.ops, metrics)
    if prepared_ops is None:
        return None
    asset_paths, text_placements = prepared_ops
    return _PreparedNativeOmikuji(display_list, font_path, asset_paths, text_placements)


def _prepare_native_omikuji_ops(
    ops: list[Any],
    metrics: _NativeGeneralTextMetrics,
) -> tuple[dict[int, str], dict[int, tuple[str, float]]] | None:
    asset_paths: dict[int, str] = {}
    text_placements: dict[int, tuple[str, float]] = {}
    font_ref = GeneralFontRef(name="omikuji")
    for op in ops:
        if isinstance(op, OmikujiAssetOp):
            asset_path = _relative_asset_path(Path(op.path))
            if asset_path is None:
                return None
            asset_paths[id(op)] = asset_path
        elif isinstance(op, OmikujiTextOp):
            if op.decorative:
                return None
            text_placements[id(op)] = metrics.anchor_placement(
                text=op.text,
                pos=op.pos if abs(op.rotation) < 1.0e-6 else (0.0, 0.0),
                size=op.size,
                anchor=op.anchor,
                font=font_ref,
            )
    return asset_paths, text_placements


def _emit_native_omikuji_text(
    scene: _SceneAssembler,
    op: OmikujiTextOp,
    placement: tuple[str, float],
) -> None:
    align, baseline = placement
    text_args = (
        op.text,
        "default",
        op.size,
    )
    text_kwargs = {
        "align": align,
        "baseline": "alphabetic",
        "fill": op.fill,
        "font_name": _OMIKUJI_FONT_IR_NAME,
    }
    if abs(op.rotation) < 1.0e-6:
        scene.builder.text(text_args[0], (op.pos[0], baseline), *text_args[1:], **text_kwargs)
        return
    theta = math.radians(op.rotation)
    with scene.builder.transform(
        (
            math.cos(theta),
            -math.sin(theta),
            op.pos[0],
            math.sin(theta),
            math.cos(theta),
            op.pos[1],
        )
    ):
        scene.builder.text(text_args[0], (0.0, baseline), *text_args[1:], **text_kwargs)


def _emit_native_omikuji_ops(scene: _SceneAssembler, prepared: _PreparedNativeOmikuji) -> None:
    for op in prepared.display_list.ops:
        if isinstance(op, OmikujiAssetOp):
            left, top, right, bottom = op.rect
            scene.builder.image(
                prepared.asset_paths[id(op)],
                (round(left), round(top)),
                (max(1, round(right - left)), max(1, round(bottom - top))),
                sampling={
                    "nearest": "nearest",
                    "bilinear": "linear",
                    "bicubic": "catmull_rom",
                    "lanczos": "pillow_lanczos",
                }[op.sampling],
                blend=op.blend,
            )
        elif isinstance(op, OmikujiRectOp):
            left, top, right, bottom = op.rect
            scene.builder.rect((left, top), (right - left, bottom - top), fill=op.fill)
        else:
            _emit_native_omikuji_text(scene, op, prepared.text_placements[id(op)])


def _emit_native_omikuji_collection(renderer: PNGRenderer, content: Any, scene: _SceneAssembler) -> bool:
    """Replay the shared omikuji result-card display list without a Pillow surface."""

    prepared = _prepare_native_omikuji(renderer, content)
    if prepared is None:
        return False
    transform = _native_content_transform(renderer, content)
    if transform is None:
        return False
    sx, sy, angle = transform

    scene.builder.register_extra_font(_OMIKUJI_FONT_IR_NAME, prepared.font_path)
    with scene.builder.unity_subscene(
        size=prepared.display_list.size,
        anchor=renderer.unity_point(content.object_data.get("position", {})),
        object_scale=(sx, sy),
        post_scale=(renderer.position_scale_x, renderer.position_scale_y),
        rotation=angle,
    ):
        _emit_native_omikuji_ops(scene, prepared)
    return True


def _emit_native_asset_image(renderer: PNGRenderer, content: Any, scene: _SceneAssembler) -> bool:
    """Lower intrinsic-size ImageContentView assets without opening them in Pillow."""

    if not content.object_data.get("visible", False):
        return False
    path = None
    if content.kind in STATIC_IMAGE_CONTENT_KINDS:
        resource = renderer.image_resource_for(content.kind, content.item)
        path = renderer.resource_path(resource)
    elif content.kind == "stamp":
        stamp_id = int(content.item.get("id", content.item.get("stampId", 0)) or 0)
        stamp_asset = renderer.stamp_assets.get(stamp_id, {})
        image_path = str(stamp_asset.get("imagePath", stamp_asset.get("image_path", "")) or "").strip()
        if image_path:
            path = renderer.resolve_request_asset_path(image_path)
        if path is None:
            path = renderer.stamp_resource_path(renderer.image_resource_for("stamp", content.item))
    else:
        return False
    if path is None or (asset_path := _relative_asset_path(path)) is None:
        return False

    scale = content.object_data.get("scale") or {}
    sx = float(scale.get("x", 1.0))
    sy = float(scale.get("y", sx))
    if not all(math.isfinite(value) and value > 0.0 for value in (sx, sy)):
        return False
    angle = renderer.rotation_sign * unity_rotation_degrees(content.object_data.get("rotation", {}))
    scene.builder.unity_image(
        path=asset_path,
        anchor=renderer.unity_point(content.object_data.get("position", {})),
        object_scale=(sx, sy),
        post_scale=(renderer.position_scale_x, renderer.position_scale_y),
        rotation=angle,
    )
    return True


def _emit_native_shape(renderer: PNGRenderer, content: Any, scene: _SceneAssembler) -> bool:
    """Lower the production SDF-shape configuration directly to an asset-backed Rust node."""

    if (
        content.kind != "shape"
        or not content.object_data.get("visible", False)
        or renderer.shape_outline_mode != "sdf"
        or not renderer.shape_sdf_screen_fwidth
    ):
        return False
    resource = renderer.shapes.get(int(content.item.get("id", 0)), {})
    path = renderer.shape_resource_path(resource)
    if path is None:
        return False
    resource_file = str(resource.get("fileName", "")).strip().lower()
    if resource_file == "triangle" and renderer.triangle_mode != "asset":
        return False
    asset_path = _relative_asset_path(path)
    if asset_path is None:
        return False

    scale = content.object_data.get("scale") or {}
    sx = float(scale.get("x", 1.0))
    sy = float(scale.get("y", sx))
    if not all(math.isfinite(value) and value > 0.0 for value in (sx, sy)):
        return False
    outline_size = max(0.0, min(1.0, float(content.item.get("outlineSize", 0.0) or 0.0)))
    outer_fill_ratio = max(
        0.0,
        min(
            1.0,
            outline_size
            * SHAPE_NATIVE_OUTLINE_FILL_RATIO_FACTOR
            * renderer.shape_sdf_ratio_scale
            * renderer.shape_sdf_outer_factor,
        ),
    )
    face_dilate = max(
        -1.0,
        min(1.0, outline_size * renderer.shape_sdf_ratio_scale * renderer.shape_sdf_face_factor),
    )
    fill_color = renderer.colors.get(int(content.item.get("colorId", 0)), "#ffffff")
    outline_color = renderer.colors.get(int(content.item.get("outlineColorId", 0)), "#ffffff")
    angle = renderer.rotation_sign * unity_rotation_degrees(content.object_data.get("rotation", {}))
    scene.builder.sdf_shape(
        path=asset_path,
        anchor=renderer.unity_point(content.object_data.get("position", {})),
        sdf_scale=(sx, sy),
        post_scale=(renderer.position_scale_x, renderer.position_scale_y),
        rotation=angle,
        field_channel="alpha" if renderer.shape_sdf_source == "alpha" else "red",
        fill_color=hex_to_rgba(fill_color, 1.0)[:3],
        fill_alpha=float(content.item.get("alpha", 1.0)),
        outline_color=hex_to_rgba(outline_color, 1.0)[:3],
        outline_alpha=float(content.item.get("outlineAlpha", 0.0) or 0.0),
        outer_fill_ratio=outer_fill_ratio,
        face_dilate=face_dilate,
        softness=max(0.0, renderer.shape_sdf_softness),
    )
    return True


def _native_content_result(
    renderer: PNGRenderer,
    content: Any,
    scene: _SceneAssembler,
) -> tuple[str, str] | None:
    if not content.object_data.get("visible", False):
        return "hidden", "hidden"

    native_emitters = (
        _emit_native_asset_image,
        _emit_native_omikuji_collection,
        _emit_native_shape,
        _emit_native_card_member,
    )
    if any(emitter(renderer, content, scene) for emitter in native_emitters):
        return "rendered-native", "native"

    if _emit_native_card_general(renderer, content, scene) == "native":
        return "rendered-native", "native"

    for result in (
        _emit_native_honor_deck(renderer, content, scene),
        _emit_native_general(renderer, content, scene),
    ):
        if result in {"native", "noop"}:
            return "rendered-native", result

    if _emit_native_honor(renderer, content, scene):
        return "rendered-native", "native"
    if _is_empty_text_noop(renderer, content):
        return "rendered-native", "noop"
    if _emit_native_simple_tmp_text(renderer, content, scene):
        return "rendered-native", "native"

    quads = _direct_text_quads(renderer, content)
    if quads is None:
        return None
    if not quads:
        return "rendered-direct", "noop"
    if scene.emit_sdf_quads(quads):
        return "rendered-direct", "native"
    return None


def _record_native_content_result(
    renderer: PNGRenderer,
    card_ref: dict[str, Any],
    content: Any,
    report: CustomProfileSceneReport,
    result: tuple[str, str] | None,
) -> None:
    audit_result, classification = result or ("unresolved", "unresolved")
    renderer.record_native_audit(card_ref, content, audit_result, None)
    report.observe(content, classification)


def _build_scene(
    renderer: PNGRenderer,
    card: dict[str, Any],
) -> tuple[bytes, dict[str, tuple], CustomProfileSceneReport]:
    canvas_size = (int(PROFILE_RENDER_VIEW_W), int(PROFILE_RENDER_VIEW_H))
    general_font_path_for = getattr(renderer, "general_font_path", None)
    general_font_path = general_font_path_for() if callable(general_font_path_for) else None
    builder = _new_builder(*canvas_size, general_font_path=general_font_path)
    # render_card starts from an OPAQUE WHITE base (Image.new(..., (255, 255, 255, 255))), not a
    # transparent canvas — the story background does not always cover the outermost pixels.
    builder.rect((0, 0), canvas_size, fill=(255, 255, 255, 255))
    scene = _SceneAssembler(builder, canvas_size, CUSTOM_PROFILE_MAX_SCENE_BYTES)
    card_ref = renderer.native_card_ref(card)
    contents = renderer.build_native_contents(card)
    report = CustomProfileSceneReport(elements_total=len(contents))

    # Walk the same z-ordered content list as render_card. Every successful element must lower to
    # asset/font-backed IR. Unsupported content is classified before a Pillow layer is created;
    # the strict coverage gate then declines the whole scene to the route-level Pillow fallback.
    for content in contents:
        _record_native_content_result(
            renderer,
            card_ref,
            content,
            report,
            _native_content_result(renderer, content, scene),
        )
    report.mem_images = len(scene.mem_images)
    report.mem_bytes = scene.mem_bytes

    ir_json = json.dumps(builder.build(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return ir_json, scene.mem_images, report


def _render_custom_profile_native_scene(
    native: Any,
    card: dict[str, Any],
    profile_context: dict[str, Any],
    resources: dict[str, Any],
    region: str,
):
    """Construct and rasterize one native scene inside the render-pool task."""

    from src.sekai.profile.custom_profile import drawer as _drawer
    from src.settings import (
        CUSTOM_PROFILE_ASSETS_DIR,
        CUSTOM_PROFILE_FONTS_DIR,
        CUSTOM_PROFILE_MAX_LAYER_PIXELS,
        CUSTOM_PROFILE_PARALLEL_WORKERS,
        CUSTOM_PROFILE_SHAPE_SPRITE_DIR,
        CUSTOM_PROFILE_TMP_FONT_METADATA,
        CUSTOM_PROFILE_UNITY_UI_SPRITE_DIR,
    )

    try:
        renderer = PNGRenderer(
            masterdata=None,
            assets=_drawer._require_region_path("custom_profile_assets_dir", CUSTOM_PROFILE_ASSETS_DIR, region),
            fonts=_drawer._require_region_path("custom_profile_fonts_dir", CUSTOM_PROFILE_FONTS_DIR, region),
            resources=resources,
            tmp_font_metadata=_drawer._optional_region_file(
                "custom_profile_tmp_font_metadata", CUSTOM_PROFILE_TMP_FONT_METADATA, region
            ),
            shape_sprite_dir=_drawer._require_region_path(
                "custom_profile_shape_sprite_dir", CUSTOM_PROFILE_SHAPE_SPRITE_DIR, region
            ),
            profile_context=profile_context,
            parallel_workers=max(1, int(CUSTOM_PROFILE_PARALLEL_WORKERS or 1)),
            parallel_stage="transform",
            clip_canvas_transform=True,
            canvas_w=int(PROFILE_RENDER_VIEW_W),
            canvas_h=int(PROFILE_RENDER_VIEW_H),
            origin_x=PROFILE_RENDER_VIEW_W / 2.0,
            origin_y=PROFILE_RENDER_VIEW_H / 2.0,
            unity_ui_sprite_dir=_drawer._require_region_path(
                "custom_profile_unity_ui_sprite_dir", CUSTOM_PROFILE_UNITY_UI_SPRITE_DIR, region
            ),
            region=region,
            max_layer_pixels=CUSTOM_PROFILE_MAX_LAYER_PIXELS,
            max_scene_bytes=CUSTOM_PROFILE_MAX_SCENE_BYTES,
        )
    except Exception as exc:
        raise _CustomProfileSkiaStageError("renderer_init") from exc
    try:
        ir_json, mem_images, report = _build_scene(renderer, card)
    except Exception as exc:
        raise _CustomProfileSkiaStageError("scene_build") from exc
    if not report.complete:
        return None, report
    try:
        return native.render_scene(ir_json, mem_images), report
    except Exception as exc:
        raise _CustomProfileSkiaStageError("native_render", report=report) from exc


async def try_render_custom_profile_card_attempt(
    request: CustomProfileCardRenderRequest,
) -> CustomProfileSkiaAttempt:
    """Build a deferred Skia attempt for the Custom Profile route."""
    if not skia_plot_enabled():
        return CustomProfileSkiaAttempt(None, OUTCOME_DISABLED)
    try:
        native = load_native_renderer()
    except ImportError as exc:
        # Also where any wheel older than REQUIRED_NATIVE_IR_CAPABILITY fails open.
        logger.error("haruki_skia_renderer not importable; falling back to Pillow error_type=ImportError")
        return CustomProfileSkiaAttempt(
            None,
            OUTCOME_FALLBACK,
            error_stage="renderer_load",
            error_type="ImportError",
            exception_diagnostic=capture_safe_exception(exc),
        )

    started = time.perf_counter()
    try:
        result, report = await run_in_pool(
            _render_custom_profile_native_scene,
            native,
            dict(request.card),
            dict(request.profile_context),
            dict(request.resources),
            request.region,
        )
    except _CustomProfileSkiaStageError as exc:
        # FAIL-OPEN (honor doctrine): anything escaping here would skip _record and 500 instead
        # of letting Pillow render and raise the canonical error (e.g. the ValueError -> 400).
        cause = exc.__cause__ or exc
        return CustomProfileSkiaAttempt(
            None,
            OUTCOME_ERROR,
            report=exc.report,
            error_stage=exc.stage,
            error_type=type(cause).__name__,
            exception_diagnostic=capture_safe_exception(exc),
        )
    except Exception as exc:
        return CustomProfileSkiaAttempt(
            None,
            OUTCOME_ERROR,
            error_stage="pool_dispatch",
            error_type=type(exc).__name__,
            exception_diagnostic=capture_safe_exception(exc),
        )
    scene_metrics = report.metrics()
    if result is None:
        from src.core.debug import current_request_context

        context = current_request_context()
        logger.warning(
            "custom_profile.scene id=%s complete=false visible=%d native=%d hybrid=%d "
            "missing=%d unresolved=%d mem_images=%d mem_bytes=%d issues_by_kind=%s",
            context["request_id"],
            report.visible_elements,
            report.native_elements,
            report.hybrid_elements,
            report.missing_elements,
            report.unresolved_elements,
            report.mem_images,
            report.mem_bytes,
            scene_metrics["issues_by_kind"],
        )
        return CustomProfileSkiaAttempt(None, OUTCOME_FALLBACK, report=report)
    try:
        payload = payload_from_native(result)
    except Exception as exc:
        return CustomProfileSkiaAttempt(
            None,
            OUTCOME_ERROR,
            report=report,
            error_stage="payload_decode",
            error_type=type(exc).__name__,
            exception_diagnostic=capture_safe_exception(exc),
        )
    payload.native_metrics = {**(payload.native_metrics or {}), **report.native_metrics()}
    logger.info(
        "custom_profile_card backend=skia total=%.3fs bytes=%d image=%sx%s",
        time.perf_counter() - started,
        len(payload.image_bytes),
        payload.image_width,
        payload.image_height,
    )
    return CustomProfileSkiaAttempt(payload, OUTCOME_SKIA, report=report)


async def try_render_custom_profile_card_payload(
    request: CustomProfileCardRenderRequest,
) -> EncodedImagePayload | None:
    """Skia path for direct/parity callers; ``None`` means "Pillow, please"."""

    attempt = await try_render_custom_profile_card_attempt(request)
    attempt.record()
    return attempt.payload
