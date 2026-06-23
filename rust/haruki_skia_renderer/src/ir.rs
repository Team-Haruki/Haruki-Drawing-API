//! Render IR v2 — declarative scene graph consumed by the general interpreter.
//!
//! Python builds this tree; `interp.rs` renders it with Skia. See
//! `docs/rust-skia-renderer-migration.md` for the design and constraints.

use serde::Deserialize;

/// Top-level scene envelope. Supersets the v1 card IRs.
#[derive(Debug, Deserialize)]
pub struct Scene {
    pub version: u32,
    pub assets_base_dir: String,
    #[serde(default = "default_export_format")]
    pub export_format: String,
    #[serde(default = "default_jpg_quality")]
    pub jpg_quality: i32,
    pub fonts: FontsIr,
    pub canvas: CanvasIr,
    /// Optional flat background painted before the root tree (TriangleBg or cover image).
    #[serde(default)]
    pub background: Option<Node>,
    pub root: Node,
}

fn default_export_format() -> String {
    "png".to_string()
}

fn default_jpg_quality() -> i32 {
    90
}

#[derive(Debug, Deserialize)]
pub struct CanvasIr {
    pub width: i32,
    pub height: i32,
}

/// Font roles. `heavy`/`emoji` are optional and fall back to bold/regular.
#[derive(Debug, Deserialize)]
pub struct FontsIr {
    pub dir: String,
    pub default: String,
    pub bold: String,
    #[serde(default)]
    pub heavy: Option<String>,
    // Reserved for the Skia color-emoji fallback chain (migration doc §8); not yet
    // wired into the FontRegistry.
    #[serde(default)]
    #[allow(dead_code)]
    pub emoji: Option<String>,
}

/// RGBA, 0-255 per channel.
pub type Color4 = [u8; 4];

/// A point/size pair in the current group's local coordinate space.
pub type Vec2 = [f32; 2];

/// Fill for shapes and text. A JSON array is a solid color; an object is a gradient.
#[derive(Debug, Deserialize)]
#[serde(untagged)]
pub enum Fill {
    Solid(Color4),
    Gradient(GradientSpec),
}

// Some fields (`method`, the radial parameters) are accepted from the IR but not yet
// consumed by the MVP shader; they are part of the v2 contract for the full gradient pass.
#[derive(Debug, Deserialize)]
#[serde(tag = "kind")]
#[allow(dead_code)]
pub enum GradientSpec {
    #[serde(rename = "linear")]
    Linear {
        c1: Color4,
        c2: Color4,
        /// Endpoints in local coordinates.
        p1: Vec2,
        p2: Vec2,
        #[serde(default = "default_gradient_method")]
        method: String,
    },
    #[serde(rename = "radial")]
    Radial {
        /// c1 = edge, c2 = center (Painter's inverted convention, preserved).
        c1: Color4,
        c2: Color4,
        center: Vec2,
        radius_px: f32,
    },
}

fn default_gradient_method() -> String {
    "combine".to_string()
}

/// Optional clip carried by a Group.
#[derive(Debug, Deserialize)]
#[serde(tag = "kind")]
pub enum Clip {
    #[serde(rename = "rect")]
    Rect,
    #[serde(rename = "rrect")]
    RRect {
        radius: f32,
        #[serde(default = "all_corners")]
        corners: [bool; 4],
    },
}

fn all_corners() -> [bool; 4] {
    [true, true, true, true]
}

/// Horizontal text anchor relative to `pos.x`.
#[derive(Debug, Deserialize, Clone, Copy, PartialEq, Eq, Default)]
#[serde(rename_all = "lowercase")]
pub enum HAlign {
    #[default]
    Left,
    Center,
    Right,
}

/// Vertical baseline policy. `CjkTop` anchors the visual top of the line at `pos.y`
/// (Painter's `'哇'`-reference widget behaviour); `Ascender` is the raster-text default.
#[derive(Debug, Deserialize, Clone, Copy, PartialEq, Eq, Default)]
#[serde(rename_all = "snake_case")]
pub enum Baseline {
    #[default]
    CjkTop,
    Ascender,
}

#[derive(Debug, Deserialize, Clone, Copy, Default)]
#[serde(rename_all = "lowercase")]
pub enum FontRole {
    #[default]
    Default,
    Bold,
    Heavy,
}

#[derive(Debug, Deserialize)]
pub struct FontRef {
    #[serde(default)]
    pub role: FontRole,
    pub size: f32,
}

/// Image fit modes mirroring Painter resize/paste intents.
#[derive(Debug, Deserialize, Clone, Copy, PartialEq, Eq, Default)]
#[serde(rename_all = "lowercase")]
pub enum Fit {
    /// Scale (possibly non-uniformly) to exactly fill `size`.
    #[default]
    Stretch,
    /// Scale to cover `size`, center-crop overflow.
    Cover,
    /// Scale to fit inside `size` preserving aspect, centered.
    Contain,
    /// `size[0]` is the target width; height derives from aspect ratio.
    Width,
}

#[derive(Debug, Deserialize)]
#[serde(tag = "type")]
pub enum Node {
    Group(GroupNode),
    Rect(RectNode),
    RoundRect(RoundRectNode),
    PieSlice(PieSliceNode),
    Image(ImageNode),
    Text(TextNode),
    BlurGlass(BlurGlassNode),
    TriangleBg(TriangleBgNode),
    ImageBg(ImageBgNode),
    Watermark(WatermarkNode),
}

#[derive(Debug, Deserialize)]
pub struct GroupNode {
    #[serde(default)]
    pub offset: Vec2,
    /// Required when `clip` is set; otherwise informational.
    #[serde(default)]
    pub size: Vec2,
    #[serde(default)]
    pub clip: Option<Clip>,
    #[serde(default)]
    pub children: Vec<Node>,
}

#[derive(Debug, Deserialize)]
pub struct RectNode {
    pub pos: Vec2,
    pub size: Vec2,
    #[serde(default)]
    pub fill: Option<Fill>,
    #[serde(default)]
    pub stroke: Option<Color4>,
    #[serde(default = "default_stroke_width")]
    pub stroke_width: f32,
}

#[derive(Debug, Deserialize)]
pub struct RoundRectNode {
    pub pos: Vec2,
    pub size: Vec2,
    pub radius: f32,
    #[serde(default = "all_corners")]
    pub corners: [bool; 4],
    #[serde(default)]
    pub fill: Option<Fill>,
    #[serde(default)]
    pub stroke: Option<Color4>,
    #[serde(default = "default_stroke_width")]
    pub stroke_width: f32,
}

#[derive(Debug, Deserialize)]
pub struct PieSliceNode {
    pub pos: Vec2,
    pub size: Vec2,
    pub start_angle: f32,
    pub end_angle: f32,
    #[serde(default)]
    pub fill: Option<Fill>,
    #[serde(default)]
    pub stroke: Option<Color4>,
    #[serde(default = "default_stroke_width")]
    pub stroke_width: f32,
}

fn default_stroke_width() -> f32 {
    1.0
}

#[derive(Debug, Deserialize)]
pub struct ImageNode {
    pub pos: Vec2,
    /// Required for stretch/cover/contain; for `width` only `size[0]` is used.
    #[serde(default)]
    pub size: Vec2,
    pub path: String,
    #[serde(default)]
    pub fit: Fit,
    #[serde(default = "default_alpha")]
    pub alpha: f32,
}

fn default_alpha() -> f32 {
    1.0
}

#[derive(Debug, Deserialize)]
pub struct TextNode {
    pub text: String,
    pub pos: Vec2,
    pub font: FontRef,
    #[serde(default)]
    pub align: HAlign,
    #[serde(default)]
    pub baseline: Baseline,
    pub fill: Color4,
}

#[derive(Debug, Deserialize)]
pub struct BlurGlassNode {
    pub pos: Vec2,
    pub size: Vec2,
    pub radius: f32,
    pub fill: Color4,
    #[serde(default = "default_shadow_alpha")]
    pub shadow_alpha: f32,
}

fn default_shadow_alpha() -> f32 {
    0.26
}

#[derive(Debug, Deserialize)]
pub struct TriangleBgNode {
    #[serde(default = "default_hour")]
    pub hour: f32,
}

fn default_hour() -> f32 {
    15.0
}

#[derive(Debug, Deserialize)]
pub struct ImageBgNode {
    pub path: String,
}

/// Pre-laid-out watermark lines (Python owns wrapping/auto-size).
#[derive(Debug, Deserialize)]
pub struct WatermarkNode {
    pub lines: Vec<WatermarkLine>,
    pub font: FontRef,
    pub fill: Color4,
}

#[derive(Debug, Deserialize)]
pub struct WatermarkLine {
    pub text: String,
    pub pos: Vec2,
    #[serde(default)]
    pub align: HAlign,
}

/// Validate an IR asset path (mirrors the Python-side `_validate_asset_path`).
/// The interpreter additionally re-checks via `resolve_asset_path`.
pub fn is_safe_asset_path(path: &str) -> bool {
    !path.is_empty() && !path.contains('\\') && !path.contains("..") && !path.starts_with('/')
}
