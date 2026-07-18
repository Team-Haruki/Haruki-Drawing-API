"""Skia Render-IR path for the /profile/custom-profile-card endpoint (Phase 1: composition).

The custom profile renderer is not a plot.py widget tree: it rasterizes Unity layout elements to
local RGBA layers and affine-composites them (see ``PNGRenderer.render_card``). Phase 1 keeps the
per-element rasterization in Python (``render_content_for_card`` — TMP-SDF text, prefab widgets,
shapes are unchanged) and moves the COMPOSITING to Skia: every layer ships as a ``mem:`` raster
placed by a ``Transform`` node built from the same ``layer_transform_inputs`` numbers the Pillow
path consumes, replacing the PIL affine / 2x rotation-supersample / premul round-trips (and the
Pillow PNG encode — the native pass encodes). Phase 2 will replace the text rasters with SdfQuad
nodes.

Parity-critical mirrors of the Pillow path:
- Unrotated, unscaled layers are pasted at ROUNDED integer positions (the ``angle ~ 0`` branch of
  ``prepare_canvas_clipped_transformed_layer``); the scene emits those as plain integer-placed
  images with no Transform, so they stay pixel-crisp instead of drifting subpixel.
- Minification (combined scale < ~0.98) keeps the Python two-step BICUBIC pre-resize: PIL's
  ``resize`` scales its kernel with the ratio and Skia sampling through a CTM does not.
- Decorative direct-raster TMP texts draw onto full-canvas PIL layers exactly as in
  ``render_card``; consecutive runs accumulate on one layer and flush in z-order.

Fail-open: this function NEVER raises — every failure records exactly one outcome and returns
``None`` so the route falls back to Pillow, which raises the canonical user-visible errors.
"""

from __future__ import annotations

import json
import logging
import math
import time
from typing import Any

from PIL import Image

from src.core.heavy_render_pool import EncodedImagePayload
from src.sekai.base.utils import run_in_pool
from src.sekai.profile.custom_profile.renderer import (
    PROFILE_RENDER_VIEW_H,
    PROFILE_RENDER_VIEW_W,
    LayerTransformInputs,
    PNGRenderer,
    harden_rgba_alpha,
)
from src.sekai.profile.model import CustomProfileCardRenderRequest
from src.sekai.skia_renderer.canvas import load_native_renderer, payload_from_native, skia_plot_enabled
from src.sekai.skia_renderer.ir_builder import IRBuilder
from src.sekai.skia_renderer.render_stats import (
    OUTCOME_DISABLED,
    OUTCOME_ERROR,
    OUTCOME_FALLBACK,
    OUTCOME_SKIA,
    backend_for_outcome,
    record_native_metrics,
    record_render,
)
from src.settings import ASSETS_BASE_DIR, DEFAULT_BOLD_FONT, DEFAULT_FONT, FONT_DIR, JPG_QUALITY

logger = logging.getLogger("custom_profile.draw.perf")

# /render-stats + the ``backend=`` log field key. Like honor and chart, this scene is hand-built
# (no plot.py tree exists to hand to render_canvas_payload), so it records its own outcome —
# exactly one per attempt.
CUSTOM_PROFILE_ENDPOINT = "custom_profile_card"

# Mirror of prepare_transformed_layer's branch thresholds (renderer.py): the exact-integer paste
# branch triggers at angle % 360 ~ 0; the minification carve-out keeps PIL resize semantics.
_ANGLE_EPS = 1.0e-9
_MIN_SCALE_FOLD = 0.98


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


def _new_builder(width: int, height: int) -> IRBuilder:
    # export_format is HARDCODED png: the route pins PNG (the card is RGBA with real
    # transparency), regardless of the global EXPORT_IMAGE_FORMAT.
    return IRBuilder(
        width,
        height,
        assets_base_dir=str(ASSETS_BASE_DIR),
        font_dir=str(FONT_DIR),
        default_font=DEFAULT_FONT,
        bold_font=DEFAULT_BOLD_FONT,
        export_format="png",
        jpg_quality=JPG_QUALITY,
    )


class _SceneAssembler:
    """Accumulates the z-ordered element scene: mem rasters + Transform placements."""

    def __init__(self, builder: IRBuilder, canvas_size: tuple[int, int]) -> None:
        self.builder = builder
        self.canvas_size = canvas_size
        self.mem_images: dict[str, tuple[int, int, bytes]] = {}
        self._direct_layer: Image.Image | None = None

    def _mem_ref(self, image: Image.Image) -> str:
        rgba = image if image.mode == "RGBA" else image.convert("RGBA")
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


def _build_scene(renderer: PNGRenderer, card: dict[str, Any]) -> tuple[bytes, dict[str, tuple[int, int, bytes]]]:
    canvas_size = (int(PROFILE_RENDER_VIEW_W), int(PROFILE_RENDER_VIEW_H))
    builder = _new_builder(*canvas_size)
    # render_card starts from an OPAQUE WHITE base (Image.new(..., (255, 255, 255, 255))), not a
    # transparent canvas — the story background does not always cover the outermost pixels.
    builder.rect((0, 0), canvas_size, fill=(255, 255, 255, 255))
    scene = _SceneAssembler(builder, canvas_size)
    card_ref = renderer.native_card_ref(card)

    # Same walk as render_card's direct-raster loop: decorative TMP texts draw straight onto an
    # accumulating full-canvas layer; everything else renders to a local layer and is placed by
    # the shared layer_transform_inputs numbers. Audit records mirror the Pillow statuses.
    for content in renderer.build_native_contents(card):
        if renderer.render_content_direct_on_card(scene.direct_layer(), content):
            renderer.record_native_audit(card_ref, content, "rendered-direct", None)
            continue
        rendered = renderer.render_content_for_card(content)
        renderer.record_native_audit(card_ref, content, rendered.status, rendered.result)
        if not isinstance(rendered.result, tuple):
            continue
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

    ir_json = json.dumps(builder.build(), ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    return ir_json, scene.mem_images


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
        )
        ir_json, mem_images = _build_scene(renderer, card)
        return native.render_scene(ir_json, mem_images)

    started = time.perf_counter()
    try:
        result = await run_in_pool(_render)
        payload = payload_from_native(result)
    except Exception:
        # FAIL-OPEN (honor doctrine): anything escaping here would skip _record and 500 instead
        # of letting Pillow render and raise the canonical error (e.g. the ValueError -> 400).
        logger.exception("custom_profile_card backend=skia failed; falling back to Pillow")
        _record(OUTCOME_ERROR)
        return None
    _record(OUTCOME_SKIA, payload)
    logger.info(
        "custom_profile_card backend=skia total=%.3fs bytes=%d image=%sx%s",
        time.perf_counter() - started,
        len(payload.image_bytes),
        payload.image_width,
        payload.image_height,
    )
    return payload
