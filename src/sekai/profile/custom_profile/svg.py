#!/usr/bin/env python3
# ruff: noqa
from __future__ import annotations

import argparse
import html
import json
import math
import re
import struct
from dataclasses import dataclass, replace
from pathlib import Path
from typing import Any
from urllib.parse import quote


DEFAULT_PROFILE = Path("/Users/deseer/PycharmProjects/metadata/profile.json")
DEFAULT_MASTERDATA = Path("/Users/deseer/PycharmProjects/haruki-sekai-sc-master/master")
DEFAULT_ASSETS = Path("/Users/deseer/PycharmProjects/Haruki-Drawing-API/data/cn-assets/startapp/custom_profile")
DEFAULT_FONTS = Path("/Users/deseer/Downloads/sekai-custom-profile-fonts/cn/fonts")

CANVAS_W = 2048
CANVAS_H = 1024
GAME_VIEWPORT_TOP = 58
GAME_VIEWPORT_H = 909
TMP_OUTLINE_FACTOR = 0.11
SHAPE_OUTLINE_SCALE_FACTOR = 0.055
TMP_SUPERSCRIPT_SIZE = 0.5
TMP_SUPERSCRIPT_OFFSET_FACTOR = 0.88
TMP_SUBSCRIPT_SIZE = 0.5
TMP_SUBSCRIPT_OFFSET_FACTOR = -0.12


@dataclass(frozen=True)
class TextStyle:
    color: str
    alpha: float
    size: float
    scale_x: float
    cspace: float
    mspace: float | None
    indent: float
    line_indent: float
    line_height: float | None
    rotate: float
    voffset: float
    mark_color: str | None
    bold: bool
    italic: bool
    underline: bool
    strike: bool
    indent_percent: float | None = None
    line_indent_percent: float | None = None


@dataclass(frozen=True)
class TextRun:
    text: str
    style: TextStyle


@dataclass(frozen=True)
class TextBreak:
    pass


@dataclass(frozen=True)
class TextStyleMarker:
    style: TextStyle
    kind: str | None = None
    opening: bool = True


TextToken = TextRun | TextBreak | TextStyleMarker
INVALID_TMP_TAG = object()


def load_json(path: Path) -> Any:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def load_index(path: Path) -> dict[int, dict[str, Any]]:
    if not path.exists():
        return {}
    return {int(item["id"]): item for item in load_json(path) if int(item.get("id", 0)) > 0}


def file_uri(path: Path) -> str:
    return path.resolve().as_uri()


def css_url(path: Path) -> str:
    return "url('" + file_uri(path).replace("'", "%27") + "')"


def svg_href(path: Path) -> str:
    return file_uri(path)


def svg_escape(value: str) -> str:
    return html.escape(value, quote=True)


def color_or(default: str, value: str | None) -> str:
    value = (value or "").strip()
    if not value:
        return default
    if value.startswith("#"):
        raw = value[1:]
        if re.fullmatch(r"[0-9a-fA-F]{3,4}", raw):
            return "#" + "".join(ch * 2 for ch in raw[:3])
        if re.fullmatch(r"[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", raw):
            return "#" + raw[:6]
        return default
    if re.fullmatch(r"[0-9a-fA-F]{3,4}", value):
        return "#" + "".join(ch * 2 for ch in value[:3])
    if re.fullmatch(r"[0-9a-fA-F]{6}([0-9a-fA-F]{2})?", value):
        return "#" + value[:6]
    return default


def parse_hex_alpha(value: str) -> float:
    value = value.strip()
    if value.startswith("#"):
        value = value[1:]
    if not value:
        return 1.0
    try:
        if len(value) <= 2:
            return max(0.0, min(1.0, int(value, 16) / 255.0))
        return max(0.0, min(1.0, int(value[-2:], 16) / 255.0))
    except ValueError:
        return 1.0


def parse_tmp_color_alpha(value: str, fallback: float) -> float:
    raw = value.strip()
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        raw = raw[1:-1].strip()
    if raw.startswith("#"):
        raw = raw[1:]
    try:
        if re.fullmatch(r"[0-9a-fA-F]{4}", raw):
            return max(0.0, min(1.0, int(raw[3] * 2, 16) / 255.0))
        if re.fullmatch(r"[0-9a-fA-F]{8}", raw):
            return max(0.0, min(1.0, int(raw[6:8], 16) / 255.0))
    except ValueError:
        return fallback
    return fallback


def parse_float(value: str, fallback: float) -> float:
    try:
        raw = value.strip()
        if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
            raw = raw[1:-1].strip()
        return float(raw.rstrip("%"))
    except ValueError:
        return fallback


def parse_tmp_numeric(
    value: str, fallback: float, font_size: float | None = None, percent_base: float | None = None
) -> float:
    raw = value.strip()
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        raw = raw[1:-1].strip()
    lower = raw.lower()
    try:
        if lower.endswith("em"):
            number = float(lower[:-2].strip())
            return number * (font_size if font_size is not None else fallback)
        if lower.endswith("px"):
            return float(lower[:-2].strip())
        if lower.endswith("%"):
            number = float(lower[:-1].strip())
            base = percent_base if percent_base is not None else (font_size if font_size is not None else fallback)
            return base * number / 100.0
        return float(raw)
    except ValueError:
        return fallback


def parse_tmp_percent(value: str) -> float | None:
    raw = value.strip()
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        raw = raw[1:-1].strip()
    if not raw.endswith("%"):
        return None
    try:
        return float(raw[:-1].strip()) / 100.0
    except ValueError:
        return None


def parse_tmp_scale(value: str, fallback: float) -> float:
    raw = value.strip()
    if (raw.startswith('"') and raw.endswith('"')) or (raw.startswith("'") and raw.endswith("'")):
        raw = raw[1:-1].strip()
    try:
        if raw.endswith("%"):
            return float(raw[:-1].strip()) / 100.0
        return float(raw)
    except ValueError:
        return fallback


def parse_tmp_tag_value(raw: str) -> str:
    value = raw.split("=", 1)[1].strip()
    if (value.startswith('"') and value.endswith('"')) or (value.startswith("'") and value.endswith("'")):
        value = value[1:-1].strip()
    return value


def apply_tmp_color(value: str, style: TextStyle) -> TextStyle:
    return replace(
        style,
        color=color_or(style.color, value),
        alpha=parse_tmp_color_alpha(value, style.alpha),
    )


def apply_tmp_tag(tag: str, style: TextStyle) -> TextStyle | None | object:
    raw = tag.strip()
    tag_l = raw.lower()
    if tag_l.startswith("/"):
        return None
    if tag_l.startswith("#") and re.fullmatch(r"#[0-9a-f]{3,4}([0-9a-f]{3,4})?", tag_l):
        return apply_tmp_color(raw, style)
    if tag_l.startswith("color="):
        return apply_tmp_color(parse_tmp_tag_value(raw), style)
    if tag_l.startswith("alpha="):
        return replace(style, alpha=parse_hex_alpha(parse_tmp_tag_value(raw)))
    if tag_l.startswith("size="):
        return replace(style, size=parse_tmp_numeric(parse_tmp_tag_value(raw), style.size, style.size, style.size))
    if tag_l.startswith("scale="):
        return replace(style, scale_x=parse_tmp_scale(parse_tmp_tag_value(raw), style.scale_x), rotate=0.0)
    if tag_l.startswith("cspace="):
        return replace(style, cspace=parse_tmp_numeric(parse_tmp_tag_value(raw), style.cspace, style.size, style.size))
    if tag_l.startswith("mspace="):
        return replace(style, mspace=parse_tmp_numeric(parse_tmp_tag_value(raw), style.size, style.size, style.size))
    if tag_l.startswith("indent="):
        value = parse_tmp_tag_value(raw)
        percent = parse_tmp_percent(value)
        if percent is not None:
            return replace(style, indent=0.0, indent_percent=percent)
        return replace(
            style, indent=parse_tmp_numeric(value, style.indent, style.size, style.size), indent_percent=None
        )
    if tag_l.startswith("line-indent="):
        value = parse_tmp_tag_value(raw)
        percent = parse_tmp_percent(value)
        if percent is not None:
            return replace(style, line_indent=0.0, line_indent_percent=percent)
        return replace(
            style,
            line_indent=parse_tmp_numeric(value, style.line_indent, style.size, style.size),
            line_indent_percent=None,
        )
    if tag_l.startswith("line-height="):
        return replace(
            style, line_height=parse_tmp_numeric(parse_tmp_tag_value(raw), style.size, style.size, style.size)
        )
    if tag_l.startswith("rotate="):
        return replace(style, rotate=parse_float(parse_tmp_tag_value(raw), style.rotate), scale_x=1.0)
    if tag_l.startswith("voffset="):
        return replace(
            style, voffset=parse_tmp_numeric(parse_tmp_tag_value(raw), style.voffset, style.size, style.size)
        )
    if tag_l.startswith("mark="):
        return replace(style, mark_color=color_or(style.color, parse_tmp_tag_value(raw)))
    if tag_l == "sup":
        return replace(
            style,
            voffset=style.voffset + style.size * TMP_SUPERSCRIPT_OFFSET_FACTOR,
            size=style.size * TMP_SUPERSCRIPT_SIZE,
        )
    if tag_l == "sub":
        return replace(
            style,
            voffset=style.voffset + style.size * TMP_SUBSCRIPT_OFFSET_FACTOR,
            size=style.size * TMP_SUBSCRIPT_SIZE,
        )
    if tag_l == "b":
        return replace(style, bold=True)
    if tag_l == "i":
        return replace(style, italic=True)
    if tag_l == "u":
        return replace(style, underline=True)
    if tag_l == "s":
        return replace(style, strike=True)
    return INVALID_TMP_TAG


def tmp_tag_kind(tag: str) -> str | None:
    raw = tag.strip().rstrip("/")
    tag_l = raw.lower()
    if tag_l.startswith("/"):
        tag_l = tag_l[1:].strip()
    name = tag_l.split("=", 1)[0].strip()
    if name.startswith("#") and re.fullmatch(r"#[0-9a-f]{3,4}([0-9a-f]{3,4})?", name):
        return "color"
    if name in {
        "color",
        "alpha",
        "size",
        "scale",
        "cspace",
        "mspace",
        "indent",
        "line-indent",
        "line-height",
        "rotate",
        "voffset",
        "mark",
        "b",
        "i",
        "u",
        "s",
        "sup",
        "sub",
    }:
        return name
    return None


def restore_tmp_tag_kind(style: TextStyle, previous: TextStyle, kind: str) -> TextStyle:
    if kind == "color":
        return replace(style, color=previous.color, alpha=previous.alpha)
    if kind == "alpha":
        return replace(style, alpha=previous.alpha)
    if kind == "size":
        return replace(style, size=previous.size)
    if kind == "scale":
        return replace(style, scale_x=1.0, rotate=0.0)
    if kind == "cspace":
        return replace(style, cspace=0.0)
    if kind == "mspace":
        return replace(style, mspace=None)
    if kind == "indent":
        return replace(style, indent=previous.indent, indent_percent=previous.indent_percent)
    if kind == "line-indent":
        return replace(style, line_indent=0.0, line_indent_percent=None)
    if kind == "line-height":
        return replace(style, line_height=None)
    if kind == "rotate":
        return replace(style, scale_x=1.0, rotate=0.0)
    if kind == "voffset":
        return replace(style, voffset=0.0)
    if kind == "mark":
        return replace(style, mark_color=previous.mark_color)
    if kind == "b":
        return replace(style, bold=previous.bold)
    if kind == "i":
        return replace(style, italic=previous.italic)
    if kind == "u":
        return replace(style, underline=previous.underline)
    if kind == "s":
        return replace(style, strike=previous.strike)
    if kind in {"sup", "sub"}:
        return replace(style, size=previous.size, voffset=previous.voffset)
    return previous


def parse_tmp_text(value: str, base_style: TextStyle) -> list[TextToken]:
    value = value.replace("\r\n", "\n").replace("\r", "\n")
    runs: list[TextToken] = []
    stacks: dict[str, list[TextStyle]] = {}
    style = base_style
    buf: list[str] = []
    i = 0
    while i < len(value):
        if value[i] == "<":
            end = value.find(">", i + 1)
            if end != -1:
                tag = value[i + 1 : end].strip()
                tag_l = tag.lower().rstrip("/")
                if tag_l == "br":
                    if buf:
                        runs.append(TextRun("".join(buf), style))
                        buf.clear()
                    runs.append(TextBreak())
                    i = end + 1
                    continue
                if tag_l.startswith("/"):
                    kind = tmp_tag_kind(tag_l)
                    if kind is None:
                        buf.append(value[i])
                        i += 1
                        continue
                    if buf:
                        runs.append(TextRun("".join(buf), style))
                        buf.clear()
                    stack = stacks.get(kind)
                    previous = stack.pop() if stack else base_style
                    style = restore_tmp_tag_kind(style, previous, kind)
                    runs.append(TextStyleMarker(style, kind, False))
                    i = end + 1
                    continue
                next_style = apply_tmp_tag(tag, style)
                if next_style is INVALID_TMP_TAG:
                    buf.append(value[i])
                    i += 1
                    continue
                kind = tmp_tag_kind(tag)
                if next_style is not None and next_style != style:
                    if buf:
                        runs.append(TextRun("".join(buf), style))
                        buf.clear()
                    if kind is not None:
                        stacks.setdefault(kind, []).append(style)
                    style = next_style
                    runs.append(TextStyleMarker(style, kind, True))
                i = end + 1
                continue
        buf.append(value[i])
        i += 1
    if buf:
        runs.append(TextRun("".join(buf), style))
    return [run for run in runs if isinstance(run, (TextBreak, TextStyleMarker)) or run.text]


def split_runs_by_line(runs: list[TextToken]) -> list[list[TextRun]]:
    lines: list[list[TextRun]] = [[]]
    for run in runs:
        if isinstance(run, TextBreak):
            lines.append([])
            continue
        if isinstance(run, TextStyleMarker):
            continue
        parts = run.text.split("\n")
        for idx, part in enumerate(parts):
            if idx:
                lines.append([])
            if part:
                lines[-1].append(TextRun(part, run.style))
    return lines


def png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as f:
        header = f.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        return (1024, 1024)
    return struct.unpack(">II", header[16:24])


def unity_point(position: dict[str, Any]) -> tuple[float, float]:
    return CANVAS_W / 2 + float(position.get("x", 0)), CANVAS_H / 2 - float(position.get("y", 0))


def unity_rotation_degrees(rotation: dict[str, Any]) -> float:
    z = float(rotation.get("z", 0))
    w = float(rotation.get("w", 1))
    if z == 0 and w == 0:
        return 0.0
    return -math.degrees(2 * math.atan2(z, w))


def transform_attr(object_data: dict[str, Any], extra_scale_x: float = 1.0) -> str:
    x, y = unity_point(object_data.get("position", {}))
    scale = object_data.get("scale", {})
    sx = float(scale.get("x") or 1.0) * extra_scale_x
    sy = float(scale.get("y") or sx or 1.0)
    angle = unity_rotation_degrees(object_data.get("rotation", {}))
    return f"translate({x:.3f} {y:.3f}) rotate({angle:.5f}) scale({sx:.6f} {sy:.6f})"


def tmp_anchor(tmp_type: int) -> str:
    horizontal = tmp_type & 0x00F
    if horizontal == 0x02:
        return "middle"
    if horizontal == 0x04:
        return "end"
    return "start"


def font_path(font_dir: Path, font_name: str) -> Path | None:
    for suffix in (".otf", ".ttf", "-alt.otf"):
        candidate = font_dir / (font_name + suffix)
        if candidate.exists():
            return candidate
    return None


class Renderer:
    def __init__(
        self,
        masterdata: Path,
        assets: Path,
        fonts: Path,
        debug: bool = False,
        text_anchor_mode: str = "tmp",
        tmp_scale_mode: str = "x",
    ):
        self.masterdata = masterdata
        self.assets = assets
        self.fonts = fonts
        self.debug = debug
        self.text_anchor_mode = text_anchor_mode
        self.tmp_scale_mode = tmp_scale_mode
        self.colors = {
            item_id: color_or("#444466", item.get("colorCode"))
            for item_id, item in load_index(masterdata / "customProfileTextColors.json").items()
        }
        self.text_fonts = {
            item_id: str(item.get("fontName", "")).strip()
            for item_id, item in load_index(masterdata / "customProfileTextFonts.json").items()
        }
        self.shapes = load_index(masterdata / "customProfileShapeResources.json")
        self.general_bgs = load_index(masterdata / "customProfileGeneralBackgroundResources.json")
        self.story_bgs = load_index(masterdata / "customProfileStoryBackgroundResources.json")
        self.collections = load_index(masterdata / "customProfileCollectionResources.json")
        self.others = load_index(masterdata / "customProfileEtcResources.json")
        self.defined_masks = 0
        self.defs: list[str] = []

    def font_css(self) -> str:
        lines = [
            "text { white-space: pre; }",
            ".debug-axis { stroke: #ff00aa; stroke-width: 1; opacity: .35; }",
        ]
        for font_name in sorted(set(self.text_fonts.values())):
            if not font_name:
                continue
            path = font_path(self.fonts, font_name)
            if path:
                lines.append(f"@font-face {{ font-family: '{font_name}'; src: {css_url(path)}; font-weight: normal; }}")
        return "<style>\n" + "\n".join(lines) + "\n</style>"

    def resource_path(self, resource: dict[str, Any], fallback_dir: str | None = None) -> Path | None:
        load_val = str(resource.get("resourceLoadVal", "")).strip("/")
        file_name = str(resource.get("fileName", "")).strip("/")
        if not file_name:
            return None
        if not file_name.lower().endswith(".png"):
            file_name += ".png"
        if load_val.startswith("custom_profile/"):
            rel = load_val.removeprefix("custom_profile/")
        elif load_val == "custom_profile":
            rel = ""
        elif fallback_dir:
            rel = fallback_dir
        else:
            rel = load_val
        path = self.assets / rel / file_name
        return path if path.exists() else None

    def mask_for_image(self, path: Path, width: int, height: int) -> str:
        self.defined_masks += 1
        mask_id = f"mask_{self.defined_masks}"
        self.defs.append(
            f'<mask id="{mask_id}" maskUnits="userSpaceOnUse" '
            f'x="{-width / 2:.3f}" y="{-height / 2:.3f}" width="{width}" height="{height}">'
            f'<image href="{svg_href(path)}" x="{-width / 2:.3f}" y="{-height / 2:.3f}" '
            f'width="{width}" height="{height}" />'
            "</mask>"
        )
        return mask_id

    def render_image(self, element_type: str, item: dict[str, Any], resource: dict[str, Any]) -> str:
        if not item.get("objectData", {}).get("visible", False):
            return ""
        path = self.resource_path(resource)
        if not path:
            return self.render_placeholder(element_type, item)
        w, h = png_size(path)
        transform = transform_attr(item["objectData"])
        return (
            f'<g transform="{transform}">'
            f'<image href="{svg_href(path)}" x="{-w / 2:.3f}" y="{-h / 2:.3f}" '
            f'width="{w}" height="{h}" />'
            "</g>"
        )

    def render_shape(self, item: dict[str, Any]) -> str:
        if not item.get("objectData", {}).get("visible", False):
            return ""
        resource = self.shapes.get(int(item.get("id", 0)), {})
        path = self.resource_path(resource, "shape")
        if not path:
            return self.render_placeholder("shape", item)

        w, h = png_size(path)
        mask_id = self.mask_for_image(path, w, h)
        transform = transform_attr(item["objectData"])
        fill = self.colors.get(int(item.get("colorId", 0)), "#ffffff")
        outline = self.colors.get(int(item.get("outlineColorId", 0)), fill)
        alpha = max(0.0, min(1.0, float(item.get("alpha", 1.0))))
        outline_alpha = max(0.0, min(1.0, float(item.get("outlineAlpha", 0.0))))
        outline_size = max(0.0, float(item.get("outlineSize", 0.0)))
        outline_scale = 1.0 + outline_size * SHAPE_OUTLINE_SCALE_FACTOR

        parts = [f'<g transform="{transform}">']
        if outline_alpha > 0 and outline_size > 0:
            parts.append(
                f'<g transform="scale({outline_scale:.5f})" opacity="{outline_alpha:.4f}">'
                f'<rect x="{-w / 2:.3f}" y="{-h / 2:.3f}" width="{w}" height="{h}" '
                f'fill="{outline}" mask="url(#{mask_id})" />'
                "</g>"
            )
        if alpha > 0:
            parts.append(
                f'<rect x="{-w / 2:.3f}" y="{-h / 2:.3f}" width="{w}" height="{h}" '
                f'fill="{fill}" opacity="{alpha:.4f}" mask="url(#{mask_id})" />'
            )
        parts.append("</g>")
        return "".join(parts)

    def render_text(self, item: dict[str, Any]) -> str:
        if not item.get("objectData", {}).get("visible", False):
            return ""
        raw_text = str(item.get("text", ""))
        if not raw_text.strip():
            return ""
        font_id = int(item.get("fontId", 0))
        font_name = self.text_fonts.get(font_id, "sans-serif") or "sans-serif"
        base_color = self.colors.get(int(item.get("colorId", 0)), "#444466")
        base_size = float(item.get("size", 24.0))
        outline_color = self.colors.get(int(item.get("outlineColorId", 0)), base_color)
        outline_size = max(0.0, float(item.get("outlineSize", 0.0))) * base_size * TMP_OUTLINE_FACTOR
        line_spacing = float(item.get("lineSpacing", 0.0))
        anchor = tmp_anchor(int(item.get("type", 513)))
        runs = parse_tmp_text(
            raw_text,
            TextStyle(
                color=base_color,
                alpha=1.0,
                size=base_size,
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
            ),
        )
        lines = split_runs_by_line(runs)
        transform = transform_attr(item["objectData"])
        parts = [f'<g transform="{transform}">']
        total_height = sum(
            (
                max(
                    ((r.style.line_height if r.style.line_height is not None else r.style.size) for r in line),
                    default=base_size,
                )
                * (1.0 + line_spacing)
            )
            for line in lines
        )
        y = -total_height / 2
        for line in lines:
            line_size = max(
                ((r.style.line_height if r.style.line_height is not None else r.style.size) for r in line),
                default=base_size,
            )
            baseline = y + line_size / 2
            x_cursor = 0.0
            for run in line:
                text = svg_escape(run.text)
                style = run.style
                if self.tmp_scale_mode == "uniform":
                    effective_size = style.size * style.scale_x
                    scale_transform = ""
                else:
                    effective_size = style.size
                    scale_transform = (
                        f" translate({x_cursor:.3f} {baseline - style.voffset:.3f})"
                        f" scale({style.scale_x:.6f} 1)"
                        f" translate({-x_cursor:.3f} {-baseline + style.voffset:.3f})"
                        if style.scale_x != 1.0
                        else ""
                    )
                stroke = ""
                if outline_size > 0:
                    stroke = (
                        f' stroke="{outline_color}" stroke-width="{outline_size:.3f}" '
                        'paint-order="stroke fill" stroke-linejoin="round"'
                    )
                rotate_attr = ""
                if style.rotate:
                    rotate_attr = f" rotate({-style.rotate:.5f} {x_cursor:.3f} {baseline - style.voffset:.3f})"
                transform_bits = (scale_transform + rotate_attr).strip()
                transform_attr_text = f' transform="{transform_bits}"' if transform_bits else ""
                decoration = []
                if style.underline:
                    decoration.append("underline")
                if style.strike:
                    decoration.append("line-through")
                decoration_attr = f' text-decoration="{" ".join(decoration)}"' if decoration else ""
                font_weight = ' font-weight="700"' if style.bold else ""
                font_style = ' font-style="italic"' if style.italic else ""
                anchor_attr = "middle" if self.text_anchor_mode == "center" else anchor
                parts.append(
                    f'<text x="{x_cursor:.3f}" y="{baseline - style.voffset:.3f}" text-anchor="{anchor_attr}" '
                    f'dominant-baseline="central" font-family="{svg_escape(font_name)}, sans-serif" '
                    f'font-size="{effective_size:.3f}" fill="{style.color}" fill-opacity="{style.alpha:.4f}"'
                    f"{font_weight}{font_style}{decoration_attr}{stroke}{transform_attr_text}>{text}</text>"
                )
                advance = style.mspace if style.mspace is not None else effective_size * 0.55 * style.scale_x
                x_cursor += len(run.text) * (advance + style.cspace)
            y += line_size * (1.0 + line_spacing)
        parts.append("</g>")
        return "".join(parts)

    def render_placeholder(self, element_type: str, item: dict[str, Any]) -> str:
        if not item.get("objectData", {}).get("visible", False):
            return ""
        transform = transform_attr(item["objectData"])
        label = svg_escape(f"{element_type}:{item.get('id', '?')}")
        return (
            f'<g transform="{transform}" opacity=".55">'
            '<rect x="-80" y="-24" width="160" height="48" rx="8" fill="#ffffff" stroke="#ff6688" />'
            f'<text text-anchor="middle" dominant-baseline="central" font-size="18" fill="#ff3366">{label}</text>'
            "</g>"
        )

    def render_layout(self, card: dict[str, Any]) -> str:
        layout = card["customProfileCard"]
        elements: list[tuple[int, str]] = []

        def add(layer_item: dict[str, Any], svg: str) -> None:
            if svg:
                elements.append((int(layer_item.get("objectData", {}).get("layer", 0)), svg))

        for item in layout.get("generalBackgrounds", []):
            add(item, self.render_image("general_background", item, self.general_bgs.get(int(item.get("id", 0)), {})))
        for item in layout.get("storyBackgrounds", []):
            add(item, self.render_image("story_background", item, self.story_bgs.get(int(item.get("id", 0)), {})))
        for item in layout.get("shapes", []):
            add(item, self.render_shape(item))
        for item in layout.get("texts", []):
            add(item, self.render_text(item))
        for item in layout.get("collections", []):
            add(item, self.render_image("collection", item, self.collections.get(int(item.get("id", 0)), {})))
        for item in layout.get("others", []):
            add(item, self.render_image("other", item, self.others.get(int(item.get("id", 0)), {})))
        elements.sort(key=lambda pair: pair[0])

        debug = ""
        if self.debug:
            debug = (
                '<line class="debug-axis" x1="0" y1="512" x2="2048" y2="512" />'
                '<line class="debug-axis" x1="1024" y1="0" x2="1024" y2="1024" />'
            )
        defs = "<defs>" + self.font_css() + "".join(self.defs) + "</defs>"
        return (
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{CANVAS_W}" height="{CANVAS_H}" '
            f'viewBox="0 0 {CANVAS_W} {CANVAS_H}">'
            f"{defs}"
            '<rect width="2048" height="1024" fill="#ffffff" />'
            f"{debug}" + "".join(svg for _, svg in elements) + "</svg>\n"
        )


def select_cards(
    profile: dict[str, Any], seq: int | None, card_id: int | None, all_cards: bool
) -> list[dict[str, Any]]:
    cards = list(profile.get("userCustomProfileCards", []))
    if all_cards:
        return sorted(cards, key=lambda c: int(c.get("seq", 0)))
    if card_id is not None:
        return [c for c in cards if int(c.get("customProfileCardId", 0)) == card_id]
    target_seq = seq or 1
    return [c for c in cards if int(c.get("seq", 0)) == target_seq]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--profile", type=Path, default=DEFAULT_PROFILE)
    parser.add_argument("--masterdata", type=Path, default=DEFAULT_MASTERDATA)
    parser.add_argument("--assets", type=Path, default=DEFAULT_ASSETS)
    parser.add_argument("--fonts", type=Path, default=DEFAULT_FONTS)
    parser.add_argument("--out", type=Path, default=Path(__file__).resolve().parent / "out")
    parser.add_argument("--seq", type=int)
    parser.add_argument("--card-id", type=int)
    parser.add_argument("--all", action="store_true")
    parser.add_argument("--debug", action="store_true")
    parser.add_argument("--text-anchor-mode", choices=["tmp", "center"], default="tmp")
    parser.add_argument("--tmp-scale-mode", choices=["x", "uniform"], default="x")
    args = parser.parse_args()

    args.out.mkdir(parents=True, exist_ok=True)
    profile = load_json(args.profile)
    cards = select_cards(profile, args.seq, args.card_id, args.all)
    if not cards:
        raise SystemExit("no matching custom profile card")
    for card in cards:
        renderer = Renderer(
            args.masterdata,
            args.assets,
            args.fonts,
            debug=args.debug,
            text_anchor_mode=args.text_anchor_mode,
            tmp_scale_mode=args.tmp_scale_mode,
        )
        svg = renderer.render_layout(card)
        seq = int(card.get("seq", 0))
        cid = int(card.get("customProfileCardId", 0))
        path = args.out / f"custom_profile_seq{seq:02d}_card{cid:02d}.svg"
        path.write_text(svg, encoding="utf-8")
        print(path)


if __name__ == "__main__":
    main()
