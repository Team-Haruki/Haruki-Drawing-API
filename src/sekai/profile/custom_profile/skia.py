"""Progressive native Render-IR path for ``/profile/custom-profile-card``.

The Unity card JSON remains the layout carrier. Asset-backed ``UnityImage`` and ``SdfShape``
elements lower directly to Rust/Skia without a Pillow decode or NumPy raster; decorative text
uses native ``SdfQuad`` shading, and normal/birthday/bonds/empty honors reuse the shared
``HonorBadgeBox`` asset-backed subtree inside an isolated native subscene. Shared General/Card
display lists replay native ``SlicedImage``/sprite/Text/viewport/card operations with strict Rust
font metrics and Pillow-compatible Lanczos stages. Plain dynamic-font TMP text also emits native IR Text;
static-atlas rich/decorative glyphs use asset-backed ``SdfAtlasQuad`` nodes. Still-unmigrated
dynamic/fallback rich TMP and incomplete HonorDeck content is explicitly classified as hybrid
and transported as bounded ``mem:`` rasters. A scene coverage
report rejects any visible missing/unresolved element before Rust runs, so ``backend=skia``
cannot mean "successfully encoded a partial card".

Honor dimensions come from the native asset-info API. An older wheel falls back to an explicitly
telemetried Pillow header probe, so compatibility cannot masquerade as native-pure.

Parity-critical mirrors of the Pillow path:
- Unrotated, unscaled layers are pasted at ROUNDED integer positions (the ``angle ~ 0`` branch of
  ``prepare_canvas_clipped_transformed_layer``); the scene emits those as plain integer-placed
  images with no Transform, so they stay pixel-crisp instead of drifting subpixel.
- Hybrid minification (combined scale < ~0.98) keeps the Python two-step BICUBIC pre-resize;
  ``UnityImage`` performs the same two sequential dimension rounds natively.
- Decorative direct-raster TMP texts draw onto full-canvas PIL layers exactly as in
  ``render_card``; consecutive runs accumulate on one layer and flush in z-order.

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
import time
from typing import Any

from PIL import Image

from src.core.image_payload import EncodedImagePayload
from src.core.pillow_telemetry import (
    PILLOW_TOUCH_CUSTOM_PROFILE_MEM_RASTER,
    PILLOW_TOUCH_IMAGE_HEADER_PROBE,
    record_pillow_touch,
)
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
from src.sekai.profile.custom_profile.general_prefab import (
    GeneralAssetImageOp,
    GeneralFontRef,
    GeneralRoundedRectOp,
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
    DirectSdfAtlasQuad,
    GENERAL_DECK_CARD_RENDER_SIZE,
    GENERAL_MUSIC_DIFFICULTIES,
    GENERAL_NATIVE_SIZES,
    GENERAL_PREFAB_PALETTE,
    PROFILE_RENDER_VIEW_H,
    PROFILE_RENDER_VIEW_W,
    SHAPE_NATIVE_OUTLINE_FILL_RATIO_FACTOR,
    STATIC_IMAGE_CONTENT_KINDS,
    LayerTransformInputs,
    PNGRenderer,
    bool_from_profile,
    content_data_id,
    harden_rgba_alpha,
    hex_to_rgba,
    unity_tint_rgba,
)
from src.sekai.profile.custom_profile.svg import unity_rotation_degrees
from src.sekai.profile.custom_profile.tmp_text_prefab import build_simple_tmp_text_display_list
from src.sekai.profile.model import CustomProfileCardRenderRequest
from src.sekai.skia_renderer.canvas import load_native_renderer, payload_from_native, skia_plot_enabled
from src.sekai.skia_renderer.ir_builder import IRBuilder, image_tint
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

# Mirror of prepare_transformed_layer's branch thresholds (renderer.py): the exact-integer paste
# branch triggers at angle % 360 ~ 0; the minification carve-out keeps PIL resize semantics.
_ANGLE_EPS = 1.0e-9
_MIN_SCALE_FOLD = 0.98
_REQUIRED_NATIVE_ASSET_INFO_CAPABILITY = 1
_REQUIRED_NATIVE_TEXT_METRICS_CAPABILITY = 1
_NATIVE_GENERAL_PREFABS = frozenset(
    {
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
_NATIVE_CARD_GENERAL_PREFABS = frozenset({"LeaderCard", "Deck"})


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
    issues: list[dict[str, int | str]] = field(default_factory=list)

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
            and self.missing_elements == 0
            and self.unresolved_elements == 0
        )

    def observe(self, content: Any, classification: str) -> None:
        if classification == "hidden":
            self.hidden_elements += 1
            return
        self.visible_elements += 1
        counter = {
            "native": "native_elements",
            "hybrid": "hybrid_elements",
            "noop": "noop_elements",
            "missing": "missing_elements",
            "unresolved": "unresolved_elements",
        }.get(classification, "unresolved_elements")
        setattr(self, counter, getattr(self, counter) + 1)
        if classification in {"missing", "unresolved"} and len(self.issues) < 16:
            self.issues.append(
                {
                    "kind": str(content.kind),
                    "status": classification,
                    "data_id": content_data_id(content.kind, content.item),
                    "layer": int(content.layer),
                }
            )

    def metrics(self) -> dict[str, Any]:
        issues_by_kind: dict[str, dict[str, int]] = {}
        for issue in self.issues:
            kind = str(issue["kind"])
            status = str(issue["status"])
            bucket = issues_by_kind.setdefault(kind, {"missing": 0, "unresolved": 0})
            bucket[status] += 1
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


def _record(outcome: str, payload: EncodedImagePayload | None = None) -> None:
    """Record one render attempt for /render-stats and tag the request context.

    Mirrors ``skia_renderer.canvas._record`` / ``honor.skia._record``: this path cannot reuse the
    canvas helper, so it records through the same public primitives instead.
    """
    from src.core.debug import set_render_backend

    record_render(CUSTOM_PROFILE_ENDPOINT, outcome)
    backend = backend_for_outcome(outcome)
    set_render_backend(backend)
    if payload is not None:
        payload.backend = backend
        record_native_metrics(payload.native_metrics)


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
    """Accumulates the z-ordered element scene: mem rasters + Transform placements."""

    def __init__(self, builder: IRBuilder, canvas_size: tuple[int, int], max_mem_bytes: int) -> None:
        self.builder = builder
        self.canvas_size = canvas_size
        self.max_mem_bytes = max(1, int(max_mem_bytes))
        self.mem_bytes = 0
        # RGBA raw 3-tuples plus A8 raw-buffer 6-tuples (capability 9) share the registry.
        self.mem_images: dict[str, tuple] = {}
        self._direct_layer: Image.Image | None = None

    def _reserve_mem(self, byte_count: int) -> None:
        total = self.mem_bytes + max(0, int(byte_count))
        if total > self.max_mem_bytes:
            raise ValueError(
                f"custom profile native scene would retain {total} raw bytes; limit is {self.max_mem_bytes}"
            )
        self.mem_bytes = total

    def _mem_ref(self, image: Image.Image) -> str:
        record_pillow_touch(PILLOW_TOUCH_CUSTOM_PROFILE_MEM_RASTER)
        rgba = image if image.mode == "RGBA" else image.convert("RGBA")
        self._reserve_mem(rgba.width * rgba.height * 4)
        key = f"m{len(self.mem_images)}"
        self.mem_images[key] = (rgba.width, rgba.height, rgba.tobytes())
        return f"mem:{key}"

    def direct_layer(self) -> Image.Image:
        """The accumulating full-canvas layer for decorative direct-raster texts."""
        if self._direct_layer is None:
            self._direct_layer = Image.new("RGBA", self.canvas_size, (0, 0, 0, 0))
        return self._direct_layer

    def flush_direct_layer(self) -> None:
        """Emit the accumulated direct-raster layer as one identity-placed image (keeps z-order:
        called before any transformed element is emitted on top of it)."""
        if self._direct_layer is None:
            return
        ref = self._mem_ref(self._direct_layer)
        self.builder.image(ref, (0, 0), self.canvas_size, sampling="linear")
        self._direct_layer = None

    def emit_sdf_quads(self, quads) -> bool:
        """Emit one decorative text element and return whether every glyph is asset-native."""
        self.flush_direct_layer()
        fully_native = True
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
            fully_native = False
            record_pillow_touch(PILLOW_TOUCH_CUSTOM_PROFILE_MEM_RASTER)
            field = quad.field
            self._reserve_mem(field.width * field.height)
            key = f"m{len(self.mem_images)}"
            self.mem_images[key] = (field.width, field.height, field.width, "a8", "unpremul", field.tobytes())
            self.builder.sdf_quad(
                (quad.left, quad.top),
                f"mem:{key}",
                scalars.face_color,
                scalars.face_scale,
                scalars.face_w,
                scalars.alpha,
                underlay,
            )
        return fully_native

    def emit_layer(self, layer: Image.Image, inputs: LayerTransformInputs, renderer: PNGRenderer) -> None:
        """Place one element layer.

        Unrotated elements (the overwhelming majority — note position_scale is ~1.118 in the
        service target, so almost every element carries scale) reproduce the Pillow sequence
        exactly: the two-step BICUBIC pre-resize in Python, then a rounded integer-position
        paste — pixel-parity by construction. Only ROTATED elements go through a Transform
        matrix (Pillow resamples those anyway; the single native pass replaces its resize +
        rotate + 2x supersample, under the relaxed rotated-content parity budget), with the
        minification carve-out keeping PIL's kernel-scaling resize semantics.
        """
        self.flush_direct_layer()
        pivot = inputs.pivot
        sx = inputs.object_scale[0] * inputs.position_scale[0]
        sy = inputs.object_scale[1] * inputs.position_scale[1]
        angle = inputs.angle % 360.0
        rotated = abs(angle) >= _ANGLE_EPS

        if not rotated or min(sx, sy) < _MIN_SCALE_FOLD:
            # Two SEPARATE sequential resizes, exactly like prepare_transformed_layer (combining
            # them changes pixels; the Pillow path is the parity baseline).
            osx, osy = inputs.object_scale
            if osx != 1.0 or osy != 1.0:
                new_w = max(1, round(layer.width * osx))
                new_h = max(1, round(layer.height * osy))
                layer = renderer.resize_layer_for_transform(layer, (new_w, new_h), Image.Resampling.BICUBIC)
                pivot = (pivot[0] * osx, pivot[1] * osy)
            psx, psy = inputs.position_scale
            if abs(psx - 1.0) >= 1.0e-6 or abs(psy - 1.0) >= 1.0e-6:
                new_w = max(1, round(layer.width * psx))
                new_h = max(1, round(layer.height * psy))
                layer = renderer.resize_layer_for_transform(layer, (new_w, new_h), Image.Resampling.BICUBIC)
                pivot = (pivot[0] * psx, pivot[1] * psy)
            sx = sy = 1.0

        ax, ay = inputs.anchor
        if not rotated:
            # Pillow's angle~0 branch pastes at rounded integer positions; mirror it so
            # unrotated content stays crisp (a float Transform would resample subpixel).
            ref = self._mem_ref(layer)
            self.builder.image(ref, (round(ax - pivot[0]), round(ay - pivot[1])), layer.size, sampling="linear")
            return

        theta = math.radians(angle)
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)
        px, py = pivot
        matrix = (
            cos_t * sx,
            -sin_t * sy,
            ax - cos_t * sx * px + sin_t * sy * py,
            sin_t * sx,
            cos_t * sy,
            ay - sin_t * sx * px - cos_t * sy * py,
        )
        ref = self._mem_ref(layer)
        with self.builder.transform(matrix):
            self.builder.image(ref, (0, 0), layer.size, sampling="catmull_rom")


def _direct_text_quads(renderer: PNGRenderer, content: Any):
    """SdfQuad records for a decorative direct-raster text element, or None.

    Mirrors render_content_direct_on_card's outer gates, then asks the shared seam
    (prepare_direct_sdf_quads — the layout + PIL-warp half of the Pillow direct path) for the
    per-glyph fields/scalars. None falls through to the Pillow-parity raster branches.
    """
    if content.kind != "text" or not content.object_data.get("visible", False):
        return None
    if not renderer.tmp_decorative_direct_raster:
        return None
    if renderer.text_layout != "tmp" or renderer.tmp_text_render_mode != "sdf":
        return None
    if not renderer.is_decorative_text_item(content.item):
        return None
    return renderer.prepare_direct_sdf_quads(content.item, content.object_data, defer_static_atlas=True)


def _is_direct_text_candidate(renderer: PNGRenderer, content: Any) -> bool:
    """Check the direct-text gates without allocating the full-canvas scratch layer."""

    return (
        content.kind == "text"
        and bool(content.object_data.get("visible", False))
        and renderer.tmp_decorative_direct_raster
        and renderer.text_layout == "tmp"
        and renderer.tmp_text_render_mode == "sdf"
        and renderer.is_decorative_text_item(content.item)
    )


def _emit_native_simple_tmp_text(renderer: PNGRenderer, content: Any, scene: _SceneAssembler) -> bool:
    """Emit the strictly plain TMP subset as source-font Skia Text nodes.

    Python remains the TMP layout oracle; no Pillow/NumPy glyph field, local RGBA surface,
    resize, or ``mem:`` transport is created. Rich/decorative/effected text returns ``False`` and
    continues through the existing compatibility path.
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

    # Register and flush only after the display-list builder has completed every eligibility and
    # layout check. An unsupported text must leave the scene untouched for the hybrid fallback.
    scene.builder.register_extra_font(font_name, font_path)
    scene.flush_direct_layer()
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
    a genuinely absent API takes the explicit Pillow-header compatibility path.
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
        label=f"custom profile native honor asset {asset_path}",
    )
    if int(info.get("mtime_ns", 0)) < 0 or int(info.get("file_size", 0)) < 0:
        raise ValueError(f"native honor asset info returned an invalid file identity: {asset_path}")
    return info


def _header_only_asset_ref(path, asset_path: str) -> AssetImageRef:
    """Build the image source the shared honor tree needs without decoding its pixels.

    Current wheels obtain dimensions from Rust. An older wheel explicitly falls back to a
    Pillow header probe and records that touch, so it cannot be mislabeled as native-pure.
    """

    resolved = path.resolve(strict=True)
    if not resolved.is_file():
        raise ValueError(f"custom profile honor asset is not a regular file: {resolved}")
    native_info = _native_asset_info(asset_path)
    if native_info is not None:
        return AssetImageRef(
            path=resolved,
            size=(int(native_info["width"]), int(native_info["height"])),
            mode=str(native_info.get("mode") or "RGBA"),
            mtime_ns=int(native_info.get("mtime_ns", 0)),
            file_size=int(native_info.get("file_size", 0)),
        )

    stat = resolved.stat()
    record_pillow_touch(PILLOW_TOUCH_IMAGE_HEADER_PROBE)
    with Image.open(resolved) as probe:
        ensure_raster_size(
            probe.size,
            max_pixels=CUSTOM_PROFILE_MAX_LAYER_PIXELS,
            label=f"custom profile honor asset {resolved.name}",
        )
        size = (int(probe.width), int(probe.height))
        mode = str(probe.mode)
    return AssetImageRef(
        path=resolved,
        size=size,
        mode=mode,
        mtime_ns=stat.st_mtime_ns,
        file_size=stat.st_size,
    )


class _NativeGeneralTextMetrics:
    """Strict Skia font metrics used by the renderer-neutral General prefab builder."""

    def __init__(self, measure, font_path: Path) -> None:
        self._measure_batch = measure
        self.font_path = font_path
        self._cache: dict[tuple[str, int], dict[str, Any]] = {}

    @classmethod
    def create(cls, font_path: Path | None) -> _NativeGeneralTextMetrics | None:
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
        return cls(measure, resolved)

    def measure(self, text: str, font: GeneralFontRef, size: int) -> dict[str, Any]:
        if font.name != "FOT-RodinNTLGPro-DB":
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


def _prepare_native_card_display_list(
    display_list: CardDisplayList,
    metrics: _NativeGeneralTextMetrics | None,
) -> _PreparedCardDisplayList | None:
    """Resolve every card dependency without opening or decoding an image in Pillow."""

    asset_paths: dict[int, str] = {}
    text_placements: dict[int, tuple[str, float]] = {}
    for op in display_list.ops:
        op_key = id(op)
        if isinstance(op, CardAlphaMaskOp):
            # No active card path uses the legacy mask hook. Its rounded fallback and
            # alpha-multiply contract need a dedicated shared native primitive before this
            # opt-in API can be truthfully classified native.
            return None
        if isinstance(op, CardCoverArtOp):
            status, asset_path = _existing_native_asset(op.path)
            if status != "ready" or asset_path is None:
                return None
            if op.cover_align != (0.5, 0.5):
                # Render IR Cover currently centers its source crop.
                return None
            asset_paths[op_key] = asset_path
            continue
        if isinstance(op, CardSpriteOp):
            status, asset_path = _existing_native_asset(op.resource.path)
            if status == "outside":
                return None
            if status != "ready":
                fallback_status, asset_path = _existing_native_asset(op.resource.fallback_path)
                if fallback_status == "outside":
                    return None
                status = fallback_status
            if status == "ready" and asset_path is not None:
                asset_paths[op_key] = asset_path
            elif op.resource.resource_policy == "required":
                return None
            continue
        if isinstance(op, CardTextOp):
            if metrics is None:
                return None
            if op.font.name != "general" or not op.font.bold:
                return None
            text_placements[op_key] = metrics.anchor_placement(
                text=op.text,
                pos=op.pos,
                size=op.size,
                anchor=op.anchor,
            )
            continue
        if not isinstance(op, CardRectOp):
            return None
        if op.radius > 0.0 and op.blend == "src":
            translucent = (op.fill is not None and op.fill[3] < 255) or (op.outline is not None and op.outline[3] < 255)
            if translucent:
                # RoundRect has no Porter-Duff Src switch yet.
                return None
    return _PreparedCardDisplayList(display_list, asset_paths, text_placements)


def _emit_prepared_card_ops(scene: _SceneAssembler, prepared: _PreparedCardDisplayList) -> None:
    """Replay one natural-size card display list into the current isolated surface."""

    display_list = prepared.display_list
    sampling_map = {
        "nearest": "nearest",
        "bilinear": "linear",
        "bicubic": "catmull_rom",
        "lanczos": "pillow_lanczos",
    }
    for op in display_list.ops:
        op_key = id(op)
        if isinstance(op, CardCoverArtOp):
            cover_w = max(1, round(op.cover_size[0]))
            cover_h = max(1, round(op.cover_size[1]))
            crop_left = max(0, round((cover_w - display_list.size[0]) * op.crop_align[0]))
            crop_top = max(0, round((cover_h - display_list.size[1]) * op.crop_align[1]))
            scene.builder.image(
                prepared.asset_paths[op_key],
                (-crop_left, -crop_top),
                (cover_w, cover_h),
                fit="cover",
                sampling=sampling_map[op.sampling],
                blend=op.blend,
            )
            continue
        if isinstance(op, CardRectOp):
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
            else:
                scene.builder.rect(
                    (left, top),
                    size,
                    fill=op.fill,
                    stroke=op.outline,
                    stroke_width=op.width,
                    blend=op.blend,
                )
            continue
        if isinstance(op, CardTextOp):
            align, baseline = prepared.text_placements[op_key]
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
            continue
        if not isinstance(op, CardSpriteOp):
            raise TypeError(f"unsupported native card display-list op: {type(op).__name__}")
        asset_path = prepared.asset_paths.get(op_key)
        if asset_path is None:
            continue
        left, top, right, bottom = op.rect
        scene.builder.image(
            asset_path,
            (round(left), round(top)),
            (max(1, round(right - left)), max(1, round(bottom - top))),
            sampling=sampling_map[op.sampling],
        )


def _emit_native_card_general(renderer: PNGRenderer, content: Any, scene: _SceneAssembler) -> str | None:
    """Compose LeaderCard/Deck from their shared CardDisplayList without Pillow pixels."""

    if content.kind != "general" or not content.object_data.get("visible", False):
        return None
    resource_for = getattr(renderer, "image_resource_for", None)
    font_path_for = getattr(renderer, "general_font_path", None)
    if not callable(resource_for) or not callable(font_path_for):
        return None
    resource = resource_for("general", content.item)
    file_name = str(resource.get("fileName", "") or "")
    if file_name not in _NATIVE_CARD_GENERAL_PREFABS:
        return None

    metrics = _NativeGeneralTextMetrics.create(font_path_for())
    prepared_cards: list[tuple[_PreparedCardDisplayList, tuple[int, int]]] = []
    if file_name == "LeaderCard":
        deck = renderer.profile_context.get("userDeck") or {}
        card_id = int(deck.get("leader", 0) or 0) if isinstance(deck, dict) else 0
        if card_id <= 0:
            return None
        display_list = renderer.build_profile_leader_card_display_list(card_id)
        if display_list is None:
            return None
        prepared = _prepare_native_card_display_list(display_list, metrics)
        if prepared is None:
            return None
        prepared_cards.append((prepared, (0, 0)))
    else:
        deck = renderer.profile_context.get("userDeck") or {}
        if not isinstance(deck, dict):
            return None
        card_ids = [int(deck.get(f"member{i}", 0) or 0) for i in range(1, 6)]
        card_lists: list[CardDisplayList] = []
        for index, card_id in enumerate(card_ids):
            display_list = renderer.build_profile_deck_card_display_list(card_id, leader=index == 0)
            if display_list is None:
                display_list = renderer.build_empty_profile_deck_card_display_list(GENERAL_DECK_CARD_RENDER_SIZE)
            card_lists.append(display_list)
        card_w, card_h = card_lists[0].render_size or card_lists[0].size
        gap = max(0.0, (GENERAL_NATIVE_SIZES["Deck"][0] - card_w * 5) / 4.0)
        total_w = card_w * 5 + gap * 4
        start_x = max(0.0, (GENERAL_NATIVE_SIZES["Deck"][0] - total_w) / 2.0)
        y = GENERAL_NATIVE_SIZES["Deck"][1] - card_h
        for index, display_list in enumerate(card_lists):
            prepared = _prepare_native_card_display_list(display_list, metrics)
            if prepared is None:
                return None
            prepared_cards.append((prepared, (round(start_x + index * (card_w + gap)), round(y))))

    scale = content.object_data.get("scale") or {}
    sx = float(scale.get("x") or 1.0)
    sy = float(scale.get("y") or sx or 1.0)
    if not all(math.isfinite(value) and value > 0.0 for value in (sx, sy)):
        return None
    angle = renderer.rotation_sign * unity_rotation_degrees(content.object_data.get("rotation", {}))
    outer_size = GENERAL_NATIVE_SIZES[file_name]
    scene.flush_direct_layer()
    with scene.builder.unity_subscene(
        size=outer_size,
        anchor=renderer.unity_point(content.object_data.get("position", {})),
        object_scale=(sx, sy),
        post_scale=(renderer.position_scale_x, renderer.position_scale_y),
        rotation=angle,
    ):
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
    scene.flush_direct_layer()
    with scene.builder.unity_subscene(
        size=display_list.size,
        anchor=renderer.unity_point(content.object_data.get("position", {})),
        object_scale=(sx, sy),
        post_scale=(renderer.position_scale_x, renderer.position_scale_y),
        rotation=angle,
    ):
        _emit_prepared_card_ops(scene, prepared)
    return True


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

    display_list = build_general_prefab_display_list(
        file_name,
        size=GENERAL_NATIVE_SIZES[file_name],
        profile_context=renderer.profile_context,
        labels={
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
        },
        metrics=metrics,
        palette=GENERAL_PREFAB_PALETTE,
        asset_paths=asset_paths,
        music_difficulties=GENERAL_MUSIC_DIFFICULTIES,
        story_favorite_resources=renderer.story_favorite_resources,
    )
    if display_list is None:
        return "noop"

    # Resolve every dependency before mutating the scene. Required resources decline the whole
    # element; optional resources are omitted; fallback resources replay their rounded rectangle.
    # A supplied path outside the asset root is never downgraded to "optional missing".
    resource_paths: dict[int, str | None] = {}
    text_placements: dict[int, tuple[str, float]] = {}

    def walk_ops(ops):
        for op in ops:
            yield op
            if isinstance(op, GeneralViewportOp):
                yield from walk_ops(op.children)

    for op in walk_ops(display_list.ops):
        op_key = id(op)
        if isinstance(op, GeneralSpriteOp):
            path = renderer.unity_ui_sprite_path(op.name)
            if path is not None:
                asset_path = _relative_asset_path(path)
                if asset_path is None:
                    return None
                resource_paths[op_key] = asset_path
                continue
            if op.resource_policy == "required":
                return None
            resource_paths[op_key] = None
        elif isinstance(op, GeneralAssetImageOp):
            if op.fit == "cover" and op.align != (0.5, 0.5):
                # IR Image cover is deliberately centered. A future non-centered display-list
                # operation must decline instead of silently changing its crop.
                return None
            if op.clip_radius is not None:
                # Pillow builds a discrete L mask with ImageDraw.rounded_rectangle and multiplies
                # it into the resized alpha. Skia's anti-aliased rrect clip differs at the edge;
                # keep banner-backed StoryFavorite hybrid until an exact mask primitive exists.
                return None
            asset_status, asset_path = _existing_native_asset(op.path)
            if asset_status == "ready":
                resource_paths[op_key] = asset_path
                continue
            if asset_status == "outside":
                return None
            if op.resource_policy == "required":
                return None
            resource_paths[op_key] = None
        elif isinstance(op, GeneralTextOp):
            text_placements[op_key] = metrics.text_placement(op)

    scale = content.object_data.get("scale") or {}
    sx = float(scale.get("x") or 1.0)
    sy = float(scale.get("y") or sx or 1.0)
    if not all(math.isfinite(value) and value > 0.0 for value in (sx, sy)):
        return None
    angle = renderer.rotation_sign * unity_rotation_degrees(content.object_data.get("rotation", {}))

    def emit_rounded_rect(op: GeneralRoundedRectOp) -> None:
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

    sampling_map = {
        "nearest": "nearest",
        "bilinear": "linear",
        "bicubic": "catmull_rom",
        "lanczos": "catmull_rom",
    }

    def emit_ops(ops) -> None:
        for op in ops:
            op_key = id(op)
            if isinstance(op, GeneralSpriteOp):
                asset_path = resource_paths[op_key]
                if asset_path is None:
                    if op.fallback is not None:
                        emit_rounded_rect(op.fallback)
                    continue
                left, top, right, bottom = op.rect
                pos = (round(left), round(top))
                size = (max(1, round(right - left)), max(1, round(bottom - top)))
                tint = image_tint(unity_tint_rgba(op.tint), "recolor") if op.tint is not None else None
                if op.sliced_border is not None:
                    scene.builder.sliced_image(
                        path=asset_path,
                        pos=pos,
                        size=size,
                        border=op.sliced_border,
                        tint=tint,
                    )
                else:
                    scene.builder.image(
                        asset_path,
                        pos,
                        size,
                        sampling=sampling_map[op.sampling],
                        tint=tint,
                    )
                continue
            if isinstance(op, GeneralRoundedRectOp):
                emit_rounded_rect(op)
                continue
            if isinstance(op, GeneralAssetImageOp):
                asset_path = resource_paths[op_key]
                if asset_path is None:
                    if op.fallback is not None:
                        emit_rounded_rect(op.fallback)
                    continue
                left, top, right, bottom = op.rect
                pos = (round(left), round(top))
                size = (max(1, round(right - left)), max(1, round(bottom - top)))
                sampling = "pillow_lanczos" if op.sampling == "lanczos" else sampling_map[op.sampling]
                scene.builder.image(
                    asset_path,
                    pos,
                    size,
                    fit=op.fit,
                    sampling=sampling,
                )
                continue
            if isinstance(op, GeneralViewportOp):
                with scene.builder.group(
                    offset=op.offset,
                    size=op.viewport_size,
                    clip={"kind": "rect"},
                ):
                    # Dependency preflight above deliberately walked every child, including
                    # rows wholly outside this viewport. Replay does the same under a hard clip.
                    emit_ops(op.children)
                continue
            if not isinstance(op, GeneralTextOp):
                raise TypeError(f"unsupported GeneralContentView display-list op: {type(op).__name__}")
            align, baseline = text_placements[op_key]
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

    scene.flush_direct_layer()
    with scene.builder.unity_subscene(
        size=display_list.size,
        anchor=renderer.unity_point(content.object_data.get("position", {})),
        object_scale=(sx, sy),
        post_scale=(renderer.position_scale_x, renderer.position_scale_y),
        rotation=angle,
    ):
        emit_ops(display_list.ops)
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

    resolution = resolve_honor_assets(
        request,
        path_resolver=renderer.resolve_request_asset_path,
        source_factory=source_factory,
    )
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
        if isinstance(candidate, HonorRequest):
            request = candidate
        elif isinstance(candidate, dict):
            request = HonorRequest.model_validate(candidate)
        else:
            return False

        source_status, images = _native_honor_sources(renderer, request)
        if source_status == "unrenderable":
            continue
        if source_status != "ready" or images is None:
            return False

        canvas = build_honor_badge_canvas(request, images)
        if canvas is None:
            return False
        try:
            badge = lower_canvas_subtree(canvas, require_asset_backed=True, export_format="png")
        except NativeSubtreeError:
            return False

        scale = content.object_data.get("scale") or {}
        sx = float(scale.get("x", 1.0))
        sy = float(scale.get("y", sx))
        if not all(math.isfinite(value) and value > 0.0 for value in (sx, sy)):
            return False
        angle = renderer.rotation_sign * unity_rotation_degrees(content.object_data.get("rotation", {}))
        scene.flush_direct_layer()
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
            request = HonorRequest.model_validate(payload)
            source_status, images = _native_honor_sources(renderer, request)
            if source_status == "unrenderable":
                continue
            if source_status != "ready" or images is None:
                return "hybrid", None
            canvas = build_honor_badge_canvas(request, images)
            if canvas is None:
                continue
            try:
                badge = lower_canvas_subtree(canvas, require_asset_backed=True, export_format="png")
            except NativeSubtreeError:
                return "hybrid", None
            return "ready", badge
    return "missing", None


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

    slots: list[tuple[NativeSubtree, tuple[int, int, int, int], int]] = []
    for slot in plan.slots:
        status, badge = _native_profile_honor_badge(
            renderer,
            dict(slot.profile_row),
            full_size=slot.full_size,
        )
        if status != "ready":
            return None
        assert badge is not None
        # Legacy paste_in_rect uses Pillow LANCZOS when a supplied badge has the wrong natural
        # size. Native custom-profile does not claim that filter yet; decline rather than
        # silently substituting Catmull-Rom through a nested subscene.
        if badge.size != slot.target_size:
            return None
        slots.append(
            (
                badge,
                (
                    *slot.target_xy,
                    *slot.target_size,
                ),
                slot.index,
            )
        )

    assert plan.panel is not None
    background_path = renderer.unity_ui_sprite_path(plan.panel.sprite_name)
    background_asset = _relative_asset_path(background_path) if background_path is not None else None
    if background_path is not None and background_asset is None:
        return None
    scale = content.object_data.get("scale") or {}
    sx = float(scale.get("x") or 1.0)
    sy = float(scale.get("y") or sx or 1.0)
    if not all(math.isfinite(value) and value > 0.0 for value in (sx, sy)):
        return None
    angle = renderer.rotation_sign * unity_rotation_degrees(content.object_data.get("rotation", {}))
    size = plan.natural_size
    scene.flush_direct_layer()
    with scene.builder.unity_subscene(
        size=size,
        anchor=renderer.unity_point(content.object_data.get("position", {})),
        object_scale=(sx, sy),
        post_scale=(renderer.position_scale_x, renderer.position_scale_y),
        rotation=angle,
    ):
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
    return "native"


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
    scene.flush_direct_layer()
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
    scene.flush_direct_layer()
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

    # Same walk as render_card's direct-raster loop: decorative TMP texts become native SdfQuads
    # (Phase 2 — Python keeps layout + the PIL field warp, the node shades per pixel); if the
    # element is not quad-eligible the accumulating full-canvas direct layer takes it, and
    # everything else renders to a local layer placed by the shared layer_transform_inputs
    # numbers. Audit records mirror the Pillow statuses.
    for content in contents:
        if _emit_native_asset_image(renderer, content, scene):
            renderer.record_native_audit(card_ref, content, "rendered-native", None)
            report.observe(content, "native")
            continue
        if _emit_native_shape(renderer, content, scene):
            renderer.record_native_audit(card_ref, content, "rendered-native", None)
            report.observe(content, "native")
            continue
        if _emit_native_card_member(renderer, content, scene):
            renderer.record_native_audit(card_ref, content, "rendered-native", None)
            report.observe(content, "native")
            continue
        card_general_result = _emit_native_card_general(renderer, content, scene)
        if card_general_result == "native":
            renderer.record_native_audit(card_ref, content, "rendered-native", None)
            report.observe(content, "native")
            continue
        honor_deck_result = _emit_native_honor_deck(renderer, content, scene)
        if honor_deck_result == "native":
            renderer.record_native_audit(card_ref, content, "rendered-native", None)
            report.observe(content, "native")
            continue
        if honor_deck_result == "noop":
            renderer.record_native_audit(card_ref, content, "rendered-native", None)
            report.observe(content, "noop")
            continue
        general_result = _emit_native_general(renderer, content, scene)
        if general_result == "native":
            renderer.record_native_audit(card_ref, content, "rendered-native", None)
            report.observe(content, "native")
            continue
        if general_result == "noop":
            renderer.record_native_audit(card_ref, content, "rendered-native", None)
            report.observe(content, "noop")
            continue
        if _emit_native_honor(renderer, content, scene):
            renderer.record_native_audit(card_ref, content, "rendered-native", None)
            report.observe(content, "native")
            continue
        if _emit_native_simple_tmp_text(renderer, content, scene):
            renderer.record_native_audit(card_ref, content, "rendered-native", None)
            report.observe(content, "native")
            continue
        quads = _direct_text_quads(renderer, content)
        if quads is not None:
            renderer.record_native_audit(card_ref, content, "rendered-direct", None)
            if quads:
                report.observe(content, "native" if scene.emit_sdf_quads(quads) else "hybrid")
            else:
                report.observe(content, "noop")
            continue
        if _is_direct_text_candidate(renderer, content) and renderer.render_content_direct_on_card(
            scene.direct_layer(), content
        ):
            renderer.record_native_audit(card_ref, content, "rendered-direct", None)
            report.observe(content, "hybrid")
            continue
        rendered = renderer.render_content_for_card(content)
        renderer.record_native_audit(card_ref, content, rendered.status, rendered.result)
        if not isinstance(rendered.result, tuple):
            report.observe(
                content,
                rendered.status if rendered.status in {"hidden", "missing", "unresolved"} else "unresolved",
            )
            continue
        report.observe(content, "hybrid")
        inputs = renderer.layer_transform_inputs(rendered.result, content.object_data, content.kind)
        layer = inputs.layer
        if (
            content.kind == "text"
            and renderer.tmp_decorative_alpha_harden > 1.0
            and renderer.is_decorative_text_item(content.item)
        ):
            # prepare_content_layer hardens AFTER the affine; hardening the local layer before it
            # is the closest scene equivalent (non-default configs only; production is 1.0).
            layer = harden_rgba_alpha(layer, renderer.tmp_decorative_alpha_harden)
        scene.emit_layer(layer, inputs, renderer)
    scene.flush_direct_layer()
    report.mem_images = len(scene.mem_images)
    report.mem_bytes = scene.mem_bytes

    ir_json = json.dumps(builder.build(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return ir_json, scene.mem_images, report


async def try_render_custom_profile_card_payload(
    request: CustomProfileCardRenderRequest,
) -> EncodedImagePayload | None:
    """Skia path for /profile/custom-profile-card; ``None`` means "Pillow, please"."""
    if not skia_plot_enabled():
        _record(OUTCOME_DISABLED)
        return None
    try:
        native = load_native_renderer()
    except ImportError as exc:
        # Also where a too-old wheel (IR_CAPABILITY < 8, no Transform node) fails open.
        logger.error("haruki_skia_renderer not importable (%s); falling back to Pillow", exc)
        _record(OUTCOME_FALLBACK)
        return None

    card = dict(request.card)
    profile_context = dict(request.profile_context)
    resources = dict(request.resources)
    region = request.region

    def _render():
        # Same construction as drawer._render_custom_profile_card_sync (the Pillow service path);
        # kept in one pool task so the event loop never sees the rasterization.
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
        ir_json, mem_images, report = _build_scene(renderer, card)
        if not report.complete:
            return None, report
        return native.render_scene(ir_json, mem_images), report

    started = time.perf_counter()
    try:
        result, report = await run_in_pool(_render)
    except Exception:
        # FAIL-OPEN (honor doctrine): anything escaping here would skip _record and 500 instead
        # of letting Pillow render and raise the canonical error (e.g. the ValueError -> 400).
        logger.exception("custom_profile_card backend=skia failed; falling back to Pillow")
        _record(OUTCOME_ERROR)
        return None
    scene_metrics = report.metrics()
    record_scene_completeness(CUSTOM_PROFILE_ENDPOINT, scene_metrics)
    if result is None:
        from src.core.debug import current_request_context

        context = current_request_context()
        logger.warning(
            "custom_profile.scene id=%s complete=false visible=%d native=%d hybrid=%d "
            "missing=%d unresolved=%d mem_images=%d mem_bytes=%d issues=%s",
            context["request_id"],
            report.visible_elements,
            report.native_elements,
            report.hybrid_elements,
            report.missing_elements,
            report.unresolved_elements,
            report.mem_images,
            report.mem_bytes,
            report.issues,
        )
        _record(OUTCOME_FALLBACK)
        return None
    try:
        payload = payload_from_native(result)
    except Exception:
        logger.exception("custom_profile_card backend=skia returned an invalid payload; falling back to Pillow")
        _record(OUTCOME_ERROR)
        return None
    payload.native_metrics = {**(payload.native_metrics or {}), **report.native_metrics()}
    _record(OUTCOME_SKIA, payload)
    logger.info(
        "custom_profile_card backend=skia total=%.3fs bytes=%d image=%sx%s",
        time.perf_counter() - started,
        len(payload.image_bytes),
        payload.image_width,
        payload.image_height,
    )
    return payload
