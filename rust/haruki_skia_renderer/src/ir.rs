//! Render IR v2 — declarative scene graph consumed by the general interpreter.
//!
//! Python builds this tree; `interp.rs` renders it with Skia. See
//! `docs/rust-skia-renderer-migration.md` for the design and constraints.

use std::collections::HashMap;

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
    /// Output scale: render at canvas size, then resize the final raster to
    /// (round(w*scale), round(h*scale)) — mirrors plot.py `Canvas.get_img(scale)`.
    #[serde(default = "default_scale")]
    pub scale: f32,
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

fn default_scale() -> f32 {
    1.0
}

#[derive(Debug, Deserialize)]
pub struct CanvasIr {
    pub width: i32,
    pub height: i32,
}

/// Font roles. `heavy`/`emoji` are optional and fall back to bold/regular. `extra` registers
/// arbitrary named fonts (name -> font file) addressable via `FontRef.name`.
#[derive(Debug, Deserialize)]
pub struct FontsIr {
    pub dir: String,
    pub default: String,
    pub bold: String,
    #[serde(default)]
    pub heavy: Option<String>,
    /// Color-emoji fallback typeface (opt-in).
    #[serde(default)]
    pub emoji: Option<String>,
    #[serde(default)]
    pub extra: HashMap<String, String>,
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

/// A multi-stop entry: `color` at relative position `pos` (0..1 along the gradient).
#[derive(Debug, Deserialize, Clone, Copy)]
pub struct GradientStop {
    pub color: Color4,
    #[serde(default)]
    pub pos: f32,
}

#[derive(Debug, Deserialize)]
#[serde(tag = "kind")]
pub enum GradientSpec {
    #[serde(rename = "linear")]
    Linear {
        /// Simple 2-stop endpoints (c1@0, c2@1). Ignored when `stops` is non-empty.
        #[serde(default)]
        c1: Option<Color4>,
        #[serde(default)]
        c2: Option<Color4>,
        /// Optional N-stop list (>= 2 entries) overriding c1/c2.
        #[serde(default)]
        stops: Vec<GradientStop>,
        /// Endpoints in local coordinates.
        p1: Vec2,
        p2: Vec2,
        /// `combine` = standard vector projection (Skia native). `separate` is Painter's
        /// nonstandard per-axis average; it is approximated by `combine` (differs only for
        /// diagonal gradients). Accepted for contract completeness but not separately rendered.
        #[serde(default = "default_gradient_method")]
        #[allow(dead_code)]
        method: String,
    },
    #[serde(rename = "radial")]
    Radial {
        /// c1 = edge, c2 = center (Painter's inverted convention, preserved). Ignored when
        /// `stops` is non-empty (stop 0 = center, stop 1 = edge).
        #[serde(default)]
        c1: Option<Color4>,
        #[serde(default)]
        c2: Option<Color4>,
        #[serde(default)]
        stops: Vec<GradientStop>,
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
    /// `pos.y` is the text baseline directly (raster-text / `draw_text_blob` default).
    Alphabetic,
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
    /// Optional arbitrary font name registered in `FontsIr.extra`; overrides `role`.
    #[serde(default)]
    pub name: Option<String>,
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
    /// Center-crop to `size` WITHOUT scaling (1:1 pixels, mirrors Painter's
    /// `center_crop_by_aspect_ratio`). If the source is smaller, it is centered.
    Crop,
}

/// How an image tint color is combined with the image pixels.
#[derive(Debug, Deserialize, Clone, Copy, PartialEq, Eq, Default)]
#[serde(rename_all = "lowercase")]
pub enum TintMode {
    /// Component-wise multiply (Painter `multiply_image_by_color`).
    #[default]
    Multiply,
    /// Alpha-weighted lerp toward `color` by `strength` (Painter `mix_image_by_color`).
    Mix,
}

#[derive(Debug, Deserialize, Clone, Copy)]
pub struct Tint {
    pub color: Color4,
    #[serde(default)]
    pub mode: TintMode,
    /// Mix weight 0..1 (only used by `mix`).
    #[serde(default = "default_tint_strength")]
    pub strength: f32,
}

fn default_tint_strength() -> f32 {
    1.0
}

/// A drop shadow derived from the image's own alpha silhouette (Painter `paste(use_shadow=True)`).
#[derive(Debug, Deserialize, Clone, Copy)]
pub struct ImageShadow {
    #[serde(default = "default_shadow_node_alpha")]
    pub alpha: f32,
    #[serde(default = "default_shadow_offset")]
    pub offset: Vec2,
    #[serde(default = "default_shadow_sigma")]
    pub sigma: f32,
    #[serde(default = "default_shadow_color")]
    pub color: Color4,
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
    Shadow(ShadowNode),
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
    /// A JSON array is a solid stroke color; an object is a gradient stroke.
    #[serde(default)]
    pub stroke: Option<Fill>,
    #[serde(default = "default_stroke_width")]
    pub stroke_width: f32,
}

#[derive(Debug, Deserialize)]
pub struct RoundRectNode {
    pub pos: Vec2,
    pub size: Vec2,
    pub radius: f32,
    /// Optional per-corner radii (UL, UR, LR, LL); overrides `radius` when present.
    #[serde(default)]
    pub corner_radii: Option<[f32; 4]>,
    #[serde(default = "all_corners")]
    pub corners: [bool; 4],
    #[serde(default)]
    pub fill: Option<Fill>,
    #[serde(default)]
    pub stroke: Option<Fill>,
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
    pub stroke: Option<Fill>,
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
    /// Anchor of `pos` within the drawn rect: [0,0]=top-left (default), [1,1]=bottom-right,
    /// [0.5,0.5]=center. Lets `pos` align the rect's corner/center (mirrors Painter's
    /// content_align), which matters for `width` fit where the height is computed.
    #[serde(default)]
    pub anchor: Vec2,
    /// Optional color tint applied to the image pixels.
    #[serde(default)]
    pub tint: Option<Tint>,
    /// Optional drop shadow from the image's alpha silhouette, drawn behind the image.
    #[serde(default)]
    pub shadow: Option<ImageShadow>,
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
    /// A JSON array is a solid color; an object is a gradient (gradient text fill).
    pub fill: Fill,
    /// Optional outline drawn under the fill.
    #[serde(default)]
    pub stroke: Option<TextStroke>,
    /// Extra spacing (px) between glyphs.
    #[serde(default)]
    pub letter_spacing: f32,
    /// Optional background-adaptive contrast color (overrides `fill` with a solid color
    /// chosen from the average luminance behind the text — Painter `AdaptiveTextColor`).
    #[serde(default)]
    pub adaptive: Option<AdaptiveColor>,
}

#[derive(Debug, Deserialize)]
pub struct TextStroke {
    pub color: Color4,
    #[serde(default = "default_stroke_width")]
    pub width: f32,
}

/// Average-luminance adaptive text color (MVP: per-text-box average, not per-pixel).
#[derive(Debug, Deserialize)]
pub struct AdaptiveColor {
    #[serde(default = "default_adaptive_light")]
    pub light: Color4,
    #[serde(default = "default_adaptive_dark")]
    pub dark: Color4,
    #[serde(default = "default_adaptive_threshold")]
    pub threshold: f32,
}

fn default_adaptive_light() -> Color4 {
    [255, 255, 255, 255]
}

fn default_adaptive_dark() -> Color4 {
    [0, 0, 0, 255]
}

fn default_adaptive_threshold() -> f32 {
    0.4
}

/// Soft drop shadow for a rounded rect (mirrors Painter's thumbnail contact shadow).
#[derive(Debug, Deserialize)]
pub struct ShadowNode {
    pub pos: Vec2,
    pub size: Vec2,
    pub radius: f32,
    #[serde(default = "default_shadow_node_alpha")]
    pub alpha: f32,
    #[serde(default = "default_shadow_offset")]
    pub offset: Vec2,
    #[serde(default = "default_shadow_sigma")]
    pub sigma: f32,
    #[serde(default = "default_shadow_color")]
    pub color: Color4,
}

fn default_shadow_node_alpha() -> f32 {
    0.35
}

fn default_shadow_offset() -> Vec2 {
    [2.0, 4.0]
}

fn default_shadow_sigma() -> f32 {
    2.5
}

fn default_shadow_color() -> Color4 {
    [0, 0, 0, 255]
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
    /// `true` = time-of-day pink palette (uses `hour`); `false` = custom `main_hue` palette.
    #[serde(default = "default_true")]
    pub time_color: bool,
    /// Base hue (0..1) for the non-time palette.
    #[serde(default)]
    pub main_hue: f32,
    /// 0 = size scales with canvas (default); 1 = fixed triangle size, density scales instead.
    #[serde(default)]
    pub size_fixed_rate: f32,
}

fn default_hour() -> f32 {
    15.0
}

fn default_true() -> bool {
    true
}

/// How an `ImageBg` is placed on the canvas (mirrors plot.py ImageBg modes).
#[derive(Debug, Deserialize, Clone, Copy, Default)]
#[serde(rename_all = "lowercase")]
pub enum BgMode {
    /// Scale to cover, aligned (default — the legacy full-canvas cover).
    #[default]
    Fit,
    /// Stretch to exactly fill the canvas.
    Fill,
    /// Natural size, aligned (no scaling).
    Fixed,
    /// Tile at natural size.
    Repeat,
}

#[derive(Debug, Deserialize)]
pub struct ImageBgNode {
    pub path: String,
    #[serde(default)]
    pub mode: BgMode,
    /// Alignment string, e.g. "c", "tl", "br" (h in {l,c,r}, v in {t,c,b}).
    #[serde(default = "default_bg_align")]
    pub align: String,
    /// Apply a GaussianBlur(3) (Painter ImageBg blur).
    #[serde(default)]
    pub blur: bool,
    /// Brightness fade 0..1 (multiplies RGB by 1-fade; Painter ImageBg fade).
    #[serde(default)]
    pub fade: f32,
}

fn default_bg_align() -> String {
    "c".to_string()
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
