"""Renderer-neutral display lists for the strict, plain TMP ``TextContent`` subset.

This module is deliberately a *layout bridge*, not a text rasterizer.  It asks the existing
``PNGRenderer`` TMP layout oracle for preferred/mesh layout data and turns that data into
alphabetic-baseline text operations plus one local-to-canvas affine transform.  It never creates
Pillow images, glyph masks, SDF fields, or ``mem:`` payloads.

The first native consumer is intentionally narrow.  It accepts dynamic source-font TMP assets
whose face/material settings reduce to ordinary filled text, and rejects rich/decorative text,
font fallbacks, TMP spacing/effect tags, and compatibility layout modes.  ``None`` is the
explicit fail-open result for anything outside that subset.

Imports from ``renderer`` are kept inside the builder.  This lets ``renderer.py`` eventually
import the display-list types without a module-import cycle while still reusing its canonical
``StyledLine`` splitter and ``TMPNativeTextLayout`` implementation.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from pathlib import Path
from typing import Any, Literal, Protocol, TypeAlias

from src.sekai.profile.custom_profile.svg import TextStyle, parse_tmp_text, unity_rotation_degrees

Color: TypeAlias = tuple[int, int, int, int]
HAlign = Literal["left", "center", "right"]
Matrix: TypeAlias = tuple[float, float, float, float, float, float]

_DEFAULT_FONT_NAME = "FOT-RodinNTLGPro-DB"
_EPS = 1.0e-6
_PLAIN_HORIZONTAL_ALIGNMENTS: dict[int, HAlign] = {
    0x01: "left",
    0x02: "center",
    0x04: "right",
}
_PLAIN_VERTICAL_ALIGNMENTS = {
    0x0100: "top",
    0x0200: "middle",
    0x0400: "bottom",
    0x0800: "baseline",
}
_ZERO_MATERIAL_FIELDS = (
    "face_dilate",
    "outline_width",
    "outline_softness",
    "sharpness",
    "underlay_softness",
    "underlay_offset_x",
    "underlay_offset_y",
    "normal_spacing_offset",
    "weight_normal",
)


class TMPTextLayoutProvider(Protocol):
    """Structural seam implemented by ``PNGRenderer`` without any raster calls."""

    text_layout: str
    text_vertical_mode: str
    tmp_text_render_mode: str
    tmp_scale_mode: str
    tmp_box_mode: str
    tmp_block_mode: str
    tmp_metrics_mode: str
    tmp_dynamic_sdf: bool
    tmp_font_scale: float
    tmp_space_width_factor: float
    include_empty_lines: bool
    rotation_sign: int
    position_scale_x: float
    position_scale_y: float
    text_fonts: Mapping[int, str]
    tmp_font_library: Any

    def generate_text_data(self, item: dict[str, Any]) -> Any: ...

    def update_text_mesh_state(self, data: Any, font_name: str) -> Any: ...

    def font_path_for(self, font_name: str) -> Path: ...

    def is_decorative_text_item(self, item: dict[str, Any]) -> bool: ...

    def tmp_native_text_layout(
        self,
        lines: list[Any],
        font_name: str,
        font_path: Path,
        base_size: float,
        line_spacing: float,
        dominant_size: float,
        layout_mode: str,
        outline_dilate: float,
        margin_width: float | None,
        *,
        source_metrics_only: bool = False,
    ) -> Any | None: ...

    def tmp_resolve_percent_indent_margin_width(
        self,
        lines: list[Any],
        font_name: str,
        font_path: Path,
        base_size: float,
        line_spacing: float,
        dominant_size: float,
        outline_dilate: float,
        zero_margin_layout: Any | None,
    ) -> float | None: ...

    def tmp_text_box_size(
        self,
        dominant_size: float,
        content_width: float,
        content_height: float,
    ) -> tuple[float, float]: ...

    def tmp_native_baseline_downs(
        self,
        layout: Any,
        box_h: float,
        vertical_align: str,
    ) -> list[float]: ...

    def unity_point(self, position: dict[str, Any]) -> tuple[float, float]: ...


@dataclass(frozen=True, slots=True)
class TMPTextFontRef:
    """Source font used by both the TMP metrics oracle and a future native text emitter."""

    name: str
    path: Path


@dataclass(frozen=True, slots=True)
class TMPTextOp:
    """One plain line, positioned at its alphabetic baseline in box-centred local space."""

    line_index: int
    text: str
    pos: tuple[float, float]
    size: float
    fill: Color
    font: TMPTextFontRef
    align: Literal["left"] = "left"
    baseline: Literal["alphabetic"] = "alphabetic"
    letter_spacing: float = 0.0


@dataclass(frozen=True, slots=True)
class TMPTextTransform:
    """TMP RectTransform placement for operations whose local origin is the text-box centre.

    ``anchor`` is already in final canvas coordinates (``PNGRenderer.unity_point`` applies the
    profile position scale to the Unity position).  ``object_scale`` and ``post_scale`` affect
    glyph geometry and are therefore retained in the linear part of :attr:`matrix`.
    """

    anchor: tuple[float, float]
    object_scale: tuple[float, float]
    post_scale: tuple[float, float]
    rotation: float

    @property
    def matrix(self) -> Matrix:
        theta = math.radians(self.rotation % 360.0)
        cos_t = math.cos(theta)
        sin_t = math.sin(theta)
        sx = self.object_scale[0] * self.post_scale[0]
        sy = self.object_scale[1] * self.post_scale[1]
        return (
            cos_t * sx,
            -sin_t * sy,
            self.anchor[0],
            sin_t * sx,
            cos_t * sy,
            self.anchor[1],
        )

    def map_point(self, point: tuple[float, float]) -> tuple[float, float]:
        a, b, c, d, e, f = self.matrix
        return a * point[0] + b * point[1] + c, d * point[0] + e * point[1] + f


@dataclass(frozen=True, slots=True)
class TMPTextDisplayList:
    """Plain TMP text operations plus all geometry needed for native placement."""

    text: str
    font: TMPTextFontRef
    box_size: tuple[float, float]
    preferred_size: tuple[float, float]
    horizontal_alignment: HAlign
    vertical_alignment: str
    line_widths: tuple[float, ...]
    baselines: tuple[float, ...]
    transform: TMPTextTransform
    ops: tuple[TMPTextOp, ...]

    def canvas_pos(self, op: TMPTextOp) -> tuple[float, float]:
        return self.transform.map_point(op.pos)


@dataclass(frozen=True, slots=True)
class _PlainTMPMesh:
    object_data: Mapping[str, Any]
    raw_text: str
    font_name: str
    font_color: str
    fill: Color
    base_size: float
    line_spacing: float
    horizontal: HAlign
    vertical: str


@dataclass(frozen=True, slots=True)
class _PlainTMPSource:
    mesh: _PlainTMPMesh
    font_path: Path
    font_size: float
    base_style: TextStyle
    layout_lines: list[Any]
    dominant_size: float


@dataclass(frozen=True, slots=True)
class _PlainTMPLayout:
    preferred: Any
    mesh: Any
    box_w: float
    box_h: float
    baselines: list[float]
    transform: TMPTextTransform


@dataclass(frozen=True, slots=True)
class _PlainTMPOps:
    line_widths: tuple[float, ...]
    ops: tuple[TMPTextOp, ...]


def _finite(value: Any) -> float | None:
    try:
        result = float(value)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def _near_zero(value: Any) -> bool:
    number = _finite(value)
    return number is not None and abs(number) <= _EPS


def _color_rgba(value: str) -> Color | None:
    raw = value.strip().removeprefix("#")
    if len(raw) != 6:
        return None
    try:
        rgb = tuple(int(raw[index : index + 2], 16) for index in (0, 2, 4))
    except ValueError:
        return None
    return rgb[0], rgb[1], rgb[2], 255


def _plain_style(style: TextStyle, base: TextStyle) -> bool:
    return (
        style.color == base.color
        and abs(style.alpha - 1.0) <= _EPS
        and abs(style.size - base.size) <= _EPS
        and abs(style.scale_x - 1.0) <= _EPS
        and abs(style.cspace) <= _EPS
        and style.mspace is None
        and abs(style.indent) <= _EPS
        and abs(style.line_indent) <= _EPS
        and style.line_height is None
        and abs(style.rotate) <= _EPS
        and abs(style.voffset) <= _EPS
        and style.mark_color is None
        and not style.bold
        and not style.italic
        and not style.underline
        and not style.strike
        and style.indent_percent is None
        and style.line_indent_percent is None
        and style.pos is None
        and style.pos_percent is None
    )


def _resolved_source_font(renderer: TMPTextLayoutProvider, font_name: str, asset: Any) -> Path | None:
    runtime_source = renderer.tmp_font_library.runtime_source_font_path(asset)
    if runtime_source is None:
        return None
    try:
        runtime_path = Path(runtime_source).resolve(strict=True)
        selected_path = Path(renderer.font_path_for(font_name)).resolve(strict=True)
    except OSError:
        return None
    if not runtime_path.is_file() or runtime_path != selected_path:
        return None
    return runtime_path


def _font_has_every_glyph(
    renderer: TMPTextLayoutProvider,
    font_name: str,
    text: str,
    font_size: float,
) -> bool:
    source_metrics = getattr(renderer.tmp_font_library, "source_glyph_metrics", None)
    if not callable(source_metrics):
        return False
    try:
        return all(
            source_metrics(font_name, char, font_size, include_fallback=False) is not None
            for char in text
            if char != "\n"
        )
    except (OSError, TypeError, ValueError):
        return False


def _plain_runtime(renderer: TMPTextLayoutProvider) -> bool:
    return (
        renderer.text_layout == "tmp"
        and renderer.text_vertical_mode in {"tmp-native", "tmp-native-top"}
        and renderer.tmp_text_render_mode == "sdf"
        and renderer.tmp_scale_mode == "fx-native"
        and renderer.tmp_box_mode == "preferred"
        and renderer.tmp_block_mode == "glyph"
        and renderer.tmp_metrics_mode in {"asset", "asset-fallback"}
        and renderer.tmp_dynamic_sdf
        and abs(renderer.tmp_space_width_factor - 1.0) <= _EPS
    )


def _plain_dynamic_asset(renderer: TMPTextLayoutProvider, asset: Any) -> bool:
    face_scale = _finite(getattr(asset, "face_scale", None))
    return (
        int(getattr(asset, "atlas_population_mode", -1)) == 1
        and not bool(getattr(asset, "has_static_glyphs", True))
        and not getattr(asset, "fallback_names", ())
        and face_scale is not None
        and abs(face_scale - renderer.tmp_font_scale) <= _EPS
        and all(_near_zero(getattr(asset, field, None)) for field in _ZERO_MATERIAL_FIELDS)
    )


def _object_transform(
    renderer: TMPTextLayoutProvider,
    object_data: Mapping[str, Any],
) -> TMPTextTransform | None:
    scale = object_data.get("scale") or {}
    if not isinstance(scale, Mapping):
        return None
    object_scale_x = _finite(scale.get("x") or 1.0)
    object_scale_y = _finite(scale.get("y") or object_scale_x or 1.0)
    post_scale_x = _finite(renderer.position_scale_x)
    post_scale_y = _finite(renderer.position_scale_y)
    if (
        object_scale_x is None
        or object_scale_y is None
        or post_scale_x is None
        or post_scale_y is None
        or min(object_scale_x, object_scale_y, post_scale_x, post_scale_y) <= 0.0
    ):
        return None

    position = object_data.get("position") or {}
    rotation_data = object_data.get("rotation") or {}
    if not isinstance(position, Mapping) or not isinstance(rotation_data, Mapping):
        return None
    try:
        anchor = renderer.unity_point(dict(position))
        rotation = renderer.rotation_sign * unity_rotation_degrees(dict(rotation_data))
    except (TypeError, ValueError):
        return None
    anchor_x = _finite(anchor[0])
    anchor_y = _finite(anchor[1])
    rotation = _finite(rotation)
    if anchor_x is None or anchor_y is None or rotation is None:
        return None
    return TMPTextTransform(
        anchor=(anchor_x, anchor_y),
        object_scale=(object_scale_x, object_scale_y),
        post_scale=(post_scale_x, post_scale_y),
        rotation=rotation,
    )


def _plain_tmp_mesh(
    renderer: TMPTextLayoutProvider,
    item: Mapping[str, Any],
    object_data: Mapping[str, Any] | None,
) -> _PlainTMPMesh | None:
    if not _plain_runtime(renderer):
        return None
    mutable_item = dict(item)
    resolved_object_data = object_data if object_data is not None else mutable_item.get("objectData")
    if not isinstance(resolved_object_data, Mapping) or not bool(resolved_object_data.get("visible", False)):
        return None

    text_data = renderer.generate_text_data(mutable_item)
    raw_text = str(text_data.text).replace("\r\n", "\n").replace("\r", "\n")
    if not raw_text.strip() or "<" in raw_text or any(char in raw_text for char in ("\t", "\x00", "\x03")):
        return None
    if renderer.is_decorative_text_item(mutable_item):
        return None

    font_name = renderer.text_fonts.get(int(text_data.font_id), _DEFAULT_FONT_NAME) or _DEFAULT_FONT_NAME
    mesh_state = renderer.update_text_mesh_state(text_data, font_name)
    if not _near_zero(text_data.outline_size) or not _near_zero(mesh_state.underlay_dilate):
        return None
    fill = _color_rgba(str(mesh_state.font_color))
    base_size = _finite(mesh_state.font_size)
    line_spacing = _finite(mesh_state.tmp_line_spacing)
    if fill is None or base_size is None or base_size <= 0.0 or line_spacing is None:
        return None

    horizontal = _PLAIN_HORIZONTAL_ALIGNMENTS.get(int(mesh_state.align) & 0x00FF)
    vertical = _PLAIN_VERTICAL_ALIGNMENTS.get(int(mesh_state.align) & 0xFF00)
    if horizontal is None or vertical is None:
        return None
    return _PlainTMPMesh(
        object_data=resolved_object_data,
        raw_text=raw_text,
        font_name=font_name,
        font_color=str(mesh_state.font_color),
        fill=fill,
        base_size=base_size,
        line_spacing=line_spacing,
        horizontal=horizontal,
        vertical=vertical,
    )


def _plain_tmp_source(renderer: TMPTextLayoutProvider, mesh: _PlainTMPMesh) -> _PlainTMPSource | None:
    asset = renderer.tmp_font_library.active_asset(mesh.font_name)
    if asset is None or not _plain_dynamic_asset(renderer, asset):
        return None
    font_path = _resolved_source_font(renderer, mesh.font_name, asset)
    font_size = mesh.base_size * renderer.tmp_font_scale
    if font_path is None or not math.isfinite(font_size) or font_size <= 0.0:
        return None
    if not _font_has_every_glyph(renderer, mesh.font_name, mesh.raw_text, font_size):
        return None

    base_style = TextStyle(
        color=mesh.font_color,
        alpha=1.0,
        size=mesh.base_size,
        scale_x=1.0,
        cspace=0.0,
        mspace=None,
        indent=0.0,
        line_indent=0.0,
        line_height=None,
        rotate=0.0,
        voffset=0.0,
        mark_color=None,
        bold=False,
        italic=False,
        underline=False,
        strike=False,
    )
    tokens = parse_tmp_text(mesh.raw_text, base_style)
    from src.sekai.profile.custom_profile.renderer import split_runs_by_line_with_style

    lines = split_runs_by_line_with_style(tokens, base_style)
    if not lines or any(not _plain_style(run.style, base_style) for line in lines for run in line.runs):
        return None
    layout_lines = [line for line in lines if renderer.include_empty_lines or line.runs]
    if not layout_lines:
        return None
    return _PlainTMPSource(
        mesh=mesh,
        font_path=font_path,
        font_size=font_size,
        base_style=base_style,
        layout_lines=layout_lines,
        dominant_size=max((line.style.size for line in layout_lines), default=mesh.base_size),
    )


def _plain_tmp_layout(renderer: TMPTextLayoutProvider, source: _PlainTMPSource) -> _PlainTMPLayout | None:
    mesh = source.mesh
    preferred_layout = renderer.tmp_native_text_layout(
        source.layout_lines,
        mesh.font_name,
        source.font_path,
        mesh.base_size,
        mesh.line_spacing,
        source.dominant_size,
        "preferred",
        0.0,
        None,
        source_metrics_only=True,
    )
    if preferred_layout is None:
        return None
    if (
        renderer.tmp_resolve_percent_indent_margin_width(
            source.layout_lines,
            mesh.font_name,
            source.font_path,
            mesh.base_size,
            mesh.line_spacing,
            source.dominant_size,
            0.0,
            preferred_layout,
        )
        is not None
    ):
        return None
    mesh_layout = renderer.tmp_native_text_layout(
        source.layout_lines,
        mesh.font_name,
        source.font_path,
        mesh.base_size,
        mesh.line_spacing,
        source.dominant_size,
        "mesh",
        0.0,
        None,
        source_metrics_only=True,
    )
    if mesh_layout is None or len(mesh_layout.lines) != len(source.layout_lines):
        return None

    box_size = renderer.tmp_text_box_size(
        preferred_layout.dominant_size,
        preferred_layout.preferred_width,
        preferred_layout.content_height,
    )
    box_w = _finite(box_size[0])
    box_h = _finite(box_size[1])
    if box_w is None or box_h is None or min(box_w, box_h) <= 0.0:
        return None
    baselines = renderer.tmp_native_baseline_downs(
        mesh_layout.line_layout,
        box_h,
        "top" if renderer.text_vertical_mode == "tmp-native-top" else mesh.vertical,
    )
    if len(baselines) != len(mesh_layout.lines) or any(_finite(value) is None for value in baselines):
        return None
    transform = _object_transform(renderer, mesh.object_data)
    if transform is None:
        return None
    return _PlainTMPLayout(preferred_layout, mesh_layout, box_w, box_h, baselines, transform)


def _plain_line_x(horizontal: str, box_width: float, line_width: float) -> float:
    if horizontal == "center":
        return (box_width - line_width) / 2.0
    if horizontal == "right":
        return box_width - line_width
    return 0.0


def _plain_tmp_ops(source: _PlainTMPSource, layout: _PlainTMPLayout) -> _PlainTMPOps | None:
    font = TMPTextFontRef(source.mesh.font_name, source.font_path)
    ops: list[TMPTextOp] = []
    line_widths: list[float] = []
    for line_index, (line_info, baseline) in enumerate(zip(layout.mesh.lines, layout.baselines, strict=True)):
        line_width = _finite(line_info.width)
        if line_width is None or line_width < 0.0 or len(line_info.run_metrics) > 1:
            return None
        line_widths.append(line_width)
        if not line_info.run_metrics:
            continue
        run, run_x, _run_width = line_info.run_metrics[0]
        if not run.text or not _plain_style(run.style, source.base_style):
            return None
        run_x = _finite(run_x)
        if run_x is None:
            return None
        line_x = _plain_line_x(source.mesh.horizontal, layout.box_w, line_width)
        ops.append(
            TMPTextOp(
                line_index=line_index,
                text=run.text,
                pos=(line_x + run_x - layout.box_w / 2.0, float(baseline) - layout.box_h / 2.0),
                size=source.font_size,
                fill=source.mesh.fill,
                font=font,
            )
        )
    return _PlainTMPOps(tuple(line_widths), tuple(ops)) if ops else None


def build_simple_tmp_text_display_list(
    renderer: TMPTextLayoutProvider,
    item: Mapping[str, Any],
    object_data: Mapping[str, Any] | None = None,
) -> TMPTextDisplayList | None:
    """Build a no-raster display list for a strictly plain TMP ``TextContent``.

    The returned operation coordinates are relative to the centre of TMP's final text box.
    Consequently the large local raster padding and the later alpha-bbox trim used by the Pillow
    renderer cancel out and are not part of the native contract.
    """

    mesh = _plain_tmp_mesh(renderer, item, object_data)
    if mesh is None:
        return None
    source = _plain_tmp_source(renderer, mesh)
    if source is None:
        return None
    layout = _plain_tmp_layout(renderer, source)
    if layout is None:
        return None
    rendered_ops = _plain_tmp_ops(source, layout)
    if rendered_ops is None:
        return None

    preferred_width = _finite(layout.preferred.preferred_width)
    preferred_height = _finite(layout.preferred.preferred_height)
    if preferred_width is None or preferred_height is None:
        return None
    return TMPTextDisplayList(
        text=mesh.raw_text,
        font=TMPTextFontRef(mesh.font_name, source.font_path),
        box_size=(layout.box_w, layout.box_h),
        preferred_size=(preferred_width, preferred_height),
        horizontal_alignment=mesh.horizontal,
        vertical_alignment=mesh.vertical,
        line_widths=rendered_ops.line_widths,
        baselines=tuple(float(value) for value in layout.baselines),
        transform=layout.transform,
        ops=rendered_ops.ops,
    )
