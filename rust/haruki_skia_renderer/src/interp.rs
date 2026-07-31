//! General Render IR v2 interpreter.
//!
//! Renders a `Scene` (tree of `Node`s) with Skia. Coordinates are resolved to
//! absolute canvas space (the canvas matrix stays identity), so backdrop
//! snapshots used by `BlurGlass` line up with the drawing coordinate system.
//! Reuses infrastructure from `lib.rs` (`pub(crate)` items): image decode,
//! font loading, surface encode, blur glass, triangle background, cover image.

use std::collections::{HashMap, HashSet};
use std::path::PathBuf;
use std::sync::OnceLock;
use std::time::Instant;

#[cfg(not(test))]
use pyo3::buffer::PyBuffer;
use rayon::prelude::*;
use skia_safe::{
    AlphaType, BlendMode, BlurStyle, Canvas, ClipOp, Color, Color4f, ColorType, CubicResampler,
    Data, FilterMode, Font, IRect, Image, ImageInfo, MaskFilter, Matrix, MipmapMode, Paint,
    PaintStyle, Point, RRect, Rect, RoundOut, SamplingOptions, Shader, Surface, TextBlob, TileMode,
    Typeface, canvas::SrcRectConstraint, color_filters, gradient, image::CachingHint,
    image_filters, surfaces,
};

use crate::ir::*;
use crate::pillow_resize::{PillowResizeLimits, resize_rgba8_pillow_lanczos};
use crate::text_metrics::configured_text_font;
use crate::{
    AssetDescriptor, NativeMetrics, RasterCacheOutcome, RenderedImage, decode_asset_descriptor,
    decode_asset_rgba_unpremul, draw_blur_glass_rect, draw_sekai_triangle_background,
    draw_source_to_raster, encode_surface, load_asset_descriptor, load_typeface_checked,
    raster_cache_snapshot, rasterize_asset_cached,
};

#[cfg(not(test))]
pub(crate) type RawBufferOwner = PyBuffer<u8>;
#[cfg(test)]
pub(crate) struct RawBufferOwner;

/// Strong reference to the immutable Python object (a `bytes`, or a tuple holding one) whose
/// buffer a borrowed `Data` points into. Keeping it next to the `Data` is what makes the
/// zero-copy `mem:*` transport sound; see `crate::borrowed_data`.
#[cfg(not(test))]
pub(crate) type BytesOwner = pyo3::Py<pyo3::PyAny>;
#[cfg(test)]
pub(crate) struct BytesOwner;

static TEXT_COVERAGE_GAMMA: OnceLock<f32> = OnceLock::new();
static PROFILE_ENABLED: OnceLock<bool> = OnceLock::new();

fn profile_enabled() -> bool {
    *PROFILE_ENABLED.get_or_init(|| {
        std::env::var("HARUKI_SKIA_PROFILE")
            .ok()
            .is_some_and(|value| !matches!(value.as_str(), "" | "0" | "false" | "False"))
    })
}

fn default_text_coverage_gamma() -> f32 {
    if cfg!(target_os = "macos") {
        4.0
    } else if cfg!(target_os = "linux") {
        0.95
    } else {
        1.0
    }
}

#[allow(deprecated)]
fn apply_text_coverage_gamma(paint: &mut Paint) {
    let gamma = *TEXT_COVERAGE_GAMMA.get_or_init(|| {
        std::env::var("HARUKI_SKIA_TEXT_GAMMA")
            .ok()
            .and_then(|value| value.parse::<f32>().ok())
            .filter(|value| value.is_finite() && *value > 0.0)
            .unwrap_or_else(default_text_coverage_gamma)
    });
    if (gamma - 1.0).abs() > f32::EPSILON {
        paint.set_mask_filter(MaskFilter::gamma(gamma));
    }
}

/// Resolved typefaces for the scene's font roles.
struct FontRegistry {
    regular: Typeface,
    bold: Typeface,
    heavy: Typeface,
    /// Opt-in color-emoji typeface; emoji codepoints route here when present.
    emoji: Option<Typeface>,
    /// Arbitrary named fonts (FontsIr.extra), addressable via FontRef.name.
    extra: HashMap<String, Typeface>,
    /// How many of this scene's fonts could not be resolved and fell back to sans-serif.
    /// `load_typeface_checked` logs each distinct one at ERROR; this surfaces it per render.
    fallbacks: u64,
}

impl FontRegistry {
    fn build(fonts: &FontsIr) -> Self {
        let mut fallbacks = 0_u64;
        let mut load = |name: &str| {
            let (typeface, fell_back) = load_typeface_checked(&fonts.dir, name);
            fallbacks += u64::from(fell_back);
            typeface
        };
        let regular = load(&fonts.default);
        let bold = load(&fonts.bold);
        let heavy = match &fonts.heavy {
            Some(name) => load(name),
            None => bold.clone(),
        };
        // Only load an emoji typeface when explicitly configured (otherwise emoji codepoints
        // keep falling back to the main font, unchanged).
        let emoji = fonts.emoji.as_ref().map(|name| load(name));
        let extra: HashMap<String, Typeface> = fonts
            .extra
            .iter()
            .map(|(key, file)| (key.clone(), load(file)))
            .collect();
        Self {
            regular,
            bold,
            heavy,
            emoji,
            extra,
            fallbacks,
        }
    }

    fn resolve(&self, role: FontRole) -> &Typeface {
        match role {
            FontRole::Bold => &self.bold,
            FontRole::Heavy => &self.heavy,
            FontRole::Default => &self.regular,
        }
    }

    /// Resolve a FontRef: an arbitrary `name` (if registered) wins, else the role.
    fn resolve_ref(&self, font: &FontRef) -> &Typeface {
        if let Some(name) = &font.name
            && let Some(tf) = self.extra.get(name)
        {
            return tf;
        }
        self.resolve(font.role)
    }

    fn emoji_font(&self, size: f32) -> Option<Font> {
        self.emoji
            .as_ref()
            .map(|t| Font::from_typeface(t.clone(), size))
    }

    /// The emoji `Font`, built only when `text` actually contains an emoji codepoint.
    ///
    /// `routes_to_emoji` already returns false for every non-emoji char whether or not the emoji
    /// font exists, so for the overwhelming majority of strings — which contain no emoji at all —
    /// passing `None` here is indistinguishable from passing the real font. Building it eagerly
    /// meant allocating a Skia `Font` for every text node in the scene to route zero characters.
    fn emoji_font_for(&self, text: &str, size: f32) -> Option<Font> {
        if self.emoji.is_none() || !text.chars().any(is_emoji) {
            return None;
        }
        self.emoji_font(size)
    }
}

/// Whether a codepoint should route to the emoji font (emoji blocks + ZWJ/variation selectors).
fn is_emoji(ch: char) -> bool {
    let c = ch as u32;
    matches!(c,
        0x1F000..=0x1FAFF      // emoticons, transport, supplemental + extended-A, regional flags
        | 0x2600..=0x27BF      // misc symbols + dingbats
        | 0x2300..=0x23FF      // misc technical (⌚⌛⏰…)
        | 0x2B00..=0x2BFF      // misc symbols and arrows (⭐…)
        | 0xFE00..=0xFE0F      // variation selectors
        | 0x200D) // zero-width joiner (keep ZWJ sequences together)
}

/// Whether `ch` should actually draw with the emoji font: it must be in an emoji block AND
/// the emoji typeface must cover it. Twemoji's cmap lacks many misc symbols the blocks
/// include (\u{2661} \u{2606} \u{2605} \u{266a} \u{2713} ...) and its .notdef advance is 0,
/// so routing an uncovered char would render a zero-width hole and shift the rest of the
/// line left; those chars fall back to the main font (matching the Pillow path, where
/// emoji.emoji_count treats them as plain text). ZWJ/variation selectors stay with the
/// emoji run so sequences hold together.
fn routes_to_emoji(ch: char, emoji: Option<&Font>) -> bool {
    if !is_emoji(ch) {
        return false;
    }
    let Some(font) = emoji else { return false };
    let c = ch as u32;
    if c == 0x200D || (0xFE00..=0xFE0F).contains(&c) {
        return true;
    }
    font.unichar_to_glyph(ch as i32) != 0
}

/// Split text into consecutive (emoji-routed, run) segments for per-font drawing.
fn classify_runs(text: &str, emoji: Option<&Font>) -> Vec<(bool, String)> {
    let mut runs: Vec<(bool, String)> = Vec::new();
    for ch in text.chars() {
        let e = routes_to_emoji(ch, emoji);
        match runs.last_mut() {
            Some(last) if last.0 == e => last.1.push(ch),
            _ => runs.push((e, ch.to_string())),
        }
    }
    runs
}

fn run_font<'a>(is_emoji_run: bool, main: &'a Font, emoji: Option<&'a Font>) -> &'a Font {
    if is_emoji_run {
        emoji.unwrap_or(main)
    } else {
        main
    }
}

/// A runtime image shipped alongside the IR and referenced as "mem:<key>".
///
/// Both variants borrow their bytes from Python rather than copying them, so each keeps the
/// owner of that memory alive: `_buffer` for a read-only buffer exported by another extension,
/// `_owner` for an immutable `bytes` (or a tuple holding one). `Interp` declares `direct_images`
/// before `mem_images`, so the `Image`s built from these `Data`s are dropped first.
pub(crate) enum MemImage {
    /// PNG/JPEG bytes (decoded lazily via `Image::from_encoded`).
    Encoded {
        data: Data,
        _owner: Option<BytesOwner>,
    },
    /// Raw pixels — no encode/decode.
    Raw {
        width: i32,
        height: i32,
        row_bytes: usize,
        color_type: ColorType,
        alpha_type: AlphaType,
        data: Data,
        _buffer: Option<RawBufferOwner>,
        _owner: Option<BytesOwner>,
    },
}

struct SdfShapeSource {
    pixels: Vec<u8>,
    width: i32,
    height: i32,
}

struct UnityImageSource {
    image: Image,
    width: i32,
    height: i32,
}

/// Straight-alpha RGBA8 source retained for the explicit Pillow-Lanczos path.
///
/// A premultiplied Skia raster is not interchangeable here: Pillow quantizes its integer
/// premultiply before filtering and unpremultiplies the result afterwards.
struct PillowLanczosSource {
    pixels: Vec<u8>,
    width: i32,
    height: i32,
}

/// Interpreter state shared across the node tree (assets, fonts, canvas dims).
struct Interp {
    base: PathBuf,
    fonts: FontRegistry,
    /// Runtime images and the few direct-draw disk images (background/masks), per render.
    direct_images: HashMap<String, Image>,
    /// Small path/signature/dimension descriptors. Full-size decoded disk images are not held.
    asset_descriptors: HashMap<String, AssetDescriptor>,
    /// Runtime images referenced as "mem:<key>"; materialized lazily into `direct_images`.
    mem_images: HashMap<String, MemImage>,
    /// Straight-RGBA source pixels for asset-backed custom-profile shapes, decoded before any
    /// drawing so a missing/corrupt source fails the whole scene instead of dropping one layer.
    sdf_shape_sources: HashMap<String, SdfShapeSource>,
    /// Fully decoded straight-RGBA assets for UnityImage. Keeping these separate from the
    /// ordinary lazy image map makes corrupt pixel streams fail during scene preparation.
    unity_image_sources: HashMap<String, UnityImageSource>,
    /// Fully decoded straight-RGBA assets referenced by Image(sampling="pillow_lanczos").
    pillow_lanczos_sources: HashMap<String, PillowLanczosSource>,
    max_node_pixels: usize,
    max_scene_bytes: usize,
    /// Bytes retained for the lifetime of this render: the output surface, request-provided
    /// `mem:` payloads, and fully decoded strict/native assets.
    retained_native_asset_bytes: usize,
    active_native_runtime_bytes: usize,
    canvas_w: f32,
    canvas_h: f32,
    /// True while rendering inside a `Transform` subtree (non-identity CTM). Image draws must
    /// then sample exactly once through the CTM, so `draw_image_node` skips the pre-rasterized
    /// raster-cache path (which would resample its integral-size intermediate a second time).
    in_transform: bool,
    /// Ordinary images are fail-soft in the general IR, but a UnitySubscene is one logical
    /// custom-profile element. Dropping one of its assets would serve a partial native success.
    strict_asset_depth: usize,
    metrics: NativeMetrics,
}

impl Interp {
    fn load_mem(&mut self, path: &str) -> Option<Image> {
        if let Some(image) = self.direct_images.get(path) {
            return Some(image.clone());
        }
        let key = path.strip_prefix("mem:")?;
        let image = match self.mem_images.get(key)? {
            MemImage::Encoded { data, .. } => Image::from_encoded(data.clone())?,
            MemImage::Raw {
                width,
                height,
                row_bytes,
                color_type,
                alpha_type,
                data,
                ..
            } => {
                let info = ImageInfo::new((*width, *height), *color_type, *alpha_type, None);
                skia_safe::images::raster_from_data(&info, data.clone(), *row_bytes)?
            }
        };
        self.direct_images.insert(path.to_string(), image.clone());
        Some(image)
    }

    fn describe_asset(&mut self, path: &str) -> Result<(AssetDescriptor, Option<Image>), String> {
        if let Some(descriptor) = self.asset_descriptors.get(path) {
            return Ok((descriptor.clone(), None));
        }
        if !is_safe_asset_path(path) {
            return Err(format!("rejected unsafe asset path: {path}"));
        }
        let started = Instant::now();
        let loaded = load_asset_descriptor(&self.base, path);
        self.metrics.asset_load_elapsed += started.elapsed().as_secs_f64();
        let loaded = loaded?;
        self.asset_descriptors
            .insert(path.to_string(), loaded.descriptor.clone());
        Ok((loaded.descriptor, loaded.source))
    }

    fn load_direct(&mut self, path: &str) -> Option<Image> {
        if path.starts_with("mem:") {
            return self.load_mem(path);
        }
        if let Some(image) = self.direct_images.get(path) {
            return Some(image.clone());
        }
        let (descriptor, source) = match self.describe_asset(path) {
            Ok(loaded) => loaded,
            Err(err) => {
                eprintln!("haruki_skia_renderer: asset load failed, node skipped: {path} ({err})");
                return None;
            }
        };
        let started = Instant::now();
        let image = source
            .map(Ok)
            .unwrap_or_else(|| decode_asset_descriptor(&descriptor));
        self.metrics.asset_load_elapsed += started.elapsed().as_secs_f64();
        match image {
            Ok(image) => {
                self.direct_images.insert(path.to_string(), image.clone());
                Some(image)
            }
            Err(err) => {
                eprintln!(
                    "haruki_skia_renderer: asset decode failed, node skipped: {path} ({err})"
                );
                None
            }
        }
    }

    fn ensure_native_scene_bytes(&self, additional: usize, label: &str) -> Result<(), String> {
        let total = self
            .retained_native_asset_bytes
            .checked_add(self.active_native_runtime_bytes)
            .and_then(|value| value.checked_add(additional))
            .ok_or_else(|| format!("{label} byte count overflow"))?;
        if total > self.max_scene_bytes {
            return Err(format!(
                "{label} would retain or allocate {total} bytes; scene limit is {}",
                self.max_scene_bytes
            ));
        }
        Ok(())
    }

    fn available_native_scene_bytes(&self, label: &str) -> Result<usize, String> {
        let used = self
            .retained_native_asset_bytes
            .checked_add(self.active_native_runtime_bytes)
            .ok_or_else(|| format!("{label} byte count overflow"))?;
        self.max_scene_bytes.checked_sub(used).ok_or_else(|| {
            format!(
                "{label} has no bytes left in scene limit {}",
                self.max_scene_bytes
            )
        })
    }

    fn retain_native_asset_bytes(&mut self, byte_count: usize, label: &str) -> Result<(), String> {
        self.ensure_native_scene_bytes(byte_count, label)?;
        self.retained_native_asset_bytes = self
            .retained_native_asset_bytes
            .checked_add(byte_count)
            .ok_or_else(|| format!("{label} byte count overflow"))?;
        Ok(())
    }

    fn push_native_runtime_bytes(&mut self, byte_count: usize, label: &str) -> Result<(), String> {
        self.ensure_native_scene_bytes(byte_count, label)?;
        self.active_native_runtime_bytes = self
            .active_native_runtime_bytes
            .checked_add(byte_count)
            .ok_or_else(|| format!("{label} byte count overflow"))?;
        Ok(())
    }

    fn pop_native_runtime_bytes(&mut self, byte_count: usize) {
        debug_assert!(self.active_native_runtime_bytes >= byte_count);
        self.active_native_runtime_bytes = self
            .active_native_runtime_bytes
            .checked_sub(byte_count)
            .unwrap_or_default();
    }
}

fn rgba_byte_len(width: i32, height: i32, label: &str) -> Result<usize, String> {
    usize::try_from(width)
        .ok()
        .and_then(|width| {
            usize::try_from(height)
                .ok()
                .and_then(|height| width.checked_mul(height))
        })
        .and_then(|pixels| pixels.checked_mul(4))
        .ok_or_else(|| format!("{label} byte count overflow"))
}

fn mem_payload_bytes(mem_images: &HashMap<String, MemImage>) -> Result<usize, String> {
    mem_images.values().try_fold(0usize, |total, image| {
        let bytes = match image {
            MemImage::Encoded { data, .. } | MemImage::Raw { data, .. } => data.size(),
        };
        total
            .checked_add(bytes)
            .ok_or_else(|| "request mem image byte count overflow".to_string())
    })
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct SliceAxis {
    source: [i32; 4],
    target: [i32; 4],
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
struct SlicedImageGeometry {
    target_size: (i32, i32),
    x: SliceAxis,
    y: SliceAxis,
}

fn sliced_target_size(
    node: &SlicedImageNode,
    max_node_pixels: usize,
) -> Result<((i32, i32), usize), String> {
    if node
        .pos
        .iter()
        .chain(node.size.iter())
        .chain(std::iter::once(&node.alpha))
        .any(|value| !value.is_finite())
    {
        return Err("SlicedImage contains a non-finite scalar".to_string());
    }
    let dimension = |value: f32, label: &str| -> Result<i32, String> {
        let rounded = (value as f64).round_ties_even().max(1.0);
        if !rounded.is_finite() || rounded > i32::MAX as f64 {
            return Err(format!("SlicedImage target {label} overflows"));
        }
        Ok(rounded as i32)
    };
    let width = dimension(node.size[0], "width")?;
    let height = dimension(node.size[1], "height")?;
    let target_bytes =
        validate_strict_asset_size(width, height, max_node_pixels, "SlicedImage target")?;
    Ok(((width, height), target_bytes))
}

fn sliced_axis_geometry(
    source_extent: i32,
    target_extent: i32,
    leading_border: i32,
    trailing_border: i32,
) -> SliceAxis {
    let mut leading = leading_border.clamp(0, source_extent);
    let mut trailing = trailing_border.clamp(0, source_extent - leading);
    let border_sum = leading + trailing;
    if target_extent <= border_sum && border_sum > 0 {
        let scale = target_extent as f64 / border_sum as f64;
        leading = (leading as f64 * scale).floor() as i32;
        trailing = target_extent - leading;
    }
    SliceAxis {
        source: [0, leading, source_extent - trailing, source_extent],
        target: [0, leading, target_extent - trailing, target_extent],
    }
}

fn sliced_image_geometry(
    node: &SlicedImageNode,
    source_width: i32,
    source_height: i32,
    max_node_pixels: usize,
) -> Result<(SlicedImageGeometry, usize), String> {
    validate_strict_asset_size(
        source_width,
        source_height,
        max_node_pixels,
        "SlicedImage source",
    )?;
    let (target_size, target_bytes) = sliced_target_size(node, max_node_pixels)?;
    let [left, bottom, right, top] = node.border;
    Ok((
        SlicedImageGeometry {
            target_size,
            x: sliced_axis_geometry(source_width, target_size.0, left, right),
            y: sliced_axis_geometry(source_height, target_size.1, top, bottom),
        },
        target_bytes,
    ))
}

/// Return the maximum concurrent native scratch bytes below this node. This pass is pure: it
/// must run before strict child assets, SdfShape, UnityImage, or image prewarming can decode.
/// Sibling scratch surfaces render sequentially, while nested isolated subscene surfaces stay
/// live until their child snapshots have been placed.
fn preflight_isolated_subscene_peak(node: &Node, max_node_pixels: usize) -> Result<usize, String> {
    match node {
        Node::Group(group) => group.children.iter().try_fold(0usize, |peak, child| {
            Ok::<usize, String>(peak.max(preflight_isolated_subscene_peak(child, max_node_pixels)?))
        }),
        Node::Transform(transform) => transform.children.iter().try_fold(0usize, |peak, child| {
            Ok::<usize, String>(peak.max(preflight_isolated_subscene_peak(child, max_node_pixels)?))
        }),
        Node::UnitySubscene(subscene) => {
            let (width, height) = (subscene.size[0], subscene.size[1]);
            if width <= 0 || height <= 0 {
                return Err("UnitySubscene size must be positive".to_string());
            }
            let pixels = usize::try_from(width)
                .ok()
                .and_then(|width| {
                    usize::try_from(height)
                        .ok()
                        .and_then(|height| width.checked_mul(height))
                })
                .ok_or_else(|| "UnitySubscene pixel count overflow".to_string())?;
            if pixels > max_node_pixels {
                return Err(format!(
                    "UnitySubscene {width}x{height} ({pixels} pixels) exceeds limit \
                     {max_node_pixels}"
                ));
            }
            let surface_bytes = pixels
                .checked_mul(4)
                .ok_or_else(|| "UnitySubscene byte count overflow".to_string())?;
            let placement = UnityImageNode {
                path: "<unity-subscene>".to_string(),
                anchor: subscene.anchor,
                object_scale: subscene.object_scale,
                post_scale: subscene.post_scale,
                rotation: subscene.rotation,
                sampling: subscene.sampling,
                alpha: subscene.alpha,
            };
            validate_unity_image(&placement)?;
            let dimensions = unity_image_dimensions(&placement, width, height, max_node_pixels)?;
            let mut placement_bytes = 0usize;
            if dimensions.first != (width, height) {
                placement_bytes = rgba_byte_len(
                    dimensions.first.0,
                    dimensions.first.1,
                    "UnitySubscene object-scale raster",
                )?;
            }
            if dimensions.final_size != dimensions.first {
                placement_bytes = placement_bytes
                    .checked_add(rgba_byte_len(
                        dimensions.final_size.0,
                        dimensions.final_size.1,
                        "UnitySubscene post-scale raster",
                    )?)
                    .ok_or_else(|| "UnitySubscene resize byte count overflow".to_string())?;
            }
            let child_peak = subscene.children.iter().try_fold(0usize, |peak, child| {
                Ok::<usize, String>(
                    peak.max(preflight_isolated_subscene_peak(child, max_node_pixels)?),
                )
            })?;
            surface_bytes
                .checked_add(child_peak.max(placement_bytes))
                .ok_or_else(|| "nested UnitySubscene byte count overflow".to_string())
        }
        Node::RasterSubscene(subscene) => {
            let (width, height) = (subscene.natural_size[0], subscene.natural_size[1]);
            let surface_bytes = validate_strict_asset_size(
                width,
                height,
                max_node_pixels,
                "RasterSubscene natural surface",
            )?;
            let child_peak = subscene.children.iter().try_fold(0usize, |peak, child| {
                Ok::<usize, String>(
                    peak.max(preflight_isolated_subscene_peak(child, max_node_pixels)?),
                )
            })?;
            surface_bytes
                .checked_add(child_peak)
                .ok_or_else(|| "nested RasterSubscene byte count overflow".to_string())
        }
        Node::SlicedImage(image) => {
            sliced_target_size(image, max_node_pixels).map(|(_, bytes)| bytes)
        }
        _ => Ok(0),
    }
}

pub(crate) fn render_scene_inner(
    scene: &Scene,
    mem_images: HashMap<String, MemImage>,
) -> Result<RenderedImage, String> {
    let total_started = Instant::now();
    if scene.version != 2 {
        return Err(format!("unsupported scene IR version {}", scene.version));
    }
    if scene.canvas.width <= 0 || scene.canvas.height <= 0 {
        return Err("scene canvas must be positive".to_string());
    }
    let max_node_pixels = usize::try_from(scene.limits.max_node_pixels)
        .unwrap_or(usize::MAX)
        .max(1);
    let max_scene_bytes = usize::try_from(scene.limits.max_scene_bytes)
        .unwrap_or(usize::MAX)
        .max(1);
    // Validate the generic isolate-then-place contract for the whole tree before memory sizing
    // or any asset/font access. Scene.scale is a later whole-page resize, not an ancestor CTM.
    if let Some(background) = &scene.background {
        validate_raster_subscene_usage(background, false)?;
    }
    validate_raster_subscene_usage(&scene.root, false)?;

    let output_surface_bytes = rgba_byte_len(
        scene.canvas.width,
        scene.canvas.height,
        "scene output surface",
    )?;
    let scaled_output_bytes = if (scene.scale - 1.0).abs() > 1e-3 && scene.scale > 0.0 {
        let out_w = ((scene.canvas.width as f32) * scene.scale).floor() as i32;
        let out_h = ((scene.canvas.height as f32) * scene.scale).floor() as i32;
        if out_w > 0 && out_h > 0 {
            rgba_byte_len(out_w, out_h, "scaled scene output surface")?
        } else {
            0
        }
    } else {
        0
    };
    let request_mem_bytes = mem_payload_bytes(&mem_images)?;
    let retained_base_bytes = output_surface_bytes
        .checked_add(scaled_output_bytes)
        .and_then(|value| value.checked_add(request_mem_bytes))
        .ok_or_else(|| "scene retained base byte count overflow".to_string())?;
    let background_subscene_peak = scene
        .background
        .as_ref()
        .map(|node| preflight_isolated_subscene_peak(node, max_node_pixels))
        .transpose()?
        .unwrap_or_default();
    let subscene_runtime_peak = background_subscene_peak.max(preflight_isolated_subscene_peak(
        &scene.root,
        max_node_pixels,
    )?);
    let preflight_total = retained_base_bytes
        .checked_add(subscene_runtime_peak)
        .ok_or_else(|| "scene preflight byte count overflow".to_string())?;
    if preflight_total > max_scene_bytes {
        return Err(format!(
            "scene output, request buffers, optional scaled output, and isolated-subscene runtime require at least \
             {preflight_total} bytes; scene limit is {max_scene_bytes}"
        ));
    }
    // PasteLerp reads and rewrites destination pixels. Validate its deliberately narrow
    // identity-CTM/integral contract for the WHOLE tree before allocating a surface, loading
    // fonts/assets, or drawing anything; any unsupported emitter output must fail open to
    // Pillow, never leave a partially-rendered native scene.
    if let Some(background) = &scene.background {
        validate_paste_lerp_usage(background, (0.0, 0.0), false, false)?;
    }
    validate_paste_lerp_usage(&scene.root, (0.0, 0.0), false, false)?;

    let mut surface = surfaces::raster_n32_premul((scene.canvas.width, scene.canvas.height))
        .ok_or_else(|| "failed to create raster surface".to_string())?;
    let mut interp = Interp {
        base: PathBuf::from(&scene.assets_base_dir),
        fonts: FontRegistry::build(&scene.fonts),
        direct_images: HashMap::new(),
        asset_descriptors: HashMap::new(),
        mem_images,
        sdf_shape_sources: HashMap::new(),
        unity_image_sources: HashMap::new(),
        pillow_lanczos_sources: HashMap::new(),
        max_node_pixels,
        max_scene_bytes,
        retained_native_asset_bytes: retained_base_bytes,
        // Reserve the worst nested isolated-subscene peak while preparing retained assets. This
        // prevents source decodes from consuming the bytes the later offscreen surfaces need.
        active_native_runtime_bytes: subscene_runtime_peak,
        canvas_w: scene.canvas.width as f32,
        canvas_h: scene.canvas.height as f32,
        in_transform: false,
        strict_asset_depth: 0,
        metrics: NativeMetrics::default(),
    };
    interp.metrics.font_fallbacks = interp.fonts.fallbacks;
    interp.metrics.setup_elapsed = total_started.elapsed().as_secs_f64();

    // Device-bounds nodes inside Transform are rejected up front for the same reason as the
    // SdfQuad field validation below: an emitter regression must fail the WHOLE scene
    // (-> PyRuntimeError -> Python fail-open to Pillow), never render a silently wrong image.
    if let Some(background) = &scene.background {
        validate_transform_subtrees(background, false)?;
        validate_pillow_lanczos_usage(background, false)?;
    }
    validate_transform_subtrees(&scene.root, false)?;
    validate_pillow_lanczos_usage(&scene.root, false)?;

    // SdfQuad field references are validated up front so a bad one fails the WHOLE scene
    // (-> PyRuntimeError -> Python fail-open to Pillow) instead of silently skipping glyphs.
    if let Some(background) = &scene.background {
        validate_sdf_quad_fields(background, &interp.mem_images)?;
    }
    validate_sdf_quad_fields(&scene.root, &interp.mem_images)?;
    if let Some(background) = &scene.background {
        prepare_pillow_lanczos_sources(background, &mut interp)?;
        prepare_unity_subscene_assets(background, &mut interp)?;
        prepare_sdf_shape_sources(background, &mut interp)?;
        prepare_unity_image_assets(background, &mut interp)?;
    }
    prepare_pillow_lanczos_sources(&scene.root, &mut interp)?;
    prepare_unity_subscene_assets(&scene.root, &mut interp)?;
    prepare_sdf_shape_sources(&scene.root, &mut interp)?;
    prepare_unity_image_assets(&scene.root, &mut interp)?;
    // Asset preparation retained everything needed by strict subscenes. Replace the reservation
    // with actual push/pop accounting during drawing.
    interp.active_native_runtime_bytes = 0;

    prewarm_scene_images(scene, &mut interp);

    let draw_started = Instant::now();
    if let Some(background) = &scene.background {
        render_node(&mut surface, &mut interp, (0.0, 0.0), background)?;
    }
    render_node(&mut surface, &mut interp, (0.0, 0.0), &scene.root)?;
    interp.metrics.draw_elapsed = draw_started.elapsed().as_secs_f64();

    // Optional output scaling: render at 1x then resize the raster (linear), matching
    // plot.py Canvas.get_img(scale) which renders then BILINEAR-resizes the final image.
    let scale_started = Instant::now();
    let mut output_surface = None;
    if (scene.scale - 1.0).abs() > 1e-3 && scene.scale > 0.0 {
        // Truncate (floor for positives) to match plot.py's int(size * scale).
        let out_w = ((scene.canvas.width as f32) * scene.scale).floor() as i32;
        let out_h = ((scene.canvas.height as f32) * scene.scale).floor() as i32;
        if out_w > 0
            && out_h > 0
            && let Some(mut scaled) = surfaces::raster_n32_premul((out_w, out_h))
        {
            let image = surface.image_snapshot();
            let mut paint = Paint::default();
            paint.set_anti_alias(true);
            scaled.canvas().draw_image_rect_with_sampling_options(
                &image,
                None,
                Rect::from_xywh(0.0, 0.0, out_w as f32, out_h as f32),
                SamplingOptions::new(FilterMode::Linear, MipmapMode::None),
                &paint,
            );
            output_surface = Some(scaled);
        }
    }
    interp.metrics.scale_elapsed = scale_started.elapsed().as_secs_f64();
    let mut metrics = std::mem::take(&mut interp.metrics);
    let cache = raster_cache_snapshot();
    metrics.raster_cache_entries = cache.entries;
    metrics.raster_cache_bytes = cache.bytes;
    drop(interp);

    let mut rendered = encode_surface(
        output_surface.unwrap_or(surface),
        &scene.export_format,
        scene.jpg_quality,
    )?;
    metrics.total_elapsed = total_started.elapsed().as_secs_f64();
    rendered.metrics = metrics;
    if profile_enabled() {
        eprintln!(
            "haruki_skia_renderer.profile total={:.4}s setup={:.4}s prewarm={:.4}s draw={:.4}s scale={:.4}s encode={:.4}s asset_load={:.4}s raster_build={:.4}s raster_wait={:.4}s prewarm_req={} prewarm_hit={} prewarm_miss={} prewarm_coalesced={} cache_hit={} cache_miss={} cache_coalesced={} cache_bypass={} cache_entries={} cache_bytes={} zero_blur={} font_fallbacks={} sdf_quads={} sdf_quad_elapsed={:.4}s",
            rendered.metrics.total_elapsed,
            rendered.metrics.setup_elapsed,
            rendered.metrics.raster_prewarm_elapsed,
            rendered.metrics.draw_elapsed,
            rendered.metrics.scale_elapsed,
            rendered.encode_elapsed,
            rendered.metrics.asset_load_elapsed,
            rendered.metrics.raster_cache_build_elapsed,
            rendered.metrics.raster_cache_wait_elapsed,
            rendered.metrics.raster_prewarm_requests,
            rendered.metrics.raster_prewarm_hits,
            rendered.metrics.raster_prewarm_misses,
            rendered.metrics.raster_prewarm_coalesced,
            rendered.metrics.raster_cache_hits,
            rendered.metrics.raster_cache_misses,
            rendered.metrics.raster_cache_coalesced,
            rendered.metrics.raster_cache_bypasses,
            rendered.metrics.raster_cache_entries,
            rendered.metrics.raster_cache_bytes,
            rendered.metrics.zero_blur_fast_paths,
            rendered.metrics.font_fallbacks,
            rendered.metrics.sdf_quad_count,
            rendered.metrics.sdf_quad_elapsed,
        );
    }
    Ok(rendered)
}

fn render_node(
    surface: &mut Surface,
    interp: &mut Interp,
    off: (f32, f32),
    node: &Node,
) -> Result<(), String> {
    match node {
        Node::Group(group) => {
            let child_off = (off.0 + group.offset[0], off.1 + group.offset[1]);
            let mask_rect = group
                .mask
                .as_ref()
                .map(|_| Rect::from_xywh(child_off.0, child_off.1, group.size[0], group.size[1]));
            if let Some(rect) = mask_rect {
                let layer = skia_safe::canvas::SaveLayerRec::default().bounds(&rect);
                surface.canvas().save_layer(&layer);
            }
            let clipped = group.clip.is_some();
            if let Some(clip) = &group.clip {
                let canvas = surface.canvas();
                canvas.save();
                apply_clip(canvas, child_off, group.size, clip);
            }
            for child in &group.children {
                render_node(surface, interp, child_off, child)?;
            }
            if clipped {
                surface.canvas().restore();
            }
            if let Some(rect) = mask_rect {
                let mask_ref = group.mask.as_deref().unwrap_or_default();
                if let Some(mask) = interp.load_direct(mask_ref) {
                    let mut keep = Paint::default();
                    keep.set_anti_alias(true);
                    keep.set_blend_mode(BlendMode::DstIn);
                    surface.canvas().draw_image_rect(&mask, None, rect, &keep);
                } else {
                    if interp.strict_asset_depth > 0 {
                        surface.canvas().restore();
                        return Err(format!(
                            "UnitySubscene group mask was not prepared or decoded: {mask_ref}"
                        ));
                    }
                    eprintln!("haruki_skia_renderer: group mask missing, mask skipped: {mask_ref}");
                }
                surface.canvas().restore();
            }
        }
        Node::Transform(node) => {
            // Forward local->parent affine (see `TransformNode`). The enclosing group offset
            // applies BEFORE the matrix; children then resolve entirely through the CTM, so
            // they render with a zero offset (passing `off` down too would apply it twice).
            let canvas = surface.canvas();
            let save_count = canvas.save();
            canvas.translate((off.0, off.1));
            let m = node.matrix;
            canvas.concat(&Matrix::new_all(
                m[0], m[1], m[2], m[3], m[4], m[5], 0.0, 0.0, 1.0,
            ));
            let was_in_transform = interp.in_transform;
            interp.in_transform = true;
            for child in &node.children {
                render_node(surface, interp, (0.0, 0.0), child)?;
            }
            interp.in_transform = was_in_transform;
            surface.canvas().restore_to_count(save_count);
        }
        Node::Rect(rect) => render_rect(surface.canvas(), rect, off),
        Node::RoundRect(rr) => render_round_rect(surface.canvas(), rr, off),
        Node::PieSlice(pie) => render_pie_slice(surface.canvas(), pie, off),
        Node::Image(image) if image.blend == ImageBlend::PasteLerp => {
            interp.metrics.raster_cache_bypasses += 1;
            draw_paste_lerp_image(surface, interp, image, off)?
        }
        Node::Image(image) => draw_image_node(surface.canvas(), interp, image, off)?,
        Node::SlicedImage(image) => draw_sliced_image(surface.canvas(), interp, image, off)?,
        Node::UnityImage(image) => draw_unity_image(surface.canvas(), interp, image, off)?,
        Node::UnitySubscene(subscene) => draw_unity_subscene(surface, interp, subscene, off)?,
        Node::RasterSubscene(subscene) => draw_raster_subscene(surface, interp, subscene, off)?,
        Node::SelfImage(node) => {
            let dst = Rect::from_xywh(
                node.pos[0] + off.0,
                node.pos[1] + off.1,
                node.size[0],
                node.size[1],
            );
            let mut src = Rect::new(
                node.source_rect[0] + off.0,
                node.source_rect[1] + off.1,
                node.source_rect[2] + off.0,
                node.source_rect[3] + off.1,
            );
            let canvas_rect = Rect::from_xywh(0.0, 0.0, interp.canvas_w, interp.canvas_h);
            if src.intersect(canvas_rect) && !src.is_empty() && !dst.is_empty() {
                let ibounds: IRect = src.round_out();
                if let Some(snap) = surface.image_snapshot_with_bounds(ibounds) {
                    let src_local = Rect::from_xywh(
                        src.left - ibounds.left as f32,
                        src.top - ibounds.top as f32,
                        src.width(),
                        src.height(),
                    );
                    let mut paint = Paint::default();
                    paint.set_anti_alias(true);
                    surface.canvas().draw_image_rect_with_sampling_options(
                        &snap,
                        Some((&src_local, skia_safe::canvas::SrcRectConstraint::Strict)),
                        dst,
                        skia_image_sampling(node.sampling).ok_or_else(|| {
                            "SelfImage does not support pillow_lanczos sampling".to_string()
                        })?,
                        &paint,
                    );
                }
            }
        }
        Node::SdfQuad(quad) => {
            let started = Instant::now();
            draw_sdf_quad(surface, interp, quad, off);
            interp.metrics.sdf_quad_elapsed += started.elapsed().as_secs_f64();
            interp.metrics.sdf_quad_count += 1;
        }
        Node::SdfShape(shape) => draw_sdf_shape(surface, interp, shape, off)?,
        Node::Text(text) => {
            let abs = (text.pos[0] + off.0, text.pos[1] + off.1);
            // Adaptive color samples the backdrop (needs the surface), so resolve it here and
            // pass a solid fill down; otherwise use the node's own fill (solid or gradient).
            let adaptive_fill;
            let fill: &Fill = if let Some(ad) = &text.adaptive {
                if ad.pixelwise {
                    // Per-pixel light/dark selection needs its own masked draw path.
                    draw_pixelwise_adaptive_text(surface, &interp.fonts, text, abs, off, ad);
                    return Ok(());
                }
                let color = resolve_adaptive_color(surface, &interp.fonts, text, abs, ad);
                adaptive_fill = Fill::Solid(color);
                &adaptive_fill
            } else {
                &text.fill
            };
            draw_styled_text(surface.canvas(), &interp.fonts, text, abs, off, fill);
        }
        Node::Shadow(shadow) => render_shadow(surface.canvas(), shadow, off),
        Node::BlurGlass(glass) => {
            let rect = Rect::from_xywh(
                glass.pos[0] + off.0,
                glass.pos[1] + off.1,
                glass.size[0],
                glass.size[1],
            );
            // Zero blur is a normal translucent panel. Avoid snapshotting the backdrop and
            // allocating two temporary surfaces for the old near-zero sigma filter.
            let backdrop = if glass.blur > 0.01 {
                let mut bounds = rect.with_outset((12.0, 12.0));
                let canvas_rect = Rect::from_xywh(0.0, 0.0, interp.canvas_w, interp.canvas_h);
                if bounds.intersect(canvas_rect) {
                    let ibounds: IRect = bounds.round_out();
                    surface
                        .image_snapshot_with_bounds(ibounds)
                        .map(|img| (img, (ibounds.left as f32, ibounds.top as f32)))
                } else {
                    None
                }
            } else {
                interp.metrics.zero_blur_fast_paths += 1;
                None
            };
            // Panel tint paint (solid or gradient shader), positioned in absolute coords like
            // every other fill so a gradient lands identically to a RoundRect of the same fill.
            let panel_paint = fill_paint(&glass.fill, off);
            let canvas = surface.canvas();
            draw_blur_glass_rect(
                canvas,
                backdrop.as_ref().map(|(img, origin)| (img, *origin)),
                rect,
                glass.radius,
                &panel_paint,
                glass.shadow_alpha,
                glass.blur,
                glass.corners,
                glass.shadow_width,
            );
        }
        Node::TriangleBg(bg) => {
            draw_sekai_triangle_background(
                surface.canvas(),
                interp.canvas_w,
                interp.canvas_h,
                bg.hour,
                bg.time_color,
                bg.main_hue,
                &bg.tris,
            );
        }
        Node::ImageBg(bg) => {
            if let Some(decoded) = interp.load_direct(&bg.path) {
                draw_image_bg(
                    surface.canvas(),
                    &decoded,
                    interp.canvas_w,
                    interp.canvas_h,
                    bg,
                );
            } else if interp.strict_asset_depth > 0 {
                return Err(format!(
                    "UnitySubscene ImageBg was not prepared or decoded: {}",
                    bg.path
                ));
            }
        }
        Node::Watermark(watermark) => {
            let canvas = surface.canvas();
            let font = configured_text_font(
                interp.fonts.resolve_ref(&watermark.font).clone(),
                watermark.font.size,
            );
            let emoji = interp.fonts.emoji_font(watermark.font.size);
            let emoji_ref = emoji.as_ref();
            let mut paint = Paint::default();
            paint.set_anti_alias(true);
            paint.set_color(color_of(watermark.fill));
            apply_text_coverage_gamma(&mut paint);
            for line in &watermark.lines {
                let abs = (line.pos[0] + off.0, line.pos[1] + off.1);
                let (x, y) = text_layout(
                    &font,
                    emoji_ref,
                    &line.text,
                    abs,
                    line.align,
                    Baseline::CjkTop,
                    0.0,
                );
                draw_text_core(canvas, &font, emoji_ref, &line.text, x, y, 0.0, &paint);
            }
        }
    }
    Ok(())
}

fn color_of(c: Color4) -> Color {
    Color::from_argb(c[3], c[0], c[1], c[2])
}

fn skia_image_sampling(mode: ImageSampling) -> Option<SamplingOptions> {
    // Bilinear + mipmaps. For mild downscales (thumbnails ~1.3x) this stays at the base
    // level and matches Pillow's soft BILINEAR character; for large downscales (skill icon
    // ~3x) the mipmaps area-average so it doesn't alias the way plain bilinear does.
    match mode {
        ImageSampling::Nearest => Some(SamplingOptions::default()),
        ImageSampling::Linear => Some(SamplingOptions::new(FilterMode::Linear, MipmapMode::None)),
        ImageSampling::Cubic => Some(CubicResampler::mitchell().into()),
        ImageSampling::CatmullRom => Some(CubicResampler::catmull_rom().into()),
        ImageSampling::PillowLanczos => None,
        ImageSampling::LinearMipmap => {
            Some(SamplingOptions::new(FilterMode::Linear, MipmapMode::Linear))
        }
    }
}

/// Build a [Point; 4] of per-corner radii (UL, UR, LR, LL); disabled corners are 0.
fn corner_radii(radius: f32, corners: &[bool; 4]) -> [Point; 4] {
    let r = radius.max(0.0);
    let pick = |on: bool| {
        if on {
            Point::new(r, r)
        } else {
            Point::new(0.0, 0.0)
        }
    };
    [
        pick(corners[0]),
        pick(corners[1]),
        pick(corners[2]),
        pick(corners[3]),
    ]
}

fn apply_clip(canvas: &Canvas, off: (f32, f32), size: Vec2, clip: &Clip) {
    let rect = Rect::from_xywh(off.0, off.1, size[0], size[1]);
    match clip {
        Clip::Rect => {
            canvas.clip_rect(rect, ClipOp::Intersect, true);
        }
        Clip::RRect { radius, corners } => {
            let radii = corner_radii(*radius, corners);
            canvas.clip_rrect(RRect::new_rect_radii(rect, &radii), ClipOp::Intersect, true);
        }
    }
}

/// Resolve a gradient spec to (colors, positions) where positions are strictly increasing.
/// `fallback` supplies the 2 endpoint colors when `stops` has fewer than 2 entries.
fn resolve_gradient_stops(
    stops: &[GradientStop],
    fallback: [Color4; 2],
) -> (Vec<Color4f>, Vec<f32>) {
    if stops.len() >= 2 {
        let mut sorted = stops.to_vec();
        sorted.sort_by(|a, b| {
            a.pos
                .partial_cmp(&b.pos)
                .unwrap_or(std::cmp::Ordering::Equal)
        });
        let mut positions = Vec::with_capacity(sorted.len());
        let mut last = -1.0_f32;
        for st in &sorted {
            let mut p = st.pos.clamp(0.0, 1.0);
            if p <= last {
                p = (last + 1e-4).min(1.0);
            }
            last = p;
            positions.push(p);
        }
        let colors = sorted.iter().map(|st| color_of(st.color).into()).collect();
        (colors, positions)
    } else {
        (
            vec![color_of(fallback[0]).into(), color_of(fallback[1]).into()],
            vec![0.0, 1.0],
        )
    }
}

/// Painter's `method="separate"` gradient field is the average of the per-axis normalized
/// offsets: t(p) = mean over axes with delta != 0 of (p_axis - p1_axis) / delta_axis. That is
/// still an affine scalar field, so it renders as a plain linear gradient along its own
/// direction: t(p) = g . (p - p1) with g = (1/(n*dx), 1/(n*dy)) (dropped axes contribute 0),
/// i.e. endpoints p1 -> p1 + g / |g|^2 (painter.py:496-503).
fn separate_endpoints(p1: [f32; 2], p2: [f32; 2]) -> ([f32; 2], [f32; 2]) {
    let dx = p2[0] - p1[0];
    let dy = p2[1] - p1[1];
    let n = (dx != 0.0) as u32 + (dy != 0.0) as u32;
    if n == 0 {
        return (p1, p2); // degenerate either way
    }
    let gx = if dx != 0.0 {
        1.0 / (n as f32 * dx)
    } else {
        0.0
    };
    let gy = if dy != 0.0 {
        1.0 / (n as f32 * dy)
    } else {
        0.0
    };
    let len_sq = gx * gx + gy * gy;
    ([p1[0], p1[1]], [p1[0] + gx / len_sq, p1[1] + gy / len_sq])
}

fn gradient_shader(spec: &GradientSpec, off: (f32, f32)) -> Option<Shader> {
    match spec {
        GradientSpec::Linear {
            c1,
            c2,
            stops,
            p1,
            p2,
            method,
        } => {
            let fallback = [c1.unwrap_or([0, 0, 0, 255]), c2.unwrap_or([0, 0, 0, 255])];
            let (colors, positions) = resolve_gradient_stops(stops, fallback);
            let grad_colors =
                gradient::Colors::new(&colors, Some(&positions), TileMode::Clamp, None);
            let grad = gradient::Gradient::new(grad_colors, gradient::Interpolation::default());
            let (q1, q2) = if method == "separate" {
                separate_endpoints(*p1, *p2)
            } else {
                (*p1, *p2)
            };
            gradient::shaders::linear_gradient(
                (
                    Point::new(q1[0] + off.0, q1[1] + off.1),
                    Point::new(q2[0] + off.0, q2[1] + off.1),
                ),
                &grad,
                None,
            )
        }
        GradientSpec::Radial {
            c1,
            c2,
            stops,
            center,
            radius_px,
        } => {
            // Painter convention: stop 0 = center (c2), stop 1 = edge (c1).
            let fallback = [c2.unwrap_or([0, 0, 0, 255]), c1.unwrap_or([0, 0, 0, 255])];
            let (colors, positions) = resolve_gradient_stops(stops, fallback);
            let grad_colors =
                gradient::Colors::new(&colors, Some(&positions), TileMode::Clamp, None);
            let grad = gradient::Gradient::new(grad_colors, gradient::Interpolation::default());
            gradient::shaders::radial_gradient(
                (
                    Point::new(center[0] + off.0, center[1] + off.1),
                    radius_px.max(0.01),
                ),
                &grad,
                None,
            )
        }
    }
}

/// Fallback solid color when a gradient shader can't be built.
fn gradient_fallback_color(spec: &GradientSpec) -> Color {
    match spec {
        GradientSpec::Linear { c2, stops, .. } => stops
            .last()
            .map(|s| color_of(s.color))
            .unwrap_or_else(|| color_of(c2.unwrap_or([0, 0, 0, 255]))),
        GradientSpec::Radial { c2, stops, .. } => stops
            .first()
            .map(|s| color_of(s.color))
            .unwrap_or_else(|| color_of(c2.unwrap_or([0, 0, 0, 255]))),
    }
}

/// Configure a paint's color or shader from a fill.
fn apply_fill(paint: &mut Paint, fill: &Fill, off: (f32, f32)) {
    match fill {
        Fill::Solid(c) => {
            paint.set_color(color_of(*c));
        }
        Fill::Gradient(spec) => match gradient_shader(spec, off) {
            Some(shader) => {
                paint.set_shader(shader);
            }
            None => {
                paint.set_color(gradient_fallback_color(spec));
            }
        },
    }
}

/// A paint pre-configured with the node's fill (solid or gradient shader).
fn fill_paint(fill: &Fill, off: (f32, f32)) -> Paint {
    let mut paint = Paint::default();
    paint.set_anti_alias(true);
    paint.set_style(PaintStyle::Fill);
    apply_fill(&mut paint, fill, off);
    paint
}

/// A stroke paint; `stroke` may be a solid color or a gradient.
fn stroke_paint(stroke: &Fill, width: f32, off: (f32, f32)) -> Paint {
    let mut paint = Paint::default();
    paint.set_anti_alias(true);
    paint.set_style(PaintStyle::Stroke);
    paint.set_stroke_width(width);
    apply_fill(&mut paint, stroke, off);
    paint
}

fn apply_rect_blend(paint: &mut Paint, blend: ImageBlend) {
    if blend == ImageBlend::Src {
        paint.set_blend_mode(BlendMode::Src);
        // Rect coordinates model Pillow's integer ImageDraw writes. Disabling AA prevents a
        // Src edge from partially replacing pixels outside the requested rectangle.
        paint.set_anti_alias(false);
    }
}

fn render_rect(canvas: &Canvas, node: &RectNode, off: (f32, f32)) {
    let rect = Rect::from_xywh(
        node.pos[0] + off.0,
        node.pos[1] + off.1,
        node.size[0],
        node.size[1],
    );
    if let Some(fill) = &node.fill {
        let mut paint = fill_paint(fill, off);
        apply_rect_blend(&mut paint, node.blend);
        canvas.draw_rect(rect, &paint);
    }
    if let Some(stroke) = &node.stroke {
        let mut paint = stroke_paint(stroke, node.stroke_width, off);
        apply_rect_blend(&mut paint, node.blend);
        canvas.draw_rect(rect, &paint);
    }
}

fn render_round_rect(canvas: &Canvas, node: &RoundRectNode, off: (f32, f32)) {
    let rect = Rect::from_xywh(
        node.pos[0] + off.0,
        node.pos[1] + off.1,
        node.size[0],
        node.size[1],
    );
    // Per-corner distinct radii (UL, UR, LR, LL) override the uniform radius + toggle.
    let radii = match node.corner_radii {
        Some(r) => [
            Point::new(r[0].max(0.0), r[0].max(0.0)),
            Point::new(r[1].max(0.0), r[1].max(0.0)),
            Point::new(r[2].max(0.0), r[2].max(0.0)),
            Point::new(r[3].max(0.0), r[3].max(0.0)),
        ],
        None => corner_radii(node.radius, &node.corners),
    };
    let rrect = RRect::new_rect_radii(rect, &radii);
    if let Some(fill) = &node.fill {
        canvas.draw_rrect(rrect, &fill_paint(fill, off));
    }
    if let Some(stroke) = &node.stroke {
        canvas.draw_rrect(rrect, &stroke_paint(stroke, node.stroke_width, off));
    }
}

fn render_pie_slice(canvas: &Canvas, node: &PieSliceNode, off: (f32, f32)) {
    let oval = Rect::from_xywh(
        node.pos[0] + off.0,
        node.pos[1] + off.1,
        node.size[0],
        node.size[1],
    );
    let sweep = node.end_angle - node.start_angle;
    // use_center = true draws the filled pie wedge (matches Pillow's pieslice).
    if let Some(fill) = &node.fill {
        canvas.draw_arc(oval, node.start_angle, sweep, true, &fill_paint(fill, off));
    }
    if let Some(stroke) = &node.stroke {
        canvas.draw_arc(
            oval,
            node.start_angle,
            sweep,
            true,
            &stroke_paint(stroke, node.stroke_width, off),
        );
    }
}

fn render_shadow(canvas: &Canvas, node: &ShadowNode, off: (f32, f32)) {
    let rect = Rect::from_xywh(
        node.pos[0] + off.0 + node.offset[0],
        node.pos[1] + off.1 + node.offset[1],
        node.size[0],
        node.size[1],
    );
    let mut paint = Paint::default();
    paint.set_anti_alias(true);
    let c = node.color;
    let alpha = (node.alpha.clamp(0.0, 1.0) * c[3] as f32) as u8;
    paint.set_color(Color::from_argb(alpha, c[0], c[1], c[2]));
    paint.set_mask_filter(MaskFilter::blur(BlurStyle::Normal, node.sigma, true));
    canvas.draw_rrect(RRect::new_rect_xy(rect, node.radius, node.radius), &paint);
}

/// Walk the tree and hard-fail on any `SdfQuad` whose `field` is not a raw Alpha8 mem entry.
/// The contract is strict on purpose: the field is per-request data the emitter just shipped,
/// so a missing/mistyped one is an emitter bug — erroring the scene reaches Python's fail-open
/// catch, while skipping would serve an image with glyphs silently missing.
/// Reject nodes that depend on an identity CTM inside a `Transform` subtree.
///
/// `SelfImage` / `BlurGlass` / adaptive `Text` snapshot device bounds, and `SdfQuad` is
/// pre-warped to device space with integer placement; under a non-identity matrix they would
/// sample the wrong canvas region or be double-transformed. The emitter (custom profile)
/// never nests them — so one showing up is an emitter REGRESSION, and failing the WHOLE
/// scene routes it into the Python fail-open path (Pillow) instead of a silently wrong
/// image, the same doctrine as unknown node kinds and dangling SdfQuad field refs.
fn validate_transform_subtrees(node: &Node, in_transform: bool) -> Result<(), String> {
    match node {
        Node::SelfImage(_) if in_transform => {
            Err("SelfImage inside Transform requires an identity CTM".to_string())
        }
        Node::BlurGlass(_) if in_transform => {
            Err("BlurGlass inside Transform requires an identity CTM".to_string())
        }
        Node::SdfQuad(_) if in_transform => {
            Err("SdfQuad inside Transform would be double-transformed".to_string())
        }
        Node::SdfShape(_) if in_transform => {
            Err("SdfShape inside Transform would apply screen-space scale twice".to_string())
        }
        Node::UnityImage(_) if in_transform => {
            Err("UnityImage inside Transform would apply its transform twice".to_string())
        }
        Node::UnitySubscene(_) if in_transform => {
            Err("UnitySubscene inside Transform would apply its transform twice".to_string())
        }
        Node::RasterSubscene(_) if in_transform => {
            Err("RasterSubscene inside Transform is unsupported".to_string())
        }
        Node::Text(text) if in_transform && text.adaptive.is_some() => {
            Err("adaptive Text inside Transform requires an identity CTM".to_string())
        }
        Node::Group(group) => group
            .children
            .iter()
            .try_for_each(|child| validate_transform_subtrees(child, in_transform)),
        Node::Transform(transform) => transform
            .children
            .iter()
            .try_for_each(|child| validate_transform_subtrees(child, true)),
        Node::UnitySubscene(subscene) => subscene
            .children
            .iter()
            .try_for_each(|child| validate_transform_subtrees(child, false)),
        Node::RasterSubscene(subscene) => subscene
            .children
            .iter()
            .try_for_each(|child| validate_transform_subtrees(child, false)),
        _ => Ok(()),
    }
}

/// Validate the generic isolate-then-place raster boundary before any native resource access.
///
/// Scene.scale is intentionally absent from `in_transform`: on this renderer baseline it is a
/// final whole-page raster resize. Explicit Transform nodes remain unsupported because their
/// arbitrary matrix would make the node's logical destination contract ambiguous.
fn validate_raster_subscene_usage(node: &Node, in_transform: bool) -> Result<(), String> {
    match node {
        Node::Group(group) => group
            .children
            .iter()
            .try_for_each(|child| validate_raster_subscene_usage(child, in_transform)),
        Node::Transform(transform) => transform
            .children
            .iter()
            .try_for_each(|child| validate_raster_subscene_usage(child, true)),
        Node::UnitySubscene(subscene) => subscene
            .children
            .iter()
            .try_for_each(|child| validate_raster_subscene_usage(child, false)),
        Node::RasterSubscene(subscene) => {
            if in_transform {
                return Err("RasterSubscene inside Transform is unsupported".to_string());
            }
            if subscene.natural_size[0] <= 0 || subscene.natural_size[1] <= 0 {
                return Err("RasterSubscene natural_size must be positive".to_string());
            }
            let placement = [
                subscene.pos[0],
                subscene.pos[1],
                subscene.dst_size[0],
                subscene.dst_size[1],
                subscene.alpha,
            ];
            if placement.iter().any(|value| !value.is_finite()) {
                return Err("RasterSubscene placement contains a non-finite scalar".to_string());
            }
            if subscene.dst_size[0] <= 0.0 || subscene.dst_size[1] <= 0.0 {
                return Err("RasterSubscene dst_size must be positive".to_string());
            }
            if !(0.0..=1.0).contains(&subscene.alpha) {
                return Err("RasterSubscene alpha must be between 0 and 1".to_string());
            }
            if subscene.sampling == ImageSampling::PillowLanczos {
                return Err("RasterSubscene does not support pillow_lanczos sampling".to_string());
            }
            if let Some(shadow) = subscene.shadow {
                let shadow_values = [
                    shadow.alpha,
                    shadow.offset[0],
                    shadow.offset[1],
                    shadow.sigma,
                ];
                if shadow_values.iter().any(|value| !value.is_finite())
                    || !(0.0..=1.0).contains(&shadow.alpha)
                    || shadow.sigma < 0.0
                {
                    return Err("RasterSubscene shadow parameters are invalid".to_string());
                }
            }
            subscene
                .children
                .iter()
                .try_for_each(|child| validate_raster_subscene_usage(child, false))
        }
        _ => Ok(()),
    }
}

/// Validate the explicit Pillow `paste(source, pos, source)` compatibility path.
///
/// Its blend is defined over straight RGBA bytes, so it cannot be expressed as an ordinary
/// Skia Porter-Duff mode. The interpreter reads the integral destination rectangle, performs
/// Pillow's byte lerp, and writes the result back through the active clip. Fractional/device-
/// transformed placements would require coverage semantics that are outside this contract.
fn validate_paste_lerp_usage(
    node: &Node,
    off: (f32, f32),
    in_transform: bool,
    in_mask_layer: bool,
) -> Result<(), String> {
    match node {
        Node::Group(group) => {
            let child_off = (off.0 + group.offset[0], off.1 + group.offset[1]);
            let child_in_mask_layer = in_mask_layer || group.mask.is_some();
            group.children.iter().try_for_each(|child| {
                validate_paste_lerp_usage(child, child_off, in_transform, child_in_mask_layer)
            })
        }
        Node::Transform(transform) => transform.children.iter().try_for_each(|child| {
            validate_paste_lerp_usage(child, (0.0, 0.0), true, in_mask_layer)
        }),
        // A UnitySubscene renders its children into a fresh identity-CTM local surface. Its
        // later Unity placement transforms the completed raster, not the PasteLerp operation.
        Node::UnitySubscene(subscene) => subscene
            .children
            .iter()
            .try_for_each(|child| validate_paste_lerp_usage(child, (0.0, 0.0), false, false)),
        // RasterSubscene is a fresh root surface: parent saveLayer state does not enter it, so
        // straight-RGBA destination reads are safe inside this isolation boundary.
        Node::RasterSubscene(subscene) => subscene
            .children
            .iter()
            .try_for_each(|child| validate_paste_lerp_usage(child, (0.0, 0.0), false, false)),
        Node::Rect(rect) if rect.blend == ImageBlend::PasteLerp => {
            Err("paste_lerp is supported only by Image nodes".to_string())
        }
        Node::Image(image) if image.blend == ImageBlend::PasteLerp => {
            if in_transform {
                return Err("paste_lerp Image inside Transform is unsupported".to_string());
            }
            if in_mask_layer {
                return Err(
                    "paste_lerp Image inside a masked Group saveLayer is unsupported".to_string(),
                );
            }
            if image.fit != Fit::Stretch {
                return Err(format!(
                    "paste_lerp Image requires stretch fit, got {:?}",
                    image.fit
                ));
            }
            if image.alpha != 1.0 {
                return Err("paste_lerp Image requires alpha=1".to_string());
            }
            if image.source_rect.is_some()
                || image.tint.is_some()
                || image.shadow.is_some()
                || image.blur_sigma.iter().any(|value| *value != 0.0)
            {
                return Err(
                    "paste_lerp Image does not support source_rect, tint, shadow, or blur"
                        .to_string(),
                );
            }
            let left = image.pos[0] + off.0 - image.size[0] * image.anchor[0];
            let top = image.pos[1] + off.1 - image.size[1] * image.anchor[1];
            let edges = [left, top, left + image.size[0], top + image.size[1]];
            if image.size[0] <= 0.0
                || image.size[1] <= 0.0
                || edges.iter().any(|value| {
                    !value.is_finite()
                        || (*value - value.round()).abs() > 1.0e-3
                        || f64::from(*value) < f64::from(i32::MIN)
                        || f64::from(*value) > f64::from(i32::MAX)
                })
            {
                return Err(
                    "paste_lerp Image requires a positive integral destination rectangle"
                        .to_string(),
                );
            }
            Ok(())
        }
        _ => Ok(()),
    }
}

/// Validate the deliberately narrow Pillow-Lanczos IR contract before any asset access or draw.
///
/// The compatibility resizer is a full-raster operation. Letting it flow into an ordinary Skia
/// sampling call would silently change kernels, so every unsupported placement is a scene error
/// and therefore reaches Python's fail-open Pillow path.
fn validate_pillow_lanczos_usage(node: &Node, in_transform: bool) -> Result<(), String> {
    match node {
        Node::Group(group) => group
            .children
            .iter()
            .try_for_each(|child| validate_pillow_lanczos_usage(child, in_transform)),
        Node::Transform(transform) => transform
            .children
            .iter()
            .try_for_each(|child| validate_pillow_lanczos_usage(child, true)),
        Node::Image(image) if image.sampling == ImageSampling::PillowLanczos => {
            if in_transform {
                return Err("pillow_lanczos Image inside Transform is unsupported".to_string());
            }
            if !matches!(image.fit, Fit::Stretch | Fit::Cover) {
                return Err(format!(
                    "pillow_lanczos Image supports only stretch/cover fit, got {:?}",
                    image.fit
                ));
            }
            if image.source_rect.is_some() {
                return Err(
                    "pillow_lanczos Image does not support source_rect; resize the full raster \
                     before cropping"
                        .to_string(),
                );
            }
            if image.tint.is_some()
                || image.shadow.is_some()
                || image.blur_sigma.iter().any(|value| *value != 0.0)
            {
                return Err(
                    "pillow_lanczos Image does not support tint, shadow, or blur decorations"
                        .to_string(),
                );
            }
            if image.path.starts_with("mem:") {
                return Err(
                    "pillow_lanczos Image requires an asset-backed straight RGBA8 source"
                        .to_string(),
                );
            }
            Ok(())
        }
        Node::UnityImage(image) if image.sampling == ImageSampling::PillowLanczos => Err(
            "UnityImage does not support pillow_lanczos; use a zero-rotation UnitySubscene"
                .to_string(),
        ),
        Node::UnitySubscene(subscene) => {
            if subscene.sampling == ImageSampling::PillowLanczos {
                let angle = subscene.rotation % 360.0;
                if !angle.is_finite() || angle.abs() >= 1.0e-9 {
                    return Err("pillow_lanczos UnitySubscene requires zero rotation".to_string());
                }
            }
            subscene
                .children
                .iter()
                .try_for_each(|child| validate_pillow_lanczos_usage(child, in_transform))
        }
        Node::RasterSubscene(subscene) => subscene
            .children
            .iter()
            .try_for_each(|child| validate_pillow_lanczos_usage(child, false)),
        Node::SelfImage(image) if image.sampling == ImageSampling::PillowLanczos => {
            Err("SelfImage does not support pillow_lanczos sampling".to_string())
        }
        _ => Ok(()),
    }
}

fn validate_sdf_quad_fields(
    node: &Node,
    mem_images: &HashMap<String, MemImage>,
) -> Result<(), String> {
    match node {
        Node::Group(group) => group
            .children
            .iter()
            .try_for_each(|child| validate_sdf_quad_fields(child, mem_images)),
        Node::Transform(transform) => transform
            .children
            .iter()
            .try_for_each(|child| validate_sdf_quad_fields(child, mem_images)),
        Node::UnitySubscene(subscene) => subscene
            .children
            .iter()
            .try_for_each(|child| validate_sdf_quad_fields(child, mem_images)),
        Node::RasterSubscene(subscene) => subscene
            .children
            .iter()
            .try_for_each(|child| validate_sdf_quad_fields(child, mem_images)),
        Node::SdfQuad(quad) => {
            let Some(key) = quad.field.strip_prefix("mem:") else {
                return Err(format!(
                    "SdfQuad field must be a mem image reference: {}",
                    quad.field
                ));
            };
            match mem_images.get(key) {
                Some(MemImage::Raw {
                    color_type: ColorType::Alpha8,
                    ..
                }) => Ok(()),
                Some(MemImage::Raw { color_type, .. }) => Err(format!(
                    "SdfQuad field {} must be an Alpha8 raw mem image, got {color_type:?}",
                    quad.field
                )),
                Some(MemImage::Encoded { .. }) => Err(format!(
                    "SdfQuad field {} must be an Alpha8 raw mem image, got encoded bytes",
                    quad.field
                )),
                None => Err(format!(
                    "SdfQuad field references unknown mem image: {}",
                    quad.field
                )),
            }
        }
        _ => Ok(()),
    }
}

/// Decode every explicit Pillow-Lanczos Image to straight RGBA8 before drawing starts.
///
/// General Image nodes are normally fail-soft. This sampling mode is not: a missing/corrupt
/// source must fail the whole scene so CardDisplayList can retry through its Pillow fallback
/// rather than returning a card with one silently absent layer.
fn prepare_pillow_lanczos_sources(node: &Node, interp: &mut Interp) -> Result<(), String> {
    match node {
        Node::Group(group) => group
            .children
            .iter()
            .try_for_each(|child| prepare_pillow_lanczos_sources(child, interp)),
        Node::Transform(transform) => transform
            .children
            .iter()
            .try_for_each(|child| prepare_pillow_lanczos_sources(child, interp)),
        Node::UnitySubscene(subscene) => subscene
            .children
            .iter()
            .try_for_each(|child| prepare_pillow_lanczos_sources(child, interp)),
        Node::RasterSubscene(subscene) => subscene
            .children
            .iter()
            .try_for_each(|child| prepare_pillow_lanczos_sources(child, interp)),
        Node::Image(image) if image.sampling == ImageSampling::PillowLanczos => {
            if interp.pillow_lanczos_sources.contains_key(&image.path) {
                return Ok(());
            }
            let (descriptor, _) = interp.describe_asset(&image.path).map_err(|err| {
                format!(
                    "pillow_lanczos Image asset load failed: {} ({err})",
                    image.path
                )
            })?;
            let source_bytes = validate_strict_asset_size(
                descriptor.width,
                descriptor.height,
                interp.max_node_pixels,
                "pillow_lanczos Image source",
            )?;
            interp.ensure_native_scene_bytes(source_bytes, "pillow_lanczos Image source")?;
            let started = Instant::now();
            let (pixels, width, height) =
                decode_asset_rgba_unpremul(&descriptor).map_err(|err| {
                    format!(
                        "pillow_lanczos Image asset decode failed: {} ({err})",
                        image.path
                    )
                })?;
            interp.metrics.asset_load_elapsed += started.elapsed().as_secs_f64();
            interp.retain_native_asset_bytes(source_bytes, "pillow_lanczos Image source")?;
            interp.pillow_lanczos_sources.insert(
                image.path.clone(),
                PillowLanczosSource {
                    pixels,
                    width,
                    height,
                },
            );
            Ok(())
        }
        _ => Ok(()),
    }
}

fn validate_strict_asset_size(
    width: i32,
    height: i32,
    max_node_pixels: usize,
    label: &str,
) -> Result<usize, String> {
    if width <= 0 || height <= 0 {
        return Err(format!("{label} dimensions must be positive"));
    }
    let pixels = usize::try_from(width)
        .ok()
        .and_then(|width| {
            usize::try_from(height)
                .ok()
                .and_then(|height| width.checked_mul(height))
        })
        .ok_or_else(|| format!("{label} pixel count overflow"))?;
    if pixels > max_node_pixels {
        return Err(format!(
            "{label} {width}x{height} ({pixels} pixels) exceeds limit {max_node_pixels}"
        ));
    }
    pixels
        .checked_mul(4)
        .ok_or_else(|| format!("{label} byte count overflow"))
}

/// Fully decode one ordinary Image/mask used inside an isolated subscene. General IR images are
/// intentionally fail-soft, but an isolated subtree must either render completely or fail the
/// whole native scene.
fn prepare_strict_image_ref(path: &str, interp: &mut Interp, context: &str) -> Result<(), String> {
    if interp.direct_images.contains_key(path) {
        return Ok(());
    }
    if let Some(key) = path.strip_prefix("mem:") {
        let encoded = match interp.mem_images.get(key) {
            Some(MemImage::Encoded { .. }) => true,
            Some(MemImage::Raw { .. }) => false,
            None => {
                return Err(format!(
                    "{context} image references unknown mem image: {path}"
                ));
            }
        };
        let image = interp
            .load_mem(path)
            .ok_or_else(|| format!("{context} failed to decode mem image: {path}"))?;
        let raster_bytes = validate_strict_asset_size(
            image.width(),
            image.height(),
            interp.max_node_pixels,
            &format!("{context} mem image"),
        )?;
        if encoded {
            interp
                .ensure_native_scene_bytes(raster_bytes, &format!("{context} decoded mem image"))?;
            let raster = image
                .make_raster_image(None, CachingHint::Disallow)
                .ok_or_else(|| format!("{context} failed to raster-decode mem image: {path}"))?;
            interp
                .retain_native_asset_bytes(raster_bytes, &format!("{context} decoded mem image"))?;
            interp.direct_images.insert(path.to_string(), raster);
        }
        return Ok(());
    }

    let (descriptor, source) = interp
        .describe_asset(path)
        .map_err(|err| format!("{context} asset load failed: {path} ({err})"))?;
    let raster_bytes = validate_strict_asset_size(
        descriptor.width,
        descriptor.height,
        interp.max_node_pixels,
        &format!("{context} asset"),
    )?;
    interp.ensure_native_scene_bytes(raster_bytes, &format!("{context} decoded asset"))?;
    let encoded = source
        .map(Ok)
        .unwrap_or_else(|| decode_asset_descriptor(&descriptor))
        .map_err(|err| format!("{context} asset decode failed: {path} ({err})"))?;
    let raster = encoded
        .make_raster_image(None, CachingHint::Disallow)
        .ok_or_else(|| format!("{context} asset raster decode failed: {path}"))?;
    interp.retain_native_asset_bytes(raster_bytes, &format!("{context} decoded asset"))?;
    interp.direct_images.insert(path.to_string(), raster);
    Ok(())
}

fn prepare_strict_subscene_children(
    node: &Node,
    interp: &mut Interp,
    context: &str,
) -> Result<(), String> {
    match node {
        Node::Group(group) => {
            if let Some(mask) = &group.mask {
                prepare_strict_image_ref(mask, interp, context)?;
            }
            group
                .children
                .iter()
                .try_for_each(|child| prepare_strict_subscene_children(child, interp, context))
        }
        Node::Transform(transform) => transform
            .children
            .iter()
            .try_for_each(|child| prepare_strict_subscene_children(child, interp, context)),
        Node::UnitySubscene(subscene) => subscene
            .children
            .iter()
            .try_for_each(|child| prepare_strict_subscene_children(child, interp, "UnitySubscene")),
        Node::RasterSubscene(subscene) => subscene.children.iter().try_for_each(|child| {
            prepare_strict_subscene_children(child, interp, "RasterSubscene")
        }),
        Node::Image(image) if image.sampling == ImageSampling::PillowLanczos => interp
            .pillow_lanczos_sources
            .contains_key(&image.path)
            .then_some(())
            .ok_or_else(|| {
                format!(
                    "pillow_lanczos Image source was not prepared: {}",
                    image.path
                )
            }),
        Node::Image(image) => prepare_strict_image_ref(&image.path, interp, context),
        Node::SlicedImage(image) => prepare_strict_image_ref(&image.path, interp, context),
        Node::ImageBg(image) => prepare_strict_image_ref(&image.path, interp, context),
        _ => Ok(()),
    }
}

fn prepare_unity_subscene_assets(node: &Node, interp: &mut Interp) -> Result<(), String> {
    match node {
        Node::Group(group) => group
            .children
            .iter()
            .try_for_each(|child| prepare_unity_subscene_assets(child, interp)),
        Node::Transform(transform) => transform
            .children
            .iter()
            .try_for_each(|child| prepare_unity_subscene_assets(child, interp)),
        Node::UnitySubscene(subscene) => subscene
            .children
            .iter()
            .try_for_each(|child| prepare_strict_subscene_children(child, interp, "UnitySubscene")),
        Node::RasterSubscene(subscene) => subscene.children.iter().try_for_each(|child| {
            prepare_strict_subscene_children(child, interp, "RasterSubscene")
        }),
        _ => Ok(()),
    }
}

fn sdf_shape_dimensions(
    node: &SdfShapeNode,
    source_width: i32,
    source_height: i32,
    max_pixels: usize,
) -> Result<(i32, i32), String> {
    let finite_values = [
        node.anchor[0],
        node.anchor[1],
        node.sdf_scale[0],
        node.sdf_scale[1],
        node.post_scale[0],
        node.post_scale[1],
        node.rotation,
        node.fill_alpha,
        node.outline_alpha,
        node.outer_fill_ratio,
        node.face_dilate,
        node.softness,
    ];
    if finite_values.iter().any(|value| !value.is_finite()) {
        return Err("SdfShape contains a non-finite scalar".to_string());
    }
    if node.sdf_scale.iter().any(|value| *value <= 0.0)
        || node.post_scale.iter().any(|value| *value <= 0.0)
    {
        return Err("SdfShape scales must be positive".to_string());
    }
    if node
        .sdf_scale
        .iter()
        .chain(node.post_scale.iter())
        .any(|value| *value > 64.0)
    {
        return Err("SdfShape scale exceeds the native safety limit".to_string());
    }
    let scaled_dimension = |source: i32, scale: f32| -> Result<i32, String> {
        let value = source as f64 * scale as f64;
        if !value.is_finite() || value <= 0.0 || value > i32::MAX as f64 {
            return Err("SdfShape dimensions overflow".to_string());
        }
        Ok(value.round_ties_even().max(1.0) as i32)
    };
    let width = scaled_dimension(source_width, node.sdf_scale[0])?;
    let height = scaled_dimension(source_height, node.sdf_scale[1])?;
    let pixels = (width as usize)
        .checked_mul(height as usize)
        .ok_or_else(|| "SdfShape pixel count overflow".to_string())?;
    if pixels > max_pixels {
        return Err(format!(
            "SdfShape patch {width}x{height} ({pixels} pixels) exceeds limit {max_pixels}"
        ));
    }
    Ok((width, height))
}

fn prepare_sdf_shape_sources(node: &Node, interp: &mut Interp) -> Result<(), String> {
    match node {
        Node::Group(group) => group
            .children
            .iter()
            .try_for_each(|child| prepare_sdf_shape_sources(child, interp)),
        Node::Transform(transform) => transform
            .children
            .iter()
            .try_for_each(|child| prepare_sdf_shape_sources(child, interp)),
        Node::UnitySubscene(subscene) => subscene
            .children
            .iter()
            .try_for_each(|child| prepare_sdf_shape_sources(child, interp)),
        Node::RasterSubscene(subscene) => subscene
            .children
            .iter()
            .try_for_each(|child| prepare_sdf_shape_sources(child, interp)),
        Node::SdfShape(shape) => {
            if !interp.sdf_shape_sources.contains_key(&shape.path) {
                let (descriptor, _) = interp.describe_asset(&shape.path)?;
                let source_pixels = (descriptor.width as usize)
                    .checked_mul(descriptor.height as usize)
                    .ok_or_else(|| "SdfShape source pixel count overflow".to_string())?;
                if source_pixels > interp.max_node_pixels {
                    return Err(format!(
                        "SdfShape source {}x{} exceeds limit {}",
                        descriptor.width, descriptor.height, interp.max_node_pixels
                    ));
                }
                let source_bytes = source_pixels
                    .checked_mul(4)
                    .ok_or_else(|| "SdfShape source byte count overflow".to_string())?;
                interp.ensure_native_scene_bytes(source_bytes, "SdfShape source")?;
                let started = Instant::now();
                let (pixels, width, height) = decode_asset_rgba_unpremul(&descriptor)?;
                interp.metrics.asset_load_elapsed += started.elapsed().as_secs_f64();
                interp.retain_native_asset_bytes(source_bytes, "SdfShape source")?;
                interp.sdf_shape_sources.insert(
                    shape.path.clone(),
                    SdfShapeSource {
                        pixels,
                        width,
                        height,
                    },
                );
            }
            let source = interp
                .sdf_shape_sources
                .get(&shape.path)
                .ok_or_else(|| format!("SdfShape source was not prepared: {}", shape.path))?;
            sdf_shape_dimensions(shape, source.width, source.height, interp.max_node_pixels)?;
            Ok(())
        }
        _ => Ok(()),
    }
}

fn validate_unity_image(node: &UnityImageNode) -> Result<(), String> {
    let values = [
        node.anchor[0],
        node.anchor[1],
        node.object_scale[0],
        node.object_scale[1],
        node.post_scale[0],
        node.post_scale[1],
        node.rotation,
        node.alpha,
    ];
    if values.iter().any(|value| !value.is_finite()) {
        return Err("UnityImage contains a non-finite scalar".to_string());
    }
    if node.object_scale.iter().any(|value| *value <= 0.0)
        || node.post_scale.iter().any(|value| *value <= 0.0)
    {
        return Err("UnityImage scales must be positive".to_string());
    }
    if node
        .object_scale
        .iter()
        .chain(node.post_scale.iter())
        .any(|value| *value > 64.0)
    {
        return Err("UnityImage scale exceeds the native safety limit".to_string());
    }
    Ok(())
}

#[derive(Clone, Copy, Debug)]
struct UnityImageDimensions {
    first: (i32, i32),
    final_size: (i32, i32),
}

fn unity_image_dimensions(
    node: &UnityImageNode,
    source_width: i32,
    source_height: i32,
    max_pixels: usize,
) -> Result<UnityImageDimensions, String> {
    if source_width <= 0 || source_height <= 0 {
        return Err("UnityImage source dimensions must be positive".to_string());
    }
    let scaled_dimension = |source: i32, scale: f32, label: &str| -> Result<i32, String> {
        let value = (source as f64 * scale as f64).round_ties_even().max(1.0);
        if !value.is_finite() || value > i32::MAX as f64 {
            return Err(format!("UnityImage {label} dimension overflow"));
        }
        Ok(value as i32)
    };
    let first = (
        scaled_dimension(source_width, node.object_scale[0], "object-scale width")?,
        scaled_dimension(source_height, node.object_scale[1], "object-scale height")?,
    );
    let final_size = (
        scaled_dimension(first.0, node.post_scale[0], "post-scale width")?,
        scaled_dimension(first.1, node.post_scale[1], "post-scale height")?,
    );
    for (label, (width, height)) in [
        ("source", (source_width, source_height)),
        ("object-scale raster", first),
        ("post-scale raster", final_size),
    ] {
        let pixels = usize::try_from(width)
            .ok()
            .and_then(|width| {
                usize::try_from(height)
                    .ok()
                    .and_then(|height| width.checked_mul(height))
            })
            .ok_or_else(|| format!("UnityImage {label} dimensions overflow"))?;
        if pixels > max_pixels {
            return Err(format!(
                "UnityImage {label} {width}x{height} ({pixels} pixels) exceeds limit \
                 {max_pixels}"
            ));
        }
    }
    Ok(UnityImageDimensions { first, final_size })
}

fn prepare_unity_image_assets(node: &Node, interp: &mut Interp) -> Result<(), String> {
    match node {
        Node::Group(group) => group
            .children
            .iter()
            .try_for_each(|child| prepare_unity_image_assets(child, interp)),
        Node::Transform(transform) => transform
            .children
            .iter()
            .try_for_each(|child| prepare_unity_image_assets(child, interp)),
        Node::UnitySubscene(subscene) => subscene
            .children
            .iter()
            .try_for_each(|child| prepare_unity_image_assets(child, interp)),
        Node::RasterSubscene(subscene) => subscene
            .children
            .iter()
            .try_for_each(|child| prepare_unity_image_assets(child, interp)),
        Node::UnityImage(image) => {
            validate_unity_image(image)?;
            if image.path.starts_with("mem:") {
                return Err(format!(
                    "UnityImage cannot reference request memory: {}",
                    image.path
                ));
            }
            if let Some(prepared) = interp.unity_image_sources.get(&image.path) {
                unity_image_dimensions(
                    image,
                    prepared.width,
                    prepared.height,
                    interp.max_node_pixels,
                )?;
                return Ok(());
            }
            let (descriptor, _) = interp.describe_asset(&image.path)?;
            unity_image_dimensions(
                image,
                descriptor.width,
                descriptor.height,
                interp.max_node_pixels,
            )?;
            let source_pixels = usize::try_from(descriptor.width)
                .ok()
                .and_then(|width| {
                    usize::try_from(descriptor.height)
                        .ok()
                        .and_then(|height| width.checked_mul(height))
                })
                .ok_or_else(|| "UnityImage source pixel count overflow".to_string())?;
            let source_bytes = source_pixels
                .checked_mul(4)
                .ok_or_else(|| "UnityImage source byte count overflow".to_string())?;
            // Codec output and Skia Data coexist briefly while the straight-alpha raster is
            // constructed. Count both so the configured scene budget is a peak bound, not only
            // a steady-state accounting number.
            interp.ensure_native_scene_bytes(
                source_bytes
                    .checked_mul(2)
                    .ok_or_else(|| "UnityImage transient byte count overflow".to_string())?,
                "UnityImage source decode",
            )?;
            let started = Instant::now();
            let (pixels, width, height) = decode_asset_rgba_unpremul(&descriptor)?;
            interp.metrics.asset_load_elapsed += started.elapsed().as_secs_f64();
            let info = ImageInfo::new(
                (width, height),
                ColorType::RGBA8888,
                AlphaType::Unpremul,
                None,
            );
            let prepared = skia_safe::images::raster_from_data(
                &info,
                Data::new_copy(&pixels),
                width as usize * 4,
            )
            .ok_or_else(|| "failed to build UnityImage straight-alpha source".to_string())?;
            interp.retain_native_asset_bytes(source_bytes, "UnityImage source")?;
            interp.unity_image_sources.insert(
                image.path.clone(),
                UnityImageSource {
                    image: prepared,
                    width,
                    height,
                },
            );
            Ok(())
        }
        _ => Ok(()),
    }
}

fn sample_sdf_shape_channel(
    source: &SdfShapeSource,
    out_width: usize,
    out_height: usize,
    x: usize,
    y: usize,
    channel: usize,
) -> f32 {
    let source_x = (((x as f32 + 0.5) * source.width as f32 / out_width as f32) - 0.5)
        .clamp(0.0, (source.width - 1) as f32);
    let source_y = (((y as f32 + 0.5) * source.height as f32 / out_height as f32) - 0.5)
        .clamp(0.0, (source.height - 1) as f32);
    let x0 = source_x.floor() as usize;
    let y0 = source_y.floor() as usize;
    let x1 = (x0 + 1).min(source.width as usize - 1);
    let y1 = (y0 + 1).min(source.height as usize - 1);
    let tx = source_x - x0 as f32;
    let ty = source_y - y0 as f32;
    let stride = source.width as usize * 4;
    let value = |px: usize, py: usize| source.pixels[py * stride + px * 4 + channel] as f32 / 255.0;
    let top = value(x0, y0) * (1.0 - tx) + value(x1, y0) * tx;
    let bottom = value(x0, y1) * (1.0 - tx) + value(x1, y1) * tx;
    top * (1.0 - ty) + bottom * ty
}

fn sample_sdf_shape_row(
    source: &SdfShapeSource,
    out_width: usize,
    out_height: usize,
    y: usize,
    channel: usize,
) -> Vec<f32> {
    (0..out_width)
        .map(|x| sample_sdf_shape_channel(source, out_width, out_height, x, y, channel))
        .collect()
}

fn shade_sdf_shape(
    source: &SdfShapeSource,
    node: &SdfShapeNode,
    out_width: usize,
    out_height: usize,
) -> Result<Vec<u8>, String> {
    let byte_len = out_width
        .checked_mul(out_height)
        .and_then(|pixels| pixels.checked_mul(4))
        .ok_or_else(|| "SdfShape output byte size overflow".to_string())?;
    let mut patch = Vec::new();
    patch
        .try_reserve_exact(byte_len)
        .map_err(|_| format!("SdfShape output allocation rejected: {out_width}x{out_height}"))?;
    patch.resize(byte_len, 0);

    let field_channel = match node.field_channel {
        SdfShapeFieldChannel::Red => 0,
        SdfShapeFieldChannel::Alpha => 3,
    };
    let mut previous = sample_sdf_shape_row(source, out_width, out_height, 0, field_channel);
    let mut current = previous.clone();
    let mut next = sample_sdf_shape_row(
        source,
        out_width,
        out_height,
        usize::from(out_height > 1),
        field_channel,
    );
    let softness = node.softness.max(0.0);

    for y in 0..out_height {
        let texture_alpha = sample_sdf_shape_row(source, out_width, out_height, y, 3);
        for x in 0..out_width {
            let grad_x = if out_width <= 1 {
                0.0
            } else if x == 0 {
                current[1] - current[0]
            } else if x + 1 == out_width {
                current[x] - current[x - 1]
            } else {
                (current[x + 1] - current[x - 1]) * 0.5
            };
            let grad_y = if out_height <= 1 {
                0.0
            } else if y == 0 {
                next[x] - current[x]
            } else if y + 1 == out_height {
                current[x] - previous[x]
            } else {
                (next[x] - previous[x]) * 0.5
            };
            let fwidth = grad_x.abs() + grad_y.abs();
            let half_width = softness * 0.5 + fwidth;
            let edge0 = 0.5 - half_width;
            let edge1 = 0.5 + half_width;
            let span = (edge1 - edge0).max(1.0e-6);
            let t = ((current[x] - edge0) / span).clamp(0.0, 1.0);
            let smooth = t * t * (3.0 - 2.0 * t);
            let face = if smooth >= 0.899999976 {
                texture_alpha[x] * smooth * node.fill_alpha
            } else {
                0.0
            };
            let outline_distance = current[x] + smooth * 0.5 + node.face_dilate * 0.5;
            let outline_t = (outline_distance * 10.0).clamp(0.0, 1.0);
            let outline_smooth = outline_t * outline_t * (3.0 - 2.0 * outline_t);
            let outline = texture_alpha[x] * outline_smooth * node.outline_alpha;
            let outline_pixel =
                outline_distance >= 1.0 - node.outer_fill_ratio && outline_distance < 1.0;
            let alpha = if outline_pixel { outline } else { face };
            let rgb = if outline_pixel {
                node.outline_color
            } else {
                node.fill_color
            };
            let pixel = &mut patch[(y * out_width + x) * 4..][..4];
            pixel[..3].copy_from_slice(&rgb);
            pixel[3] = (alpha * 255.0).round_ties_even().clamp(0.0, 255.0) as u8;
        }

        if y + 1 < out_height {
            previous = current;
            current = next;
            next = sample_sdf_shape_row(
                source,
                out_width,
                out_height,
                (y + 2).min(out_height - 1),
                field_channel,
            );
        }
    }
    Ok(patch)
}

fn draw_sdf_shape(
    surface: &mut Surface,
    interp: &Interp,
    node: &SdfShapeNode,
    off: (f32, f32),
) -> Result<(), String> {
    let source = interp
        .sdf_shape_sources
        .get(&node.path)
        .ok_or_else(|| format!("SdfShape source was not prepared: {}", node.path))?;
    let (width, height) =
        sdf_shape_dimensions(node, source.width, source.height, interp.max_node_pixels)?;
    let patch_bytes = usize::try_from(width)
        .ok()
        .and_then(|width| {
            usize::try_from(height)
                .ok()
                .and_then(|height| width.checked_mul(height))
        })
        .and_then(|pixels| pixels.checked_mul(4))
        .ok_or_else(|| "SdfShape patch byte count overflow".to_string())?;
    interp.ensure_native_scene_bytes(
        patch_bytes
            .checked_mul(2)
            .ok_or_else(|| "SdfShape transient byte count overflow".to_string())?,
        "SdfShape patch",
    )?;
    let patch = shade_sdf_shape(source, node, width as usize, height as usize)?;
    let info = ImageInfo::new(
        (width, height),
        ColorType::RGBA8888,
        AlphaType::Unpremul,
        None,
    );
    let image =
        skia_safe::images::raster_from_data(&info, Data::new_copy(&patch), width as usize * 4)
            .ok_or_else(|| "failed to build SdfShape raster image".to_string())?;
    let anchor = (node.anchor[0] + off.0, node.anchor[1] + off.1);
    let angle = node.rotation % 360.0;
    let mut paint = Paint::default();
    paint.set_anti_alias(true);

    if angle.abs() < 1.0e-9 {
        let final_width = (width as f32 * node.post_scale[0])
            .round_ties_even()
            .max(1.0);
        let final_height = (height as f32 * node.post_scale[1])
            .round_ties_even()
            .max(1.0);
        let left = (anchor.0 - width as f32 * 0.5 * node.post_scale[0]).round_ties_even();
        let top = (anchor.1 - height as f32 * 0.5 * node.post_scale[1]).round_ties_even();
        surface.canvas().draw_image_rect_with_sampling_options(
            &image,
            None,
            Rect::from_xywh(left, top, final_width, final_height),
            CubicResampler::catmull_rom(),
            &paint,
        );
        return Ok(());
    }

    let theta = angle.to_radians();
    let cos_t = theta.cos();
    let sin_t = theta.sin();
    let pivot = (width as f32 * 0.5, height as f32 * 0.5);
    let sx = node.post_scale[0];
    let sy = node.post_scale[1];
    let matrix = Matrix::new_all(
        cos_t * sx,
        -sin_t * sy,
        anchor.0 - cos_t * sx * pivot.0 + sin_t * sy * pivot.1,
        sin_t * sx,
        cos_t * sy,
        anchor.1 - sin_t * sx * pivot.0 - cos_t * sy * pivot.1,
        0.0,
        0.0,
        1.0,
    );
    let canvas = surface.canvas();
    let save_count = canvas.save();
    canvas.concat(&matrix);
    canvas.draw_image_with_sampling_options(
        &image,
        (0.0, 0.0),
        CubicResampler::catmull_rom(),
        Some(&paint),
    );
    canvas.restore_to_count(save_count);
    Ok(())
}

fn draw_unity_image(
    canvas: &Canvas,
    interp: &Interp,
    node: &UnityImageNode,
    off: (f32, f32),
) -> Result<(), String> {
    validate_unity_image(node)?;
    let source = interp
        .unity_image_sources
        .get(&node.path)
        .ok_or_else(|| format!("UnityImage source was not prepared: {}", node.path))?;
    draw_unity_raster(canvas, interp, &source.image, node, off)
}

fn sliced_source_span(points: [i32; 4], index: usize, source_extent: i32) -> (i32, i32) {
    let mut start = points[index];
    let mut end = points[index + 1];
    if end <= start {
        start = (start - 1).clamp(0, source_extent - 1);
        end = start + 1;
    }
    (start, end)
}

fn draw_sliced_patch(
    canvas: &Canvas,
    source: &Image,
    node: &SlicedImageNode,
    geometry: SlicedImageGeometry,
    off: (f32, f32),
) -> Result<(), String> {
    let (target_width, target_height) = geometry.target_size;
    let mut patch =
        surfaces::raster_n32_premul((target_width, target_height)).ok_or_else(|| {
            format!("failed to create SlicedImage surface {target_width}x{target_height}")
        })?;
    patch.canvas().clear(Color::TRANSPARENT);

    let mut paint = Paint::default();
    // PIL resizes and alpha-composites integer rectangles. Anti-aliasing the rectangle edges
    // would blend neighbouring slices or leak beyond the completed patch.
    paint.set_anti_alias(false);
    paint.set_alpha_f(node.alpha.clamp(0.0, 1.0));
    if let Some(tint) = &node.tint {
        paint.set_color_filter(tint_filter(tint));
    }
    let sampling = SamplingOptions::from(CubicResampler::catmull_rom());
    for y_index in 0..3 {
        let target_top = geometry.y.target[y_index];
        let target_bottom = geometry.y.target[y_index + 1];
        if target_bottom <= target_top {
            continue;
        }
        let (source_top, source_bottom) =
            sliced_source_span(geometry.y.source, y_index, source.height());
        for x_index in 0..3 {
            let target_left = geometry.x.target[x_index];
            let target_right = geometry.x.target[x_index + 1];
            if target_right <= target_left {
                continue;
            }
            let (source_left, source_right) =
                sliced_source_span(geometry.x.source, x_index, source.width());
            let source_rect = Rect::new(
                source_left as f32,
                source_top as f32,
                source_right as f32,
                source_bottom as f32,
            );
            let target_rect = Rect::new(
                target_left as f32,
                target_top as f32,
                target_right as f32,
                target_bottom as f32,
            );
            patch.canvas().draw_image_rect_with_sampling_options(
                source,
                Some((&source_rect, SrcRectConstraint::Strict)),
                target_rect,
                sampling,
                &paint,
            );
        }
    }

    let completed = patch.image_snapshot();
    let destination = (
        node.pos[0].round_ties_even() + off.0,
        node.pos[1].round_ties_even() + off.1,
    );
    canvas.draw_image_with_sampling_options(
        &completed,
        destination,
        SamplingOptions::default(),
        Some(&Paint::default()),
    );
    Ok(())
}

fn draw_sliced_image(
    canvas: &Canvas,
    interp: &mut Interp,
    node: &SlicedImageNode,
    off: (f32, f32),
) -> Result<(), String> {
    let strict = interp.strict_asset_depth > 0;
    let (image, source_width, source_height) = if strict {
        let image = interp
            .direct_images
            .get(&node.path)
            .cloned()
            .ok_or_else(|| {
                format!(
                    "UnitySubscene SlicedImage source was not prepared or decoded: {}",
                    node.path
                )
            })?;
        let (width, height) = (image.width(), image.height());
        (image, width, height)
    } else if node.path.starts_with("mem:") {
        let Some(image) = interp.load_mem(&node.path) else {
            return Ok(());
        };
        let (width, height) = (image.width(), image.height());
        (image, width, height)
    } else {
        let prepared = interp.direct_images.get(&node.path).cloned();
        let (descriptor, source) = match interp.describe_asset(&node.path) {
            Ok(loaded) => loaded,
            Err(err) => {
                eprintln!(
                    "haruki_skia_renderer: SlicedImage asset load failed, node skipped: {} ({err})",
                    node.path
                );
                return Ok(());
            }
        };
        let image = match prepared.or(source) {
            Some(image) => image,
            None => match decode_asset_descriptor(&descriptor) {
                Ok(image) => image,
                Err(err) => {
                    eprintln!(
                        "haruki_skia_renderer: SlicedImage asset decode failed, node skipped: {} ({err})",
                        node.path
                    );
                    return Ok(());
                }
            },
        };
        (image, descriptor.width, descriptor.height)
    };

    let (geometry, target_bytes) =
        sliced_image_geometry(node, source_width, source_height, interp.max_node_pixels)?;
    let source_runtime_bytes = if strict {
        0
    } else {
        rgba_byte_len(source_width, source_height, "SlicedImage decoded source")?
    };
    let runtime_bytes = target_bytes
        .checked_add(source_runtime_bytes)
        .ok_or_else(|| "SlicedImage runtime byte count overflow".to_string())?;
    interp.push_native_runtime_bytes(runtime_bytes, "SlicedImage render")?;
    let result = (|| {
        let raster = if strict {
            image
        } else {
            let Some(raster) = image.make_raster_image(None, CachingHint::Disallow) else {
                eprintln!(
                    "haruki_skia_renderer: SlicedImage pixel decode failed, node skipped: {}",
                    node.path
                );
                return Ok(());
            };
            raster
        };
        draw_sliced_patch(canvas, &raster, node, geometry, off)
    })();
    interp.pop_native_runtime_bytes(runtime_bytes);
    result
}

fn draw_unity_raster(
    canvas: &Canvas,
    interp: &Interp,
    image: &Image,
    node: &UnityImageNode,
    off: (f32, f32),
) -> Result<(), String> {
    let source_width = image.width();
    let source_height = image.height();
    let dimensions =
        unity_image_dimensions(node, source_width, source_height, interp.max_node_pixels)?;
    let raster_bytes = |size: (i32, i32)| -> Result<usize, String> {
        usize::try_from(size.0)
            .ok()
            .and_then(|width| {
                usize::try_from(size.1)
                    .ok()
                    .and_then(|height| width.checked_mul(height))
            })
            .and_then(|pixels| pixels.checked_mul(4))
            .ok_or_else(|| "UnityImage raster byte count overflow".to_string())
    };
    let mut transient_bytes = 0usize;
    if dimensions.first != (source_width, source_height) {
        transient_bytes = raster_bytes(dimensions.first)?;
    }
    if dimensions.final_size != dimensions.first {
        transient_bytes = transient_bytes
            .checked_add(raster_bytes(dimensions.final_size)?)
            .ok_or_else(|| "UnityImage transient byte count overflow".to_string())?;
    }
    interp.ensure_native_scene_bytes(transient_bytes, "UnityImage resize")?;
    let anchor = (node.anchor[0] + off.0, node.anchor[1] + off.1);
    let angle = node.rotation % 360.0;
    let mut paint = Paint::default();
    paint.set_anti_alias(true);
    paint.set_alpha_f(node.alpha.clamp(0.0, 1.0));
    let sampling = skia_image_sampling(node.sampling)
        .ok_or_else(|| "UnityImage does not support pillow_lanczos sampling".to_string())?;
    let mut resized = image.clone();
    if dimensions.first != (source_width, source_height) {
        resized = draw_source_to_raster(
            &resized,
            Rect::from_xywh(0.0, 0.0, source_width as f32, source_height as f32),
            dimensions.first.0,
            dimensions.first.1,
            sampling,
        )?;
    }
    if dimensions.final_size != dimensions.first {
        resized = draw_source_to_raster(
            &resized,
            Rect::from_xywh(
                0.0,
                0.0,
                dimensions.first.0 as f32,
                dimensions.first.1 as f32,
            ),
            dimensions.final_size.0,
            dimensions.final_size.1,
            sampling,
        )?;
    }
    let pivot_x = source_width as f32 * 0.5 * node.object_scale[0] * node.post_scale[0];
    let pivot_y = source_height as f32 * 0.5 * node.object_scale[1] * node.post_scale[1];

    if angle.abs() < 1.0e-9 {
        let left = (anchor.0 - pivot_x).round_ties_even();
        let top = (anchor.1 - pivot_y).round_ties_even();
        canvas.draw_image_with_sampling_options(
            &resized,
            (left, top),
            SamplingOptions::default(),
            Some(&paint),
        );
        return Ok(());
    }

    let theta = angle.to_radians();
    let cos_t = theta.cos();
    let sin_t = theta.sin();
    let matrix = Matrix::new_all(
        cos_t,
        -sin_t,
        anchor.0 - cos_t * pivot_x + sin_t * pivot_y,
        sin_t,
        cos_t,
        anchor.1 - sin_t * pivot_x - cos_t * pivot_y,
        0.0,
        0.0,
        1.0,
    );
    let save_count = canvas.save();
    canvas.concat(&matrix);
    canvas.draw_image_with_sampling_options(&resized, (0.0, 0.0), sampling, Some(&paint));
    canvas.restore_to_count(save_count);
    Ok(())
}

fn read_surface_straight_rgba8(surface: &mut Surface) -> Result<Vec<u8>, String> {
    let (width, height) = (surface.width(), surface.height());
    let byte_count = rgba_byte_len(width, height, "pillow_lanczos UnitySubscene readback")?;
    let row_bytes = usize::try_from(width)
        .ok()
        .and_then(|value| value.checked_mul(4))
        .ok_or_else(|| "pillow_lanczos UnitySubscene row size overflow".to_string())?;
    let mut pixels = Vec::new();
    pixels.try_reserve_exact(byte_count).map_err(|_| {
        format!("pillow_lanczos UnitySubscene readback allocation rejected: {width}x{height}")
    })?;
    pixels.resize(byte_count, 0);
    let info = ImageInfo::new(
        (width, height),
        ColorType::RGBA8888,
        AlphaType::Unpremul,
        None,
    );
    if !surface.read_pixels(&info, &mut pixels, row_bytes, (0, 0)) {
        return Err("failed to read straight RGBA8 UnitySubscene pixels".to_string());
    }
    Ok(pixels)
}

/// Resize a runtime-owned straight RGBA8 buffer and transfer the byte accounting from the old
/// Vec to the returned Vec. The Pillow resizer accounts for all of its additional scratch through
/// `available_native_scene_bytes`; no Catmull-Rom draw is an error recovery path.
fn resize_active_pillow_buffer(
    interp: &mut Interp,
    current: Vec<u8>,
    current_size: (i32, i32),
    destination_size: (i32, i32),
    label: &str,
) -> Result<Vec<u8>, String> {
    if current_size == destination_size {
        return Ok(current);
    }
    let current_bytes = current.len();
    let available = interp.available_native_scene_bytes(label)?;
    let resized = pillow_resize_buffer(
        &current,
        current_size,
        destination_size,
        interp.max_node_pixels,
        available,
        label,
    )?;
    let resized_bytes = resized.len();
    interp.push_native_runtime_bytes(resized_bytes, label)?;
    drop(current);
    interp.pop_native_runtime_bytes(current_bytes);
    Ok(resized)
}

fn draw_pillow_lanczos_unity_subscene(
    canvas: &Canvas,
    interp: &mut Interp,
    sub_surface: &mut Surface,
    node: &UnitySubsceneNode,
    off: (f32, f32),
) -> Result<(), String> {
    let source_size = (sub_surface.width(), sub_surface.height());
    let placement = UnityImageNode {
        path: "<pillow-lanczos-unity-subscene>".to_string(),
        anchor: node.anchor,
        object_scale: node.object_scale,
        post_scale: node.post_scale,
        rotation: node.rotation,
        sampling: node.sampling,
        alpha: node.alpha,
    };
    validate_unity_image(&placement)?;
    let dimensions = unity_image_dimensions(
        &placement,
        source_size.0,
        source_size.1,
        interp.max_node_pixels,
    )?;
    let source_bytes = rgba_byte_len(
        source_size.0,
        source_size.1,
        "pillow_lanczos UnitySubscene readback",
    )?;
    interp.push_native_runtime_bytes(source_bytes, "pillow_lanczos UnitySubscene readback")?;
    let mut pixels = read_surface_straight_rgba8(sub_surface)?;
    let mut pixel_size = source_size;

    if dimensions.first != pixel_size {
        pixels = resize_active_pillow_buffer(
            interp,
            pixels,
            pixel_size,
            dimensions.first,
            "pillow_lanczos UnitySubscene object-scale resize",
        )?;
        pixel_size = dimensions.first;
    }
    if dimensions.final_size != pixel_size {
        pixels = resize_active_pillow_buffer(
            interp,
            pixels,
            pixel_size,
            dimensions.final_size,
            "pillow_lanczos UnitySubscene post-scale resize",
        )?;
        pixel_size = dimensions.final_size;
    }

    let pixel_bytes = pixels.len();
    interp
        .push_native_runtime_bytes(pixel_bytes, "pillow_lanczos UnitySubscene Skia raster copy")?;
    let info = ImageInfo::new(pixel_size, ColorType::RGBA8888, AlphaType::Unpremul, None);
    let image = skia_safe::images::raster_from_data(
        &info,
        Data::new_copy(&pixels),
        pixel_size.0 as usize * 4,
    )
    .ok_or_else(|| "failed to build pillow_lanczos UnitySubscene raster".to_string())?;
    drop(pixels);
    interp.pop_native_runtime_bytes(pixel_bytes);

    let anchor = (node.anchor[0] + off.0, node.anchor[1] + off.1);
    let pivot_x = source_size.0 as f32 * 0.5 * node.object_scale[0] * node.post_scale[0];
    let pivot_y = source_size.1 as f32 * 0.5 * node.object_scale[1] * node.post_scale[1];
    let left = (anchor.0 - pivot_x).round_ties_even();
    let top = (anchor.1 - pivot_y).round_ties_even();
    let mut paint = Paint::default();
    paint.set_anti_alias(false);
    paint.set_alpha_f(node.alpha.clamp(0.0, 1.0));
    canvas.draw_image_with_sampling_options(
        &image,
        (left, top),
        SamplingOptions::default(),
        Some(&paint),
    );
    drop(image);
    interp.pop_native_runtime_bytes(pixel_bytes);
    Ok(())
}

fn draw_unity_subscene(
    surface: &mut Surface,
    interp: &mut Interp,
    node: &UnitySubsceneNode,
    off: (f32, f32),
) -> Result<(), String> {
    let (width, height) = (node.size[0], node.size[1]);
    if width <= 0 || height <= 0 {
        return Err("UnitySubscene size must be positive".to_string());
    }
    let pixels = usize::try_from(width)
        .ok()
        .and_then(|width| {
            usize::try_from(height)
                .ok()
                .and_then(|height| width.checked_mul(height))
        })
        .ok_or_else(|| "UnitySubscene pixel count overflow".to_string())?;
    if pixels > interp.max_node_pixels {
        return Err(format!(
            "UnitySubscene {width}x{height} ({pixels} pixels) exceeds limit {}",
            interp.max_node_pixels
        ));
    }
    let surface_bytes = pixels
        .checked_mul(4)
        .ok_or_else(|| "UnitySubscene byte count overflow".to_string())?;
    let placement = UnityImageNode {
        path: "<unity-subscene>".to_string(),
        anchor: node.anchor,
        object_scale: node.object_scale,
        post_scale: node.post_scale,
        rotation: node.rotation,
        sampling: node.sampling,
        alpha: node.alpha,
    };
    validate_unity_image(&placement)?;
    unity_image_dimensions(&placement, width, height, interp.max_node_pixels)?;
    interp.push_native_runtime_bytes(surface_bytes, "UnitySubscene surface")?;

    let result = (|| {
        let mut sub_surface = surfaces::raster_n32_premul((width, height))
            .ok_or_else(|| format!("failed to create UnitySubscene surface {width}x{height}"))?;
        sub_surface.canvas().clear(Color::TRANSPARENT);

        let previous_canvas = (interp.canvas_w, interp.canvas_h);
        let previous_in_transform = interp.in_transform;
        let previous_strict_asset_depth = interp.strict_asset_depth;
        interp.canvas_w = width as f32;
        interp.canvas_h = height as f32;
        interp.in_transform = false;
        interp.strict_asset_depth = previous_strict_asset_depth.saturating_add(1);
        let child_result = node
            .children
            .iter()
            .try_for_each(|child| render_node(&mut sub_surface, interp, (0.0, 0.0), child));
        interp.canvas_w = previous_canvas.0;
        interp.canvas_h = previous_canvas.1;
        interp.in_transform = previous_in_transform;
        interp.strict_asset_depth = previous_strict_asset_depth;
        child_result?;

        if node.sampling == ImageSampling::PillowLanczos {
            draw_pillow_lanczos_unity_subscene(
                surface.canvas(),
                interp,
                &mut sub_surface,
                node,
                off,
            )
        } else {
            let image = sub_surface.image_snapshot();
            draw_unity_raster(surface.canvas(), interp, &image, &placement, off)
        }
    })();
    interp.pop_native_runtime_bytes(surface_bytes);
    result
}

fn draw_raster_subscene(
    surface: &mut Surface,
    interp: &mut Interp,
    node: &RasterSubsceneNode,
    off: (f32, f32),
) -> Result<(), String> {
    let (width, height) = (node.natural_size[0], node.natural_size[1]);
    let surface_bytes = validate_strict_asset_size(
        width,
        height,
        interp.max_node_pixels,
        "RasterSubscene natural surface",
    )?;
    interp.push_native_runtime_bytes(surface_bytes, "RasterSubscene natural surface")?;

    let result = (|| {
        let mut sub_surface = surfaces::raster_n32_premul((width, height))
            .ok_or_else(|| format!("failed to create RasterSubscene surface {width}x{height}"))?;
        sub_surface.canvas().clear(Color::TRANSPARENT);

        let previous_canvas = (interp.canvas_w, interp.canvas_h);
        let previous_in_transform = interp.in_transform;
        let previous_strict_asset_depth = interp.strict_asset_depth;
        interp.canvas_w = width as f32;
        interp.canvas_h = height as f32;
        interp.in_transform = false;
        interp.strict_asset_depth = previous_strict_asset_depth.saturating_add(1);
        let child_result = node
            .children
            .iter()
            .try_for_each(|child| render_node(&mut sub_surface, interp, (0.0, 0.0), child));
        interp.canvas_w = previous_canvas.0;
        interp.canvas_h = previous_canvas.1;
        interp.in_transform = previous_in_transform;
        interp.strict_asset_depth = previous_strict_asset_depth;
        child_result?;

        let image = sub_surface.image_snapshot();
        let dst = Rect::from_xywh(
            node.pos[0] + off.0,
            node.pos[1] + off.1,
            node.dst_size[0],
            node.dst_size[1],
        );
        let image_node = ImageNode {
            pos: node.pos,
            size: node.dst_size,
            path: "<raster-subscene>".to_string(),
            fit: Fit::Stretch,
            sampling: node.sampling,
            source_rect: None,
            alpha: node.alpha,
            anchor: [0.0, 0.0],
            tint: None,
            shadow: node.shadow,
            blur_sigma: [0.0, 0.0],
            blend: ImageBlend::SrcOver,
        };
        let sampling = skia_image_sampling(node.sampling)
            .ok_or_else(|| "RasterSubscene does not support pillow_lanczos sampling".to_string())?;
        draw_image_placed(
            surface.canvas(),
            &image,
            ImagePlacement { src: None, dst },
            sampling,
            &image_node,
        );
        Ok(())
    })();
    interp.pop_native_runtime_bytes(surface_bytes);
    result
}

/// The SdfQuad per-pixel routine, factored out so the golden tests drive the exact code the
/// render arm uses. `field` is the A8 field (row-major, `row_bytes` stride, values 0..255);
/// the return value is the straight-alpha RGBA8888 patch (tight `width * 4` stride).
///
/// This must match Python's `shade_tmp_sdf_field` + `rgba_from_premul` bit-comparably
/// (per-channel |delta| <= 1): scalars are pre-cast f64 -> f32 ONCE, every per-pixel operation
/// is f32 with the same association order as the numpy expressions, and quantization uses
/// banker's rounding (`round_ties_even`, numpy `rint`) — not half-up.
pub(crate) fn shade_sdf_field(
    field: &[u8],
    width: usize,
    height: usize,
    row_bytes: usize,
    shading: &SdfShading,
) -> Vec<u8> {
    let face_scale = shading.face_scale as f32;
    let face_w = shading.face_w as f32;
    let alpha = shading.alpha as f32;
    let face_rgb = shading.face_color.map(|c| c as f32 / 255.0);
    let underlay = shading.underlay.as_ref().map(|u| {
        (
            u.scale as f32,
            u.w as f32,
            u.shift,
            u.color.map(|c| c as f32 / 255.0),
        )
    });

    let mut patch = vec![0_u8; width * height * 4];
    for y in 0..height {
        let row = &field[y * row_bytes..y * row_bytes + width];
        for x in 0..width {
            let f = row[x] as f32 / 255.0;
            let face_a = (f * face_scale - face_w).clamp(0.0, 1.0) * alpha;
            let (under_a, under_rgb) = match &underlay {
                Some((u_scale, u_w, shift, u_rgb)) => {
                    // shifted[y][x] = field[y + sy][x + sx]; out-of-bounds samples 0.0.
                    let sx = x as i64 + shift[0] as i64;
                    let sy = y as i64 + shift[1] as i64;
                    let shifted =
                        if (0..width as i64).contains(&sx) && (0..height as i64).contains(&sy) {
                            field[sy as usize * row_bytes + sx as usize] as f32 / 255.0
                        } else {
                            0.0
                        };
                    ((shifted * u_scale - u_w).clamp(0.0, 1.0) * alpha, *u_rgb)
                }
                None => (0.0, [0.0; 3]),
            };
            let out_a = face_a + under_a * (1.0 - face_a);
            let px = &mut patch[(y * width + x) * 4..(y * width + x) * 4 + 4];
            for c in 0..3 {
                let premul = face_rgb[c] * face_a + under_rgb[c] * under_a * (1.0 - face_a);
                // Straight-alpha quantization exactly like `rgba_from_premul`.
                let rgb = if out_a > 1e-6 { premul / out_a } else { 0.0 };
                px[c] = (rgb * 255.0).round_ties_even().clamp(0.0, 255.0) as u8;
            }
            px[3] = (out_a * 255.0).round_ties_even().clamp(0.0, 255.0) as u8;
        }
    }
    patch
}

/// Shade an SdfQuad's pre-warped A8 field and draw the straight-alpha patch src-over at its
/// integer position — nearest sampling, no AA, ZERO geometric resampling (the field arrives
/// already at display size). The field reference was validated up front, so a miss here only
/// happens for test-constructed scenes; it degrades to skipping the node like other draws.
fn draw_sdf_quad(surface: &mut Surface, interp: &Interp, node: &SdfQuadNode, off: (f32, f32)) {
    let Some(key) = node.field.strip_prefix("mem:") else {
        return;
    };
    let Some(MemImage::Raw {
        width,
        height,
        row_bytes,
        color_type: ColorType::Alpha8,
        data,
        ..
    }) = interp.mem_images.get(key)
    else {
        return;
    };
    let (w, h) = (*width as usize, *height as usize);
    let bytes = data.as_bytes();
    if bytes.len()
        < row_bytes
            .saturating_mul(h.saturating_sub(1))
            .saturating_add(w)
    {
        eprintln!(
            "haruki_skia_renderer: SdfQuad field buffer too small, node skipped: {}",
            node.field
        );
        return;
    }
    let patch = shade_sdf_field(bytes, w, h, *row_bytes, &node.shading);
    let info = ImageInfo::new(
        (*width, *height),
        ColorType::RGBA8888,
        AlphaType::Unpremul,
        None,
    );
    let Some(image) = skia_safe::images::raster_from_data(&info, Data::new_copy(&patch), w * 4)
    else {
        eprintln!(
            "haruki_skia_renderer: SdfQuad patch image build failed, node skipped: {}",
            node.field
        );
        return;
    };
    let paint = Paint::default();
    surface.canvas().draw_image_with_sampling_options(
        &image,
        (node.pos[0] + off.0, node.pos[1] + off.1),
        SamplingOptions::default(),
        Some(&paint),
    );
}

#[derive(Clone, Copy)]
struct ImagePlacement {
    src: Option<Rect>,
    dst: Rect,
}

#[derive(Hash, PartialEq, Eq)]
struct ImagePrewarmKey {
    path: String,
    size_bits: [u32; 2],
    source_rect_bits: Option<[u32; 4]>,
    fit: u8,
    sampling: u8,
}

struct ImagePrewarmRequest<'a> {
    node: &'a ImageNode,
    off: (f32, f32),
}

struct ImagePrewarmResult {
    path: String,
    descriptor: Option<AssetDescriptor>,
    asset_load_elapsed: f64,
    outcome: Option<RasterCacheOutcome>,
}

fn image_fit_key(fit: Fit) -> u8 {
    match fit {
        Fit::Stretch => 0,
        Fit::Cover => 1,
        Fit::Contain => 2,
        Fit::Width => 3,
        Fit::Crop => 4,
    }
}

fn prewarm_float_bits(value: f32) -> u32 {
    if value == 0.0 { 0 } else { value.to_bits() }
}

fn image_prewarm_key(node: &ImageNode) -> ImagePrewarmKey {
    ImagePrewarmKey {
        path: node.path.clone(),
        size_bits: [
            prewarm_float_bits(node.size[0]),
            prewarm_float_bits(node.size[1]),
        ],
        source_rect_bits: node.source_rect.map(|rect| rect.map(prewarm_float_bits)),
        fit: image_fit_key(node.fit),
        sampling: sampling_key(node.sampling),
    }
}

fn collect_image_prewarm_requests<'a>(
    node: &'a Node,
    off: (f32, f32),
    seen: &mut HashSet<ImagePrewarmKey>,
    requests: &mut Vec<ImagePrewarmRequest<'a>>,
) {
    match node {
        Node::Group(group) => {
            let child_off = (off.0 + group.offset[0], off.1 + group.offset[1]);
            for child in &group.children {
                collect_image_prewarm_requests(child, child_off, seen, requests);
            }
        }
        Node::Image(image)
            if image.sampling != ImageSampling::PillowLanczos
                && image.blend != ImageBlend::PasteLerp
                && !image.path.starts_with("mem:")
                && seen.insert(image_prewarm_key(image)) =>
        {
            requests.push(ImagePrewarmRequest { node: image, off });
        }
        // Deliberately do NOT recurse into Transform: a prewarm target size is only meaningful
        // under identity CTM — under the matrix the device footprint differs from the node's
        // dst size, and `draw_image_node` skips the raster cache inside a Transform anyway, so
        // a prewarmed entry could never be consumed.
        Node::Transform(_) => {}
        // Isolated-subscene ordinary assets are fully decoded by the strict preflight and then
        // drawn directly. Do not let fail-soft parallel prewarming duplicate their source/target
        // rasters outside the per-scene memory budget.
        Node::UnitySubscene(_) | Node::RasterSubscene(_) => {}
        _ => {}
    }
}

fn prewarm_image(base: &std::path::Path, request: &ImagePrewarmRequest<'_>) -> ImagePrewarmResult {
    let load_started = Instant::now();
    let loaded = load_asset_descriptor(base, &request.node.path);
    let asset_load_elapsed = load_started.elapsed().as_secs_f64();
    let Ok(loaded) = loaded else {
        return ImagePrewarmResult {
            path: request.node.path.clone(),
            descriptor: None,
            asset_load_elapsed,
            outcome: None,
        };
    };
    let descriptor = loaded.descriptor;
    let outcome = image_placement(
        descriptor.width,
        descriptor.height,
        request.node,
        request.off,
    )
    .and_then(|placement| {
        let (width, height) = integral_target(placement.dst)?;
        let src = placement.src.unwrap_or_else(|| {
            Rect::from_xywh(0.0, 0.0, descriptor.width as f32, descriptor.height as f32)
        });
        rasterize_asset_cached(
            &descriptor,
            loaded.source.as_ref(),
            src,
            width,
            height,
            skia_image_sampling(request.node.sampling)?,
            sampling_key(request.node.sampling),
        )
        .ok()
        .flatten()
        .map(|cached| cached.outcome)
    });
    ImagePrewarmResult {
        path: request.node.path.clone(),
        descriptor: Some(descriptor),
        asset_load_elapsed,
        outcome,
    }
}

fn prewarm_scene_images(scene: &Scene, interp: &mut Interp) {
    if raster_cache_snapshot().max_bytes == 0 {
        return;
    }
    let mut seen = HashSet::new();
    let mut requests = Vec::new();
    if let Some(background) = &scene.background {
        collect_image_prewarm_requests(background, (0.0, 0.0), &mut seen, &mut requests);
    }
    collect_image_prewarm_requests(&scene.root, (0.0, 0.0), &mut seen, &mut requests);
    if requests.len() < 2 {
        return;
    }

    let started = Instant::now();
    let results: Vec<_> = requests
        .par_iter()
        .map(|request| prewarm_image(&interp.base, request))
        .collect();
    interp.metrics.raster_prewarm_elapsed = started.elapsed().as_secs_f64();
    interp.metrics.raster_prewarm_requests = requests.len() as u64;
    interp.metrics.asset_load_elapsed += results
        .iter()
        .map(|result| result.asset_load_elapsed)
        .sum::<f64>();

    for result in results {
        if let Some(descriptor) = result.descriptor {
            interp.asset_descriptors.insert(result.path, descriptor);
        }
        match result.outcome {
            Some(RasterCacheOutcome::Hit) => interp.metrics.raster_prewarm_hits += 1,
            Some(RasterCacheOutcome::Miss) => interp.metrics.raster_prewarm_misses += 1,
            Some(RasterCacheOutcome::Coalesced) => interp.metrics.raster_prewarm_coalesced += 1,
            None => {}
        }
    }
    if interp.metrics.raster_prewarm_misses > 0 {
        interp.metrics.raster_cache_build_elapsed += interp.metrics.raster_prewarm_elapsed;
    }
}

fn image_placement(
    image_width: i32,
    image_height: i32,
    node: &ImageNode,
    off: (f32, f32),
) -> Option<ImagePlacement> {
    // Optional source-pixel crop window applied before fit: only this sub-rect participates.
    // All fit math below runs in crop-local coords (origin 0,0, size iw×ih); the resulting
    // source rect is translated back into the original image by (base_x, base_y) at the end.
    let img_w = image_width as f32;
    let img_h = image_height as f32;
    let (base_x, base_y, iw, ih) = match node.source_rect {
        Some([x0, y0, x1, y1]) => {
            let cx0 = x0.clamp(0.0, img_w);
            let cy0 = y0.clamp(0.0, img_h);
            let cx1 = x1.clamp(cx0, img_w);
            let cy1 = y1.clamp(cy0, img_h);
            (cx0, cy0, cx1 - cx0, cy1 - cy0)
        }
        None => (0.0, 0.0, img_w, img_h),
    };
    if iw <= 0.0 || ih <= 0.0 {
        return None;
    }
    // The drawn rect size depends on the fit mode (width fit derives height from aspect).
    let (rw, rh) = match node.fit {
        Fit::Width => (node.size[0], node.size[0] * ih / iw),
        _ => (node.size[0], node.size[1]),
    };
    // Anchor `pos` within the rect: [0,0] top-left .. [1,1] bottom-right.
    let x = node.pos[0] + off.0 - rw * node.anchor[0];
    let y = node.pos[1] + off.1 - rh * node.anchor[1];

    // Resolve the (source, destination) rects for the fit mode.
    let (src, dst) = match node.fit {
        Fit::Stretch | Fit::Width => (None, Rect::from_xywh(x, y, rw, rh)),
        Fit::Contain => {
            let scale = (rw / iw).min(rh / ih);
            let w = iw * scale;
            let h = ih * scale;
            (
                None,
                Rect::from_xywh(x + (rw - w) * 0.5, y + (rh - h) * 0.5, w, h),
            )
        }
        Fit::Cover => {
            let scale = (rw / iw).max(rh / ih);
            let sw = rw / scale;
            let sh = rh / scale;
            let s = Rect::from_xywh((iw - sw) * 0.5, (ih - sh) * 0.5, sw, sh);
            (Some(s), Rect::from_xywh(x, y, rw, rh))
        }
        Fit::Crop => {
            // Center-crop without scaling: take a rw×rh window of the source (clamped), draw 1:1.
            let cw = rw.min(iw);
            let ch = rh.min(ih);
            let s = Rect::from_xywh((iw - cw) * 0.5, (ih - ch) * 0.5, cw, ch);
            let d = Rect::from_xywh(x + (rw - cw) * 0.5, y + (rh - ch) * 0.5, cw, ch);
            (Some(s), d)
        }
    };
    // Translate the crop-local source rect back into the original image. With a crop and a
    // whole-source fit (src == None), the crop window itself becomes the explicit source rect.
    let src = match (src, node.source_rect) {
        (Some(s), _) => Some(Rect::from_xywh(
            s.left + base_x,
            s.top + base_y,
            s.width(),
            s.height(),
        )),
        (None, Some(_)) => Some(Rect::from_xywh(base_x, base_y, iw, ih)),
        (None, None) => None,
    };
    Some(ImagePlacement { src, dst })
}

fn integral_target(rect: Rect) -> Option<(i32, i32)> {
    let values = [rect.left, rect.top, rect.right, rect.bottom];
    if values
        .iter()
        .any(|value| !value.is_finite() || (*value - value.round()).abs() > 1e-3)
    {
        return None;
    }
    let width = rect.width().round() as i32;
    let height = rect.height().round() as i32;
    (width > 0 && height > 0).then_some((width, height))
}

fn sampling_key(mode: ImageSampling) -> u8 {
    match mode {
        ImageSampling::Nearest => 0,
        ImageSampling::Linear => 1,
        ImageSampling::Cubic => 2,
        ImageSampling::LinearMipmap => 3,
        ImageSampling::CatmullRom => 4,
        ImageSampling::PillowLanczos => 5,
    }
}

fn pillow_resize_buffer(
    source: &[u8],
    source_size: (i32, i32),
    destination_size: (i32, i32),
    max_node_pixels: usize,
    available_scene_bytes: usize,
    label: &str,
) -> Result<Vec<u8>, String> {
    let output_bytes = validate_strict_asset_size(
        destination_size.0,
        destination_size.1,
        max_node_pixels,
        label,
    )?;
    if output_bytes > available_scene_bytes {
        return Err(format!(
            "{label} needs at least {output_bytes} output bytes; only \
             {available_scene_bytes} bytes remain in the scene limit"
        ));
    }
    let max_dimension = max_node_pixels.min(i32::MAX as usize).max(1);
    resize_rgba8_pillow_lanczos(
        source,
        usize::try_from(source_size.0).map_err(|_| format!("{label} source width is invalid"))?,
        usize::try_from(source_size.1).map_err(|_| format!("{label} source height is invalid"))?,
        usize::try_from(destination_size.0)
            .map_err(|_| format!("{label} destination width is invalid"))?,
        usize::try_from(destination_size.1)
            .map_err(|_| format!("{label} destination height is invalid"))?,
        PillowResizeLimits::new(output_bytes, available_scene_bytes, max_dimension),
    )
    .map_err(|err| format!("{label} failed: {err}"))
}

fn crop_rgba8(
    source: &[u8],
    source_size: (i32, i32),
    left: i32,
    top: i32,
    destination_size: (i32, i32),
) -> Result<Vec<u8>, String> {
    let (source_width, source_height) = source_size;
    let (width, height) = destination_size;
    if left < 0
        || top < 0
        || width <= 0
        || height <= 0
        || left
            .checked_add(width)
            .is_none_or(|right| right > source_width)
        || top
            .checked_add(height)
            .is_none_or(|bottom| bottom > source_height)
    {
        return Err("pillow_lanczos cover crop is outside the resized raster".to_string());
    }
    let output_bytes = rgba_byte_len(width, height, "pillow_lanczos cover crop")?;
    let source_stride = usize::try_from(source_width)
        .ok()
        .and_then(|value| value.checked_mul(4))
        .ok_or_else(|| "pillow_lanczos cover source stride overflow".to_string())?;
    let row_bytes = usize::try_from(width)
        .ok()
        .and_then(|value| value.checked_mul(4))
        .ok_or_else(|| "pillow_lanczos cover row size overflow".to_string())?;
    let mut output = Vec::new();
    output
        .try_reserve_exact(output_bytes)
        .map_err(|_| format!("pillow_lanczos cover crop allocation rejected: {width}x{height}"))?;
    output.resize(output_bytes, 0);
    for row in 0..height {
        let source_start = usize::try_from(top + row)
            .ok()
            .and_then(|y| y.checked_mul(source_stride))
            .and_then(|offset| {
                usize::try_from(left)
                    .ok()
                    .and_then(|x| x.checked_mul(4))
                    .and_then(|x| offset.checked_add(x))
            })
            .ok_or_else(|| "pillow_lanczos cover source offset overflow".to_string())?;
        let destination_start = usize::try_from(row)
            .ok()
            .and_then(|row| row.checked_mul(row_bytes))
            .ok_or_else(|| "pillow_lanczos cover destination offset overflow".to_string())?;
        output[destination_start..destination_start + row_bytes]
            .copy_from_slice(&source[source_start..source_start + row_bytes]);
    }
    Ok(output)
}

/// Resize one explicit Image node through Pillow's full-raster Lanczos pipeline.
///
/// The returned Vec is registered in `active_native_runtime_bytes`; the caller must pop its
/// length after it has copied the pixels into the Skia image used for the final integer paste.
fn rasterize_pillow_lanczos_image(
    interp: &mut Interp,
    node: &ImageNode,
    off: (f32, f32),
) -> Result<(Vec<u8>, i32, i32, Rect), String> {
    let source = interp
        .pillow_lanczos_sources
        .get(&node.path)
        .ok_or_else(|| {
            format!(
                "pillow_lanczos Image source was not prepared: {}",
                node.path
            )
        })?;
    let placement = image_placement(source.width, source.height, node, off)
        .ok_or_else(|| "pillow_lanczos Image has empty placement".to_string())?;
    let destination_size = integral_target(placement.dst).ok_or_else(|| {
        "pillow_lanczos Image requires integral destination edges and dimensions".to_string()
    })?;
    let source_size = (source.width, source.height);

    let resized_size = match node.fit {
        Fit::Stretch => destination_size,
        Fit::Cover => {
            let scale = (destination_size.0 as f64 / source.width as f64)
                .max(destination_size.1 as f64 / source.height as f64);
            let width = (source.width as f64 * scale).round_ties_even().max(1.0);
            let height = (source.height as f64 * scale).round_ties_even().max(1.0);
            if !width.is_finite()
                || !height.is_finite()
                || width > i32::MAX as f64
                || height > i32::MAX as f64
            {
                return Err("pillow_lanczos cover dimensions overflow".to_string());
            }
            (width as i32, height as i32)
        }
        _ => {
            return Err(format!(
                "pillow_lanczos Image fit {:?} escaped validation",
                node.fit
            ));
        }
    };

    let available = interp.available_native_scene_bytes("pillow_lanczos Image resize")?;
    let resized = pillow_resize_buffer(
        &source.pixels,
        source_size,
        resized_size,
        interp.max_node_pixels,
        available,
        "pillow_lanczos Image resize",
    )?;
    let resized_bytes = resized.len();
    interp.push_native_runtime_bytes(resized_bytes, "pillow_lanczos Image resized raster")?;

    if node.fit == Fit::Stretch || resized_size == destination_size {
        return Ok((
            resized,
            destination_size.0,
            destination_size.1,
            placement.dst,
        ));
    }

    let crop_left = ((resized_size.0 - destination_size.0) as f64 * 0.5).round_ties_even() as i32;
    let crop_top = ((resized_size.1 - destination_size.1) as f64 * 0.5).round_ties_even() as i32;
    let crop_bytes = rgba_byte_len(
        destination_size.0,
        destination_size.1,
        "pillow_lanczos cover crop",
    )?;
    interp.push_native_runtime_bytes(crop_bytes, "pillow_lanczos cover crop")?;
    let cropped = crop_rgba8(
        &resized,
        resized_size,
        crop_left,
        crop_top,
        destination_size,
    )?;
    drop(resized);
    interp.pop_native_runtime_bytes(resized_bytes);
    Ok((
        cropped,
        destination_size.0,
        destination_size.1,
        placement.dst,
    ))
}

fn draw_pillow_lanczos_image(
    canvas: &Canvas,
    interp: &mut Interp,
    node: &ImageNode,
    off: (f32, f32),
) -> Result<(), String> {
    let (pixels, width, height, dst) = rasterize_pillow_lanczos_image(interp, node, off)?;
    let pixel_bytes = pixels.len();
    interp.push_native_runtime_bytes(pixel_bytes, "pillow_lanczos Skia raster copy")?;
    let info = ImageInfo::new(
        (width, height),
        ColorType::RGBA8888,
        AlphaType::Unpremul,
        None,
    );
    let image =
        skia_safe::images::raster_from_data(&info, Data::new_copy(&pixels), width as usize * 4)
            .ok_or_else(|| "failed to build pillow_lanczos straight RGBA8 raster".to_string())?;
    drop(pixels);
    interp.pop_native_runtime_bytes(pixel_bytes);
    draw_image_placed(
        canvas,
        &image,
        ImagePlacement { src: None, dst },
        SamplingOptions::default(),
        node,
    );
    drop(image);
    interp.pop_native_runtime_bytes(pixel_bytes);
    Ok(())
}

/// Resolve and resize the source for Pillow's `paste(source, pos, source)` operation.
///
/// On success the returned straight-RGBA Vec owns one `active_native_runtime_bytes` charge,
/// which the caller must pop after it has completed the destination lerp.
fn rasterize_paste_lerp_source(
    interp: &mut Interp,
    node: &ImageNode,
    off: (f32, f32),
) -> Result<(Vec<u8>, i32, i32, Rect), String> {
    if node.sampling == ImageSampling::PillowLanczos {
        return rasterize_pillow_lanczos_image(interp, node, off);
    }

    let image = if node.path.starts_with("mem:") {
        interp.load_mem(&node.path).ok_or_else(|| {
            format!(
                "paste_lerp Image failed to decode mem source: {}",
                node.path
            )
        })?
    } else if let Some(prepared) = interp.direct_images.get(&node.path) {
        prepared.clone()
    } else {
        let (descriptor, source) = interp
            .describe_asset(&node.path)
            .map_err(|err| format!("paste_lerp Image asset load failed: {} ({err})", node.path))?;
        let started = Instant::now();
        let image = source
            .map(Ok)
            .unwrap_or_else(|| decode_asset_descriptor(&descriptor))
            .map_err(|err| {
                format!(
                    "paste_lerp Image asset decode failed: {} ({err})",
                    node.path
                )
            })?;
        interp.metrics.asset_load_elapsed += started.elapsed().as_secs_f64();
        image
    };
    let placement = image_placement(image.width(), image.height(), node, off)
        .ok_or_else(|| "paste_lerp Image source or destination is empty".to_string())?;
    let (width, height) = integral_target(placement.dst)
        .ok_or_else(|| "paste_lerp Image destination is not integral".to_string())?;
    let byte_count = validate_strict_asset_size(
        width,
        height,
        interp.max_node_pixels,
        "paste_lerp source raster",
    )?;
    let accounted_bytes = byte_count
        .checked_mul(2)
        .ok_or_else(|| "paste_lerp source raster byte count overflow".to_string())?;
    interp.push_native_runtime_bytes(accounted_bytes, "paste_lerp source raster and readback")?;
    let result = (|| {
        let raster =
            if placement.src.is_none() && image.width() == width && image.height() == height {
                image
            } else {
                let source = placement.src.unwrap_or_else(|| {
                    Rect::from_xywh(0.0, 0.0, image.width() as f32, image.height() as f32)
                });
                draw_source_to_raster(
                    &image,
                    source,
                    width,
                    height,
                    skia_image_sampling(node.sampling)
                        .ok_or_else(|| "unhandled pillow_lanczos paste_lerp Image".to_string())?,
                )?
            };
        let row_bytes = width as usize * 4;
        let mut pixels = Vec::new();
        pixels.try_reserve_exact(byte_count).map_err(|_| {
            format!("paste_lerp source readback allocation rejected: {width}x{height}")
        })?;
        pixels.resize(byte_count, 0);
        let info = ImageInfo::new(
            (width, height),
            ColorType::RGBA8888,
            AlphaType::Unpremul,
            None,
        );
        if !raster.read_pixels(&info, &mut pixels, row_bytes, (0, 0), CachingHint::Disallow) {
            return Err("failed to read paste_lerp source as straight RGBA8".to_string());
        }
        Ok((pixels, width, height, placement.dst))
    })();
    match result {
        Ok(result) => {
            // The temporary source raster is gone; leave only the returned Vec charged.
            interp.pop_native_runtime_bytes(byte_count);
            Ok(result)
        }
        Err(err) => {
            interp.pop_native_runtime_bytes(accounted_bytes);
            Err(err)
        }
    }
}

/// Pillow's integer `BLEND(mask, dst, src)` helper: rounded `(dst*(255-mask)+src*mask)/255`.
fn pillow_paste_lerp_byte(destination: u8, source: u8, mask: u8) -> u8 {
    let value =
        u32::from(destination) * (255 - u32::from(mask)) + u32::from(source) * u32::from(mask);
    let biased = value + 128;
    ((biased + (biased >> 8)) >> 8) as u8
}

/// Execute `destination.paste(source, pos, source)` over straight RGBA bytes.
///
/// The result image is drawn back with Porter-Duff Src so the active Group clip remains in
/// force. Both the source and destination are read from Skia rasters as unpremultiplied RGBA;
/// RGB hidden under alpha=0 has already been discarded by Skia's premultiplied surface model
/// and therefore cannot be preserved by this compatibility operation.
fn draw_paste_lerp_image(
    surface: &mut Surface,
    interp: &mut Interp,
    node: &ImageNode,
    off: (f32, f32),
) -> Result<(), String> {
    let (source, source_width, source_height, dst) =
        rasterize_paste_lerp_source(interp, node, off)?;
    let source_bytes = source.len();
    let result = (|| {
        let full_left = dst.left.round() as i32;
        let full_top = dst.top.round() as i32;
        let full_right = dst.right.round() as i32;
        let full_bottom = dst.bottom.round() as i32;
        debug_assert_eq!(full_right - full_left, source_width);
        debug_assert_eq!(full_bottom - full_top, source_height);

        let left = full_left.clamp(0, surface.width());
        let top = full_top.clamp(0, surface.height());
        let right = full_right.clamp(0, surface.width());
        let bottom = full_bottom.clamp(0, surface.height());
        if right <= left || bottom <= top {
            return Ok(());
        }
        let width = right - left;
        let height = bottom - top;
        let visible_bytes = rgba_byte_len(width, height, "paste_lerp visible destination")?;
        let transient_bytes = visible_bytes
            .checked_mul(2)
            .ok_or_else(|| "paste_lerp visible destination byte count overflow".to_string())?;
        interp.push_native_runtime_bytes(
            transient_bytes,
            "paste_lerp destination readback and Skia raster copy",
        )?;
        let visible_result = (|| {
            let row_bytes = width as usize * 4;
            let mut output = Vec::new();
            output.try_reserve_exact(visible_bytes).map_err(|_| {
                format!("paste_lerp destination readback allocation rejected: {width}x{height}")
            })?;
            output.resize(visible_bytes, 0);
            let info = ImageInfo::new(
                (width, height),
                ColorType::RGBA8888,
                AlphaType::Unpremul,
                None,
            );
            if !surface.read_pixels(&info, &mut output, row_bytes, (left, top)) {
                return Err("failed to read paste_lerp destination as straight RGBA8".to_string());
            }

            let source_x = usize::try_from(left - full_left)
                .map_err(|_| "paste_lerp source x offset overflow".to_string())?;
            let source_y = usize::try_from(top - full_top)
                .map_err(|_| "paste_lerp source y offset overflow".to_string())?;
            let source_stride = source_width as usize * 4;
            for y in 0..height as usize {
                for x in 0..width as usize {
                    let output_offset = (y * width as usize + x) * 4;
                    let source_offset = (source_y + y) * source_stride + (source_x + x) * 4;
                    let mask = source[source_offset + 3];
                    for channel in 0..4 {
                        output[output_offset + channel] = pillow_paste_lerp_byte(
                            output[output_offset + channel],
                            source[source_offset + channel],
                            mask,
                        );
                    }
                }
            }

            let image =
                skia_safe::images::raster_from_data(&info, Data::new_copy(&output), row_bytes)
                    .ok_or_else(|| {
                        "failed to build paste_lerp straight RGBA8 raster".to_string()
                    })?;
            let mut paint = Paint::default();
            paint.set_blend_mode(BlendMode::Src);
            paint.set_anti_alias(false);
            surface.canvas().draw_image_with_sampling_options(
                &image,
                (left as f32, top as f32),
                SamplingOptions::default(),
                Some(&paint),
            );
            Ok(())
        })();
        interp.pop_native_runtime_bytes(transient_bytes);
        visible_result
    })();
    drop(source);
    interp.pop_native_runtime_bytes(source_bytes);
    result
}

fn draw_image_node(
    canvas: &Canvas,
    interp: &mut Interp,
    node: &ImageNode,
    off: (f32, f32),
) -> Result<(), String> {
    if node.sampling == ImageSampling::PillowLanczos {
        interp.metrics.raster_cache_bypasses += 1;
        return draw_pillow_lanczos_image(canvas, interp, node, off);
    }
    if node.path.starts_with("mem:") {
        interp.metrics.raster_cache_bypasses += 1;
        let Some(image) = interp.load_mem(&node.path) else {
            if interp.strict_asset_depth > 0 {
                return Err(format!(
                    "UnitySubscene mem image was not prepared or decoded: {}",
                    node.path
                ));
            }
            return Ok(());
        };
        if let Some(placement) = image_placement(image.width(), image.height(), node, off) {
            draw_image_placed(
                canvas,
                &image,
                placement,
                skia_image_sampling(node.sampling)
                    .ok_or_else(|| "unhandled pillow_lanczos Image".to_string())?,
                node,
            );
        }
        return Ok(());
    }

    let prepared_source = interp.direct_images.get(&node.path).cloned();
    let (descriptor, source) = match interp.describe_asset(&node.path) {
        Ok(loaded) => loaded,
        Err(err) => {
            if interp.strict_asset_depth > 0 {
                return Err(format!(
                    "UnitySubscene asset load failed during draw: {} ({err})",
                    node.path
                ));
            }
            eprintln!(
                "haruki_skia_renderer: asset load failed, node skipped: {} ({err})",
                node.path
            );
            return Ok(());
        }
    };
    let source = prepared_source.or(source);
    let Some(placement) = image_placement(descriptor.width, descriptor.height, node, off) else {
        return Ok(());
    };
    let sampling = skia_image_sampling(node.sampling)
        .ok_or_else(|| "unhandled pillow_lanczos Image".to_string())?;

    // Inside a Transform the CTM is non-identity: the raster cache pre-rasterizes at the
    // integral dst size and drawing that intermediate would resample it a SECOND time through
    // the CTM. Sampling must happen exactly once (source pixels -> device through the matrix),
    // so skip the cache and draw the decoded source directly.
    if interp.in_transform || interp.strict_asset_depth > 0 {
        interp.metrics.raster_cache_bypasses += 1;
    } else if let Some((width, height)) = integral_target(placement.dst) {
        let src = placement.src.unwrap_or_else(|| {
            Rect::from_xywh(0.0, 0.0, descriptor.width as f32, descriptor.height as f32)
        });
        let started = Instant::now();
        match rasterize_asset_cached(
            &descriptor,
            source.as_ref(),
            src,
            width,
            height,
            sampling,
            sampling_key(node.sampling),
        ) {
            Ok(Some(cached)) => {
                let elapsed = started.elapsed().as_secs_f64();
                match cached.outcome {
                    RasterCacheOutcome::Hit => interp.metrics.raster_cache_hits += 1,
                    RasterCacheOutcome::Miss => {
                        interp.metrics.raster_cache_misses += 1;
                        interp.metrics.raster_cache_build_elapsed += elapsed;
                    }
                    RasterCacheOutcome::Coalesced => {
                        interp.metrics.raster_cache_coalesced += 1;
                        interp.metrics.raster_cache_wait_elapsed += elapsed;
                    }
                }
                draw_image_placed(
                    canvas,
                    &cached.image,
                    ImagePlacement {
                        src: None,
                        dst: placement.dst,
                    },
                    if cached.image.width() == width && cached.image.height() == height {
                        SamplingOptions::default()
                    } else {
                        SamplingOptions::new(FilterMode::Linear, MipmapMode::None)
                    },
                    node,
                );
                return Ok(());
            }
            Ok(None) => interp.metrics.raster_cache_bypasses += 1,
            Err(err) => eprintln!(
                "haruki_skia_renderer: target raster cache failed, drawing source directly: {} ({err})",
                node.path
            ),
        }
    } else {
        interp.metrics.raster_cache_bypasses += 1;
    }

    let started = Instant::now();
    let decoded = if source.is_none() {
        match decode_asset_descriptor(&descriptor) {
            Ok(image) => Some(image),
            Err(err) => {
                if interp.strict_asset_depth > 0 {
                    return Err(format!(
                        "UnitySubscene asset decode failed during draw: {} ({err})",
                        node.path
                    ));
                }
                eprintln!(
                    "haruki_skia_renderer: asset decode failed, node skipped: {} ({err})",
                    node.path
                );
                return Ok(());
            }
        }
    } else {
        None
    };
    interp.metrics.asset_load_elapsed += started.elapsed().as_secs_f64();
    let image = source
        .as_ref()
        .or(decoded.as_ref())
        .expect("source image available");
    draw_image_placed(canvas, image, placement, sampling, node);
    Ok(())
}

fn draw_image_placed(
    canvas: &Canvas,
    image: &Image,
    placement: ImagePlacement,
    sampling: SamplingOptions,
    node: &ImageNode,
) {
    let src = placement.src;
    let dst = placement.dst;
    let src_arg = src.as_ref().map(|s| (s, SrcRectConstraint::Strict));
    let alpha = node.alpha.clamp(0.0, 1.0);

    // Alpha-silhouette drop shadow, drawn behind the image (mirrors Painter paste shadow).
    if let Some(sh) = &node.shadow {
        let mut shadow_paint = Paint::default();
        shadow_paint.set_anti_alias(true);
        let strength =
            (sh.alpha.clamp(0.0, 1.0) * (sh.color[3] as f32 / 255.0) * alpha).clamp(0.0, 1.0);
        shadow_paint.set_alpha_f(strength);
        // Recolor every covered pixel to the shadow color, keeping the image's alpha mask.
        shadow_paint.set_color_filter(color_filters::blend(
            Color::from_argb(255, sh.color[0], sh.color[1], sh.color[2]),
            BlendMode::SrcIn,
        ));
        shadow_paint.set_image_filter(image_filters::blur(
            (sh.sigma.max(0.0), sh.sigma.max(0.0)),
            TileMode::Decal,
            None,
            None,
        ));
        let sdst = Rect::from_xywh(
            dst.left + sh.offset[0],
            dst.top + sh.offset[1],
            dst.width(),
            dst.height(),
        );
        canvas.draw_image_rect_with_sampling_options(image, src_arg, sdst, sampling, &shadow_paint);
    }

    let mut paint = Paint::default();
    paint.set_anti_alias(true);
    paint.set_alpha_f(alpha);
    if let Some(tint) = &node.tint {
        paint.set_color_filter(tint_filter(tint));
    }
    let has_blur = node.blur_sigma[0] > 0.0 || node.blur_sigma[1] > 0.0;
    if has_blur {
        paint.set_image_filter(image_filters::blur(
            (node.blur_sigma[0].max(0.0), node.blur_sigma[1].max(0.0)),
            TileMode::Clamp,
            None,
            None,
        ));
    }
    if node.blend == ImageBlend::Src {
        // Replace the destination rather than compositing over it, so `Painter.paste_src` means
        // the same thing on both backends. Anti-aliasing must be off: an AA edge under kSrc would
        // write partially-transparent pixels OUTSIDE the source's own coverage.
        paint.set_blend_mode(BlendMode::Src);
        paint.set_anti_alias(false);
    }
    let save_count = if has_blur {
        // Pillow filters the finite source image and then pastes the finite result. Skia image
        // filters can expand their output beyond the destination bounds, so clip that halo away
        // to keep a blurred nested WidgetBg from leaking outside its own image rectangle.
        let count = canvas.save();
        canvas.clip_rect(dst, ClipOp::Intersect, false);
        Some(count)
    } else {
        None
    };
    canvas.draw_image_rect_with_sampling_options(image, src_arg, dst, sampling, &paint);
    if let Some(count) = save_count {
        canvas.restore_to_count(count);
    }
}

/// Parse a Painter-style align string into (h, v) where h ∈ {-1,0,1} (l/c/r) and
/// v ∈ {-1,0,1} (t/c/b). Unknown chars default to centered.
fn parse_bg_align(align: &str) -> (i8, i8) {
    let h = if align.contains('l') {
        -1
    } else if align.contains('r') {
        1
    } else {
        0
    };
    let v = if align.contains('t') {
        -1
    } else if align.contains('b') {
        1
    } else {
        0
    };
    (h, v)
}

fn align_offset(axis: i8, container: f32, content: f32) -> f32 {
    match axis {
        -1 => 0.0,
        1 => container - content,
        _ => (container - content) * 0.5,
    }
}

fn draw_image_bg(canvas: &Canvas, image: &Image, cw: f32, ch: f32, node: &ImageBgNode) {
    let iw = image.width() as f32;
    let ih = image.height() as f32;
    if iw <= 0.0 || ih <= 0.0 {
        return;
    }
    let mut paint = Paint::default();
    paint.set_anti_alias(true);
    if node.blur {
        paint.set_image_filter(image_filters::blur((3.0, 3.0), TileMode::Clamp, None, None));
    }
    if node.fade > 0.0 {
        let m = ((1.0 - node.fade).clamp(0.0, 1.0) * 255.0).round() as u8;
        paint.set_color_filter(color_filters::lighting(
            Color::from_rgb(m, m, m),
            Color::from_rgb(0, 0, 0),
        ));
    }
    let (ha, va) = parse_bg_align(&node.align);
    let sampling =
        skia_image_sampling(ImageSampling::default()).expect("default image sampling is Skia");
    match node.mode {
        BgMode::Fit => {
            let scale = (cw / iw).max(ch / ih);
            let w = iw * scale;
            let h = ih * scale;
            let x = align_offset(ha, cw, w);
            let y = align_offset(va, ch, h);
            let dst = Rect::from_xywh(x, y, w, h);
            canvas.draw_image_rect_with_sampling_options(image, None, dst, sampling, &paint);
        }
        BgMode::Fill => {
            let dst = Rect::from_xywh(0.0, 0.0, cw, ch);
            canvas.draw_image_rect_with_sampling_options(image, None, dst, sampling, &paint);
        }
        BgMode::Fixed => {
            let x = align_offset(ha, cw, iw);
            let y = align_offset(va, ch, ih);
            let dst = Rect::from_xywh(x, y, iw, ih);
            canvas.draw_image_rect_with_sampling_options(image, None, dst, sampling, &paint);
        }
        BgMode::Repeat => {
            let mut y = 0.0;
            while y < ch {
                let mut x = 0.0;
                while x < cw {
                    let dst = Rect::from_xywh(x, y, iw, ih);
                    canvas
                        .draw_image_rect_with_sampling_options(image, None, dst, sampling, &paint);
                    x += iw;
                }
                y += ih;
            }
        }
    }
}

/// Build a color filter for an image tint (multiply or alpha-weighted mix).
fn tint_filter(tint: &Tint) -> Option<skia_safe::ColorFilter> {
    let c = tint.color;
    match tint.mode {
        // Modulate = component-wise multiply (image_px * color/255).
        TintMode::Multiply => color_filters::blend(
            Color::from_argb(c[3], c[0], c[1], c[2]),
            BlendMode::Modulate,
        ),
        // Lerp RGB toward the color by `strength`, alpha untouched (img_utils.mix_image_by_color:
        // RGB' = RGB*(1-f) + C*f). A color matrix on unpremul RGBA does exactly this and, unlike
        // a SrcOver blend filter, leaves fully-transparent pixels transparent.
        TintMode::Mix => {
            let f = tint.strength.clamp(0.0, 1.0);
            let k = 1.0 - f;
            #[rustfmt::skip]
            let m = skia_safe::ColorMatrix::new(
                k, 0.0, 0.0, 0.0, f * c[0] as f32 / 255.0,
                0.0, k, 0.0, 0.0, f * c[1] as f32 / 255.0,
                0.0, 0.0, k, 0.0, f * c[2] as f32 / 255.0,
                0.0, 0.0, 0.0, 1.0, 0.0,
            );
            Some(color_filters::matrix(&m, None))
        }
        // SrcIn = keep the source alpha as a stencil, replace RGB with `color`. `color`'s
        // alpha scales the result alpha (255 keeps the source mask unchanged).
        TintMode::Recolor => {
            color_filters::blend(Color::from_argb(c[3], c[0], c[1], c[2]), BlendMode::SrcIn)
        }
    }
}

/// Total advance of `text` (with the emoji font for emoji runs) including `letter_spacing`.
fn measure_advance(main: &Font, emoji: Option<&Font>, text: &str, letter_spacing: f32) -> f32 {
    let has_emoji = text.chars().any(|ch| routes_to_emoji(ch, emoji));
    // Fast path: plain text, no spacing, no emoji routing — a single measure_str.
    if !has_emoji && letter_spacing == 0.0 {
        return main.measure_str(text, None).0;
    }
    if letter_spacing == 0.0 {
        return classify_runs(text, emoji)
            .iter()
            .map(|(e, run)| run_font(*e, main, emoji).measure_str(run, None).0)
            .sum();
    }
    let mut total = 0.0;
    let mut count = 0;
    for (e, run) in classify_runs(text, emoji) {
        let font = run_font(e, main, emoji);
        for ch in run.chars() {
            let mut buf = [0u8; 4];
            total += font.measure_str(ch.encode_utf8(&mut buf), None).0;
            count += 1;
        }
    }
    total + letter_spacing * (count.max(1) - 1) as f32
}

/// Resolve the draw origin (left x, baseline y) for a text run.
fn text_layout(
    main: &Font,
    emoji: Option<&Font>,
    text: &str,
    abs: (f32, f32),
    align: HAlign,
    baseline: Baseline,
    letter_spacing: f32,
) -> (f32, f32) {
    // Measure lazily: Left — the common case — places at `abs.0` and never looks at the advance,
    // so measuring up front was a full text measurement thrown away on most nodes in the scene.
    let x = match align {
        HAlign::Left => abs.0,
        HAlign::Center => abs.0 - measure_advance(main, emoji, text, letter_spacing) * 0.5,
        HAlign::Right => abs.0 - measure_advance(main, emoji, text, letter_spacing),
    };
    let (_, metrics) = main.metrics();
    let baseline_y = match baseline {
        // Match Painter._text: baseline at pos.y + ink height of the CJK reference glyph '哇'.
        Baseline::CjkTop => abs.1 + main.measure_str("哇", None).1.height(),
        Baseline::Ascender => abs.1 - metrics.ascent,
        Baseline::Alphabetic => abs.1,
    };
    (x, baseline_y)
}

/// Draw a text run with an arbitrary paint; single blob when plain, else per-run/per-glyph
/// so emoji codepoints route to the emoji font and letter spacing applies.
#[allow(clippy::too_many_arguments)]
fn draw_text_core(
    canvas: &Canvas,
    main: &Font,
    emoji: Option<&Font>,
    text: &str,
    x: f32,
    y: f32,
    letter_spacing: f32,
    paint: &Paint,
) {
    let has_emoji = text.chars().any(|ch| routes_to_emoji(ch, emoji));
    if !has_emoji && letter_spacing == 0.0 {
        if let Some(blob) = TextBlob::new(text, main) {
            canvas.draw_text_blob(&blob, Point::new(x, y), paint);
        }
        return;
    }
    let mut cx = x;
    for (e, run) in classify_runs(text, emoji) {
        let font = run_font(e, main, emoji);
        // Coverage calibration targets monochrome Source Han glyph masks. Color emoji
        // (CoreText OT-SVG on macOS, FreeType COLR on Linux) must retain native alpha.
        let emoji_paint = e.then(|| {
            let mut paint = paint.clone();
            paint.set_mask_filter(Option::<MaskFilter>::None);
            paint
        });
        let run_paint = emoji_paint.as_ref().unwrap_or(paint);
        if letter_spacing == 0.0 {
            if let Some(blob) = TextBlob::new(&run, font) {
                canvas.draw_text_blob(&blob, Point::new(cx, y), run_paint);
            }
            cx += font.measure_str(&run, None).0;
        } else {
            for ch in run.chars() {
                let mut buf = [0u8; 4];
                let s: &str = ch.encode_utf8(&mut buf);
                if let Some(blob) = TextBlob::new(s, font) {
                    canvas.draw_text_blob(&blob, Point::new(cx, y), run_paint);
                }
                cx += font.measure_str(s, None).0 + letter_spacing;
            }
        }
    }
}

/// Draw a `TextNode`: optional outline under the fill (solid or gradient), with letter spacing
/// and emoji-font routing.
fn draw_styled_text(
    canvas: &Canvas,
    fonts: &FontRegistry,
    node: &TextNode,
    abs: (f32, f32),
    off: (f32, f32),
    fill: &Fill,
) {
    if node.text.is_empty() {
        return;
    }
    let font = configured_text_font(fonts.resolve_ref(&node.font).clone(), node.font.size);
    let emoji = fonts.emoji_font_for(&node.text, node.font.size);
    let emoji_ref = emoji.as_ref();
    let (x, y) = text_layout(
        &font,
        emoji_ref,
        &node.text,
        abs,
        node.align,
        node.baseline,
        node.letter_spacing,
    );

    if let Some(stroke) = &node.stroke {
        let mut sp = Paint::default();
        sp.set_anti_alias(true);
        sp.set_style(PaintStyle::Stroke);
        sp.set_stroke_width(stroke.width);
        sp.set_color(color_of(stroke.color));
        apply_text_coverage_gamma(&mut sp);
        draw_text_core(
            canvas,
            &font,
            emoji_ref,
            &node.text,
            x,
            y,
            node.letter_spacing,
            &sp,
        );
    }

    let mut fp = Paint::default();
    fp.set_anti_alias(true);
    apply_fill(&mut fp, fill, off);
    apply_text_coverage_gamma(&mut fp);
    draw_text_core(
        canvas,
        &font,
        emoji_ref,
        &node.text,
        x,
        y,
        node.letter_spacing,
        &fp,
    );
}

/// Pick the adaptive fill color from the average luminance of the backdrop under the text box.
fn resolve_adaptive_color(
    surface: &mut Surface,
    fonts: &FontRegistry,
    node: &TextNode,
    abs: (f32, f32),
    ad: &AdaptiveColor,
) -> Color4 {
    let font = configured_text_font(fonts.resolve_ref(&node.font).clone(), node.font.size);
    let emoji = fonts.emoji_font_for(&node.text, node.font.size);
    let emoji_ref = emoji.as_ref();
    let (x, y) = text_layout(
        &font,
        emoji_ref,
        &node.text,
        abs,
        node.align,
        node.baseline,
        node.letter_spacing,
    );
    let advance = measure_advance(&font, emoji_ref, &node.text, node.letter_spacing);
    let (_, metrics) = font.metrics();
    // Text ink box: x..x+advance vertically spanning ascent..descent around the baseline.
    let mut bounds = Rect::new(x, y + metrics.ascent, x + advance, y + metrics.descent);
    let canvas_rect = Rect::from_xywh(0.0, 0.0, surface.width() as f32, surface.height() as f32);
    let lum = if bounds.intersect(canvas_rect) {
        let ibounds: IRect = bounds.round_out();
        surface
            .image_snapshot_with_bounds(ibounds)
            .and_then(|img| average_luminance(&img))
            .unwrap_or(1.0)
    } else {
        1.0
    };
    // Dark backdrop (low luminance) -> light text; bright backdrop -> dark text.
    if lum < ad.threshold {
        ad.light
    } else {
        ad.dark
    }
}

/// Painter's pixelwise adaptive text (painter.py:1099-1107): box-blur the backdrop, threshold
/// its luma per pixel, and paste the dark-text overlay over the light-text overlay through the
/// resulting mask (mask semantics replace pixels, they do not blend). Implemented with layers:
/// draw light text into a layer, punch out the mask region (DstOut), then composite dark text
/// clipped to the mask (nested layer + DstIn).
fn draw_pixelwise_adaptive_text(
    surface: &mut Surface,
    fonts: &FontRegistry,
    node: &TextNode,
    abs: (f32, f32),
    off: (f32, f32),
    ad: &AdaptiveColor,
) {
    let font = configured_text_font(fonts.resolve_ref(&node.font).clone(), node.font.size);
    let emoji = fonts.emoji_font_for(&node.text, node.font.size);
    let emoji_ref = emoji.as_ref();
    let (x, y) = text_layout(
        &font,
        emoji_ref,
        &node.text,
        abs,
        node.align,
        node.baseline,
        node.letter_spacing,
    );
    let advance = measure_advance(&font, emoji_ref, &node.text, node.letter_spacing);
    let (_, metrics) = font.metrics();
    let mut bounds = Rect::new(x, y + metrics.ascent, x + advance, y + metrics.descent);
    let canvas_rect = Rect::from_xywh(0.0, 0.0, surface.width() as f32, surface.height() as f32);
    let mask = if bounds.intersect(canvas_rect) {
        let ibounds: IRect = bounds.round_out();
        surface
            .image_snapshot_with_bounds(ibounds)
            .and_then(|img| pixelwise_dark_mask(&img, ad.threshold))
            .map(|mask| (mask, ibounds))
    } else {
        None
    };
    let Some((mask, ibounds)) = mask else {
        // No usable backdrop: fall back to the whole-run average path.
        let color = resolve_adaptive_color(surface, fonts, node, abs, ad);
        draw_styled_text(surface.canvas(), fonts, node, abs, off, &Fill::Solid(color));
        return;
    };
    let mask_rect = Rect::from_irect(ibounds);
    let canvas = surface.canvas();
    let layer = skia_safe::canvas::SaveLayerRec::default().bounds(&mask_rect);
    canvas.save_layer(&layer);
    draw_styled_text(canvas, fonts, node, abs, off, &Fill::Solid(ad.light));
    let mut erase = Paint::default();
    erase.set_blend_mode(BlendMode::DstOut);
    canvas.draw_image_rect(&mask, None, mask_rect, &erase);
    canvas.save_layer(&layer);
    draw_styled_text(canvas, fonts, node, abs, off, &Fill::Solid(ad.dark));
    let mut keep = Paint::default();
    keep.set_blend_mode(BlendMode::DstIn);
    canvas.draw_image_rect(&mask, None, mask_rect, &keep);
    canvas.restore();
    canvas.restore();
}

/// Opaque-white-where-dark-text-applies mask: blur the backdrop like PIL BoxBlur(8)
/// (equivalent gaussian sigma ~= sqrt((17^2 - 1) / 12)) and threshold its 601 luma.
fn pixelwise_dark_mask(backdrop: &Image, threshold: f32) -> Option<Image> {
    let w = backdrop.width().max(1);
    let h = backdrop.height().max(1);
    let mut blur_surface = surfaces::raster_n32_premul((w, h))?;
    let mut blur_paint = Paint::default();
    let sigma = (17.0_f32 * 17.0 - 1.0).sqrt() / 12.0_f32.sqrt();
    blur_paint.set_image_filter(image_filters::blur(
        (sigma, sigma),
        TileMode::Clamp,
        None,
        None,
    ));
    blur_surface
        .canvas()
        .draw_image(backdrop, (0, 0), Some(&blur_paint));
    let blurred = blur_surface.image_snapshot();
    let info = ImageInfo::new((w, h), ColorType::RGBA8888, AlphaType::Unpremul, None);
    let row = (w as usize) * 4;
    let mut buf = vec![0u8; row * h as usize];
    if !blurred.read_pixels(&info, &mut buf, row, (0, 0), CachingHint::Allow) {
        return None;
    }
    let cut = threshold * 255.0;
    for px in buf.chunks_exact_mut(4) {
        let lum = 0.299 * px[0] as f32 + 0.587 * px[1] as f32 + 0.114 * px[2] as f32;
        let v = if lum > cut { 255 } else { 0 };
        px.copy_from_slice(&[v, v, v, v]);
    }
    let mask_info = ImageInfo::new((w, h), ColorType::RGBA8888, AlphaType::Premul, None);
    skia_safe::images::raster_from_data(&mask_info, skia_safe::Data::new_copy(&buf), row)
}

/// Average relative luminance (0..1) of an image's pixels, or None if the read fails.
fn average_luminance(image: &Image) -> Option<f32> {
    let w = image.width().max(1);
    let h = image.height().max(1);
    let info = ImageInfo::new((w, h), ColorType::RGBA8888, AlphaType::Unpremul, None);
    let row = (w as usize) * 4;
    let mut buf = vec![0u8; row * h as usize];
    if !image.read_pixels(&info, &mut buf, row, (0, 0), CachingHint::Allow) {
        return None;
    }
    let mut sum = 0.0_f64;
    let mut count = 0u64;
    for px in buf.chunks_exact(4) {
        sum += 0.299 * px[0] as f64 + 0.587 * px[1] as f64 + 0.114 * px[2] as f64;
        count += 1;
    }
    if count == 0 {
        return None;
    }
    Some((sum / count as f64 / 255.0) as f32)
}

#[cfg(test)]
mod tests {
    use super::*;

    fn scene_json(extra_root: &str) -> String {
        format!(
            r#"{{
                "version": 2,
                "assets_base_dir": "/tmp/does-not-matter",
                "export_format": "png",
                "fonts": {{ "dir": "/tmp", "default": "missing", "bold": "missing" }},
                "canvas": {{ "width": 64, "height": 48 }},
                "background": {{ "type": "TriangleBg", "hour": 15.5 }},
                "root": {{ "type": "Group", "offset": [0, 0], "size": [64, 48], "children": [{extra_root}] }}
            }}"#
        )
    }

    fn render(json: &str) -> RenderedImage {
        let scene: Scene = serde_json::from_str(json).expect("scene parses");
        render_scene_inner(&scene, HashMap::new()).expect("renders")
    }

    #[test]
    fn renders_shapes_scene_to_png() {
        let json = scene_json(
            r#"
            { "type": "Rect", "pos": [4, 4], "size": [20, 20], "fill": [255, 0, 0, 255] },
            { "type": "RoundRect", "pos": [28, 4], "size": [20, 20], "radius": 6,
              "fill": { "kind": "linear", "c1": [255,255,255,255], "c2": [0,0,255,255],
                        "p1": [28,4], "p2": [48,24] } },
            { "type": "PieSlice", "pos": [4, 26], "size": [18, 18],
              "start_angle": 0, "end_angle": 120, "fill": [0, 200, 0, 255] },
            { "type": "Text", "text": "Hi", "pos": [26, 28], "font": { "role": "default", "size": 14 },
              "align": "left", "baseline": "cjk_top", "fill": [0, 0, 0, 255] }
            "#,
        );
        let rendered = render(&json);
        assert_eq!(rendered.width, 64);
        assert_eq!(rendered.height, 48);
        // PNG signature.
        assert_eq!(
            &rendered.bytes.as_bytes()[..8],
            &[0x89, b'P', b'N', b'G', 0x0d, 0x0a, 0x1a, 0x0a]
        );
    }

    #[test]
    fn rect_src_blend_replaces_destination_and_rejects_unknown_values() {
        let json = scene_json(
            r#"
            { "type": "Rect", "pos": [0, 0], "size": [64, 48],
              "fill": [255, 0, 0, 255] },
            { "type": "Rect", "pos": [4, 4], "size": [20, 20],
              "fill": [0, 0, 255, 128] },
            { "type": "Rect", "pos": [28, 4], "size": [20, 20],
              "fill": [0, 0, 255, 128], "blend": "src" }
            "#,
        );
        let rendered = render(&json);
        let (pixels, width, _) = decode_pixels(&rendered);
        let pixel = |x: usize, y: usize| &pixels[(y * width as usize + x) * 4..][..4];

        assert_eq!(
            pixel(10, 10)[3],
            255,
            "SrcOver must retain opaque destination alpha"
        );
        assert_eq!(
            pixel(34, 10),
            &[0, 0, 255, 128],
            "Src must replace all four destination channels"
        );

        let invalid = scene_json(
            r#"{ "type": "Rect", "pos": [0, 0], "size": [1, 1],
                 "fill": [0, 0, 0, 0], "blend": "multiply" }"#,
        );
        let error = serde_json::from_str::<Scene>(&invalid).expect_err("unknown blend must fail");
        assert!(
            error.to_string().contains("unknown variant `multiply`"),
            "unexpected parse error: {error}"
        );
    }

    #[test]
    fn skips_backdrop_work_for_zero_blur_glass() {
        let json = scene_json(
            r#"
            { "type": "BlurGlass", "pos": [4, 4], "size": [40, 24], "radius": 6,
              "fill": [255, 255, 255, 80], "shadow_alpha": 0.2, "blur": 0 }
            "#,
        );
        let rendered = render(&json);
        assert_eq!(rendered.metrics.zero_blur_fast_paths, 1);
    }

    #[test]
    fn renders_gradient_variants_scene() {
        // Multi-stop linear fill, radial fill, and a gradient stroke + per-corner radii.
        let json = scene_json(
            r#"
            { "type": "Rect", "pos": [2, 2], "size": [28, 20],
              "fill": { "kind": "linear", "p1": [2,2], "p2": [30,2],
                        "stops": [{"color":[255,0,0,255],"pos":0.0},
                                  {"color":[0,255,0,255],"pos":0.5},
                                  {"color":[0,0,255,255],"pos":1.0}] } },
            { "type": "RoundRect", "pos": [34, 2], "size": [24, 24], "radius": 0,
              "corner_radii": [10, 0, 10, 0],
              "fill": { "kind": "radial", "c1": [0,0,0,255], "c2": [255,255,255,255],
                        "center": [46,14], "radius_px": 12 },
              "stroke": { "kind": "linear", "p1": [34,2], "p2": [58,26],
                          "c1": [255,255,0,255], "c2": [255,0,255,255] },
              "stroke_width": 2 }
            "#,
        );
        let rendered = render(&json);
        assert_eq!(rendered.width, 64);
        assert_eq!(
            &rendered.bytes.as_bytes()[..8],
            &[0x89, b'P', b'N', b'G', 0x0d, 0x0a, 0x1a, 0x0a]
        );
    }

    #[test]
    fn parses_image_extensions() {
        // tint + alpha-silhouette shadow + crop fit + source-rect + recolor tint must
        // deserialize and render (the asset is absent in the test base dir, so the image is
        // skipped, but parsing must succeed).
        let json = scene_json(
            r#"
            { "type": "Image", "pos": [4, 4], "size": [20, 20], "path": "missing.png",
              "fit": "crop",
              "sampling": "cubic",
              "source_rect": [2, 2, 40, 40],
              "blur_sigma": [3.0, 1.5],
              "tint": { "color": [255, 128, 0, 255], "mode": "multiply" },
              "shadow": { "alpha": 0.6, "offset": [4, 4], "sigma": 3.0, "color": [0,0,0,255] } },
            { "type": "Image", "pos": [30, 4], "size": [20, 20], "path": "missing.png",
              "fit": "width", "sampling": "nearest", "source_rect": [0, 0, 16, 16],
              "tint": { "color": [255, 32, 32, 255], "mode": "recolor" } }
            "#,
        );
        let rendered = render(&json);
        assert_eq!(rendered.width, 64);
    }

    #[test]
    fn maps_image_sampling_modes() {
        let nearest = skia_image_sampling(ImageSampling::Nearest).expect("Skia sampler");
        assert_eq!(nearest.filter, FilterMode::Nearest);
        assert_eq!(nearest.mipmap, MipmapMode::None);

        let linear = skia_image_sampling(ImageSampling::Linear).expect("Skia sampler");
        assert_eq!(linear.filter, FilterMode::Linear);
        assert_eq!(linear.mipmap, MipmapMode::None);

        let cubic = skia_image_sampling(ImageSampling::Cubic).expect("Skia sampler");
        assert!(cubic.use_cubic);
        // "cubic" stays Mitchell (B = C = 1/3) — it must not be repurposed as Catmull-Rom.
        assert_eq!(cubic.cubic.b, 1.0 / 3.0);
        assert_eq!(cubic.cubic.c, 1.0 / 3.0);

        let catmull = skia_image_sampling(ImageSampling::CatmullRom).expect("Skia sampler");
        assert!(catmull.use_cubic);
        // Catmull-Rom = Keys a=-0.5 (PIL BICUBIC): B = 0, C = 0.5.
        assert_eq!(catmull.cubic.b, 0.0);
        assert_eq!(catmull.cubic.c, 0.5);

        let parsed: ImageSampling =
            serde_json::from_str("\"catmull_rom\"").expect("catmull_rom variant parses");
        assert_eq!(parsed, ImageSampling::CatmullRom);

        let pillow: ImageSampling =
            serde_json::from_str("\"pillow_lanczos\"").expect("pillow_lanczos variant parses");
        assert_eq!(pillow, ImageSampling::PillowLanczos);
        assert!(
            skia_image_sampling(pillow).is_none(),
            "Pillow Lanczos must never degrade to a Skia cubic sampler"
        );

        let mipmap = skia_image_sampling(ImageSampling::LinearMipmap).expect("Skia sampler");
        assert_eq!(mipmap.filter, FilterMode::Linear);
        assert_eq!(mipmap.mipmap, MipmapMode::Linear);
    }

    #[test]
    fn deduplicates_nested_image_prewarm_requests() {
        let json = scene_json(
            r#"
            { "type": "Image", "pos": [4, 4], "size": [20, 20], "path": "same.png" },
            { "type": "Group", "offset": [10, 0], "size": [20, 20], "children": [
                { "type": "Image", "pos": [4, 4], "size": [20, 20], "path": "same.png" }
              ] },
            { "type": "Image", "pos": [4, 28], "size": [24, 20], "path": "same.png" },
            { "type": "Image", "pos": [30, 28], "size": [20, 20], "path": "mem:runtime" },
            { "type": "Transform", "matrix": [1, 0, 0, 0, 1, 0], "children": [
                { "type": "Image", "pos": [0, 0], "size": [20, 20], "path": "other.png" }
              ] }
            "#,
        );
        let scene: Scene = serde_json::from_str(&json).expect("scene parses");
        let mut seen = HashSet::new();
        let mut requests = Vec::new();
        collect_image_prewarm_requests(&scene.root, (0.0, 0.0), &mut seen, &mut requests);

        // "other.png" sits under a Transform and must NOT be collected: its prewarm target
        // size is meaningless under a non-identity CTM and the draw path skips the cache.
        assert_eq!(requests.len(), 2);
        assert_eq!(requests[0].node.path, "same.png");
        assert_eq!(requests[1].node.size, [24.0, 20.0]);
    }

    #[test]
    fn renders_styled_text_scene() {
        // Gradient fill + outline + letter spacing, and an adaptive-color line.
        let json = scene_json(
            r#"
            { "type": "Text", "text": "Hi", "pos": [4, 10], "font": { "role": "default", "size": 16 },
              "fill": { "kind": "linear", "p1": [4,10], "p2": [40,10],
                        "c1": [255,0,0,255], "c2": [0,0,255,255] },
              "stroke": { "color": [0,0,0,255], "width": 2 }, "letter_spacing": 3 },
            { "type": "Text", "text": "Yo", "pos": [4, 30], "font": { "role": "default", "size": 14 },
              "fill": [0,0,0,255], "adaptive": { "light": [255,255,255,255], "dark": [0,0,0,255], "threshold": 0.4 } }
            "#,
        );
        let rendered = render(&json);
        assert_eq!(rendered.width, 64);
        assert_eq!(
            &rendered.bytes.as_bytes()[..8],
            &[0x89, b'P', b'N', b'G', 0x0d, 0x0a, 0x1a, 0x0a]
        );
    }

    #[test]
    fn counts_unresolvable_scene_fonts_in_metrics() {
        // The test scene points `default` and `bold` at a font that does not exist: the render
        // still succeeds (sans-serif stands in), but the scene reports both fallbacks so the
        // caller can see that the text came out with the wrong face.
        let rendered = render(&scene_json(
            r#"{ "type": "Text", "text": "Hi", "pos": [4, 10],
                 "font": { "role": "bold", "size": 14 }, "fill": [0, 0, 0, 255] }"#,
        ));
        assert_eq!(rendered.metrics.font_fallbacks, 2);
    }

    #[test]
    fn rejects_wrong_version() {
        let json = scene_json("").replace("\"version\": 2", "\"version\": 1");
        let scene: Scene = serde_json::from_str(&json).expect("scene parses");
        assert!(render_scene_inner(&scene, HashMap::new()).is_err());
    }

    /// A scene without the TriangleBg background (transparent canvas) for pixel-exact tests.
    fn bare_scene_json(canvas: (i32, i32), root: &str) -> String {
        format!(
            r#"{{
                "version": 2,
                "assets_base_dir": "/tmp/does-not-matter",
                "export_format": "png",
                "fonts": {{ "dir": "/tmp", "default": "missing", "bold": "missing" }},
                "canvas": {{ "width": {}, "height": {} }},
                "root": {root}
            }}"#,
            canvas.0, canvas.1
        )
    }

    /// Decode a rendered PNG back to unpremultiplied RGBA pixels.
    fn decode_pixels(rendered: &RenderedImage) -> (Vec<u8>, i32, i32) {
        let data = Data::new_copy(rendered.bytes.as_bytes());
        let image = Image::from_encoded(data).expect("png decodes");
        let (w, h) = (image.width(), image.height());
        let info = ImageInfo::new((w, h), ColorType::RGBA8888, AlphaType::Unpremul, None);
        let row = w as usize * 4;
        let mut buf = vec![0u8; row * h as usize];
        assert!(image.read_pixels(&info, &mut buf, row, (0, 0), CachingHint::Allow));
        (buf, w, h)
    }

    fn decode_file_pixels(path: &std::path::Path) -> (Vec<u8>, i32, i32) {
        let encoded = std::fs::read(path).expect("fixture reads");
        let image = Image::from_encoded(Data::new_copy(&encoded)).expect("fixture decodes");
        let (width, height) = (image.width(), image.height());
        let info = ImageInfo::new(
            (width, height),
            ColorType::RGBA8888,
            AlphaType::Unpremul,
            None,
        );
        let row_bytes = width as usize * 4;
        let mut pixels = vec![0; row_bytes * height as usize];
        assert!(image.read_pixels(&info, &mut pixels, row_bytes, (0, 0), CachingHint::Allow));
        (pixels, width, height)
    }

    #[test]
    fn pillow_lanczos_image_stretch_matches_full_raster_resizer() {
        let fixture = fixture_path("sdf_quad_face_only_field.png");
        let fixture_dir = fixture
            .parent()
            .expect("fixture parent")
            .to_string_lossy()
            .into_owned();
        let root = r#"{
            "type": "Image",
            "path": "sdf_quad_face_only_field.png",
            "pos": [0, 0],
            "size": [17, 13],
            "fit": "stretch",
            "sampling": "pillow_lanczos"
        }"#;
        let json = bare_scene_json((17, 13), root).replace("/tmp/does-not-matter", &fixture_dir);
        let scene: Scene = serde_json::from_str(&json).expect("parses");
        let rendered = render_scene_inner(&scene, HashMap::new()).expect("renders");
        let (actual, width, height) = decode_pixels(&rendered);
        let (source, source_width, source_height) = decode_file_pixels(&fixture);
        let expected = resize_rgba8_pillow_lanczos(
            &source,
            source_width as usize,
            source_height as usize,
            17,
            13,
            PillowResizeLimits::new(17 * 13 * 4, 128 * 1024, 4096),
        )
        .expect("reference resize");

        assert_eq!((width, height), (17, 13));
        assert_eq!(actual, expected);
    }

    #[test]
    fn pillow_lanczos_image_cover_resizes_full_source_then_crops_twice() {
        let fixture = fixture_path("sdf_quad_face_only_field.png");
        let fixture_dir = fixture
            .parent()
            .expect("fixture parent")
            .to_string_lossy()
            .into_owned();
        let root = r#"{
            "type": "Image",
            "path": "sdf_quad_face_only_field.png",
            "pos": [-3, -1],
            "size": [17, 9],
            "fit": "cover",
            "sampling": "pillow_lanczos"
        }"#;
        let json = bare_scene_json((11, 7), root).replace("/tmp/does-not-matter", &fixture_dir);
        let scene: Scene = serde_json::from_str(&json).expect("parses");
        let rendered = render_scene_inner(&scene, HashMap::new()).expect("renders");
        let (actual, width, height) = decode_pixels(&rendered);

        let (source, source_width, source_height) = decode_file_pixels(&fixture);
        assert_eq!((source_width, source_height), (64, 64));
        let resized = resize_rgba8_pillow_lanczos(
            &source,
            64,
            64,
            17,
            17,
            PillowResizeLimits::new(17 * 17 * 4, 128 * 1024, 4096),
        )
        .expect("reference cover resize");
        let covered = crop_rgba8(&resized, (17, 17), 0, 4, (17, 9)).expect("center cover crop");
        let expected = crop_rgba8(&covered, (17, 9), 3, 1, (11, 7)).expect("subscene crop");

        assert_eq!((width, height), (11, 7));
        assert_eq!(actual, expected);
    }

    #[test]
    fn pillow_lanczos_unity_subscene_applies_sequential_full_raster_resizes() {
        let root = r#"{
            "type": "UnitySubscene",
            "size": [4, 4],
            "anchor": [2, 1.5],
            "object_scale": [1.75, 1.25],
            "post_scale": [0.5714286, 0.6],
            "rotation": 0,
            "sampling": "pillow_lanczos",
            "children": [
                { "type": "Rect", "pos": [0, 0], "size": [2, 4],
                  "fill": [255, 0, 0, 255], "blend": "src" },
                { "type": "Rect", "pos": [2, 0], "size": [2, 4],
                  "fill": [0, 0, 255, 255], "blend": "src" }
            ]
        }"#;
        let scene: Scene = serde_json::from_str(&bare_scene_json((4, 3), root)).expect("parses");
        let rendered = render_scene_inner(&scene, HashMap::new()).expect("renders");
        let (actual, width, height) = decode_pixels(&rendered);

        let source: Vec<u8> = (0..4)
            .flat_map(|_| {
                [
                    [255, 0, 0, 255],
                    [255, 0, 0, 255],
                    [0, 0, 255, 255],
                    [0, 0, 255, 255],
                ]
                .into_iter()
                .flatten()
            })
            .collect();
        let first = resize_rgba8_pillow_lanczos(
            &source,
            4,
            4,
            7,
            5,
            PillowResizeLimits::new(7 * 5 * 4, 128 * 1024, 4096),
        )
        .expect("first resize");
        let expected = resize_rgba8_pillow_lanczos(
            &first,
            7,
            5,
            4,
            3,
            PillowResizeLimits::new(4 * 3 * 4, 128 * 1024, 4096),
        )
        .expect("second resize");

        assert_eq!((width, height), (4, 3));
        assert_eq!(actual, expected);
    }

    #[test]
    fn pillow_lanczos_rejects_unsupported_semantics_and_missing_assets() {
        let unsupported = r#"{
            "type": "Image",
            "path": "missing.png",
            "pos": [0, 0],
            "size": [4, 4],
            "fit": "contain",
            "sampling": "pillow_lanczos"
        }"#;
        let scene: Scene =
            serde_json::from_str(&bare_scene_json((4, 4), unsupported)).expect("parses");
        let err = expect_scene_error(&scene, HashMap::new());
        assert!(
            err.contains("supports only stretch/cover") && !err.contains("missing.png"),
            "validation must precede asset access: {err}"
        );

        let rotated = r#"{
            "type": "UnitySubscene",
            "size": [4, 4],
            "anchor": [2, 2],
            "object_scale": [1, 1],
            "post_scale": [1, 1],
            "rotation": 10,
            "sampling": "pillow_lanczos",
            "children": []
        }"#;
        let scene: Scene = serde_json::from_str(&bare_scene_json((4, 4), rotated)).expect("parses");
        let err = expect_scene_error(&scene, HashMap::new());
        assert!(
            err.contains("requires zero rotation"),
            "unexpected error: {err}"
        );

        let missing = r#"{
            "type": "Image",
            "path": "missing.png",
            "pos": [0, 0],
            "size": [4, 4],
            "fit": "stretch",
            "sampling": "pillow_lanczos"
        }"#;
        let scene: Scene = serde_json::from_str(&bare_scene_json((4, 4), missing)).expect("parses");
        let err = expect_scene_error(&scene, HashMap::new());
        assert!(
            err.contains("pillow_lanczos Image asset load failed") && err.contains("missing.png"),
            "missing source must fail the whole scene: {err}"
        );
    }

    #[test]
    fn pillow_lanczos_resize_obeys_remaining_scene_budget() {
        let fixture = fixture_path("sdf_quad_face_only_field.png");
        let fixture_dir = fixture
            .parent()
            .expect("fixture parent")
            .to_string_lossy()
            .into_owned();
        let root = r#"{
            "type": "Image",
            "path": "sdf_quad_face_only_field.png",
            "pos": [0, 0],
            "size": [17, 13],
            "fit": "stretch",
            "sampling": "pillow_lanczos"
        }"#;
        let json = bare_scene_json((17, 13), root)
            .replace("/tmp/does-not-matter", &fixture_dir)
            .replace(
                r#""root":"#,
                r#""limits":{"max_node_pixels":8388608,"max_scene_bytes":18000},"root":"#,
            );
        let scene: Scene = serde_json::from_str(&json).expect("parses");
        let err = expect_scene_error(&scene, HashMap::new());
        assert!(
            err.contains("only 732 bytes remain") || err.contains("scene limit"),
            "resize scratch/output must obey the scene budget: {err}"
        );
    }

    #[test]
    fn transform_parses_and_renders() {
        let json = scene_json(
            r#"
            { "type": "Transform", "matrix": [1, 0, 10, 0, 1, 6], "children": [
                { "type": "Rect", "pos": [0, 0], "size": [12, 8], "fill": [255, 0, 0, 255] }
              ] }
            "#,
        );
        let rendered = render(&json);
        assert_eq!(rendered.width, 64);
        assert_eq!(
            &rendered.bytes.as_bytes()[..8],
            &[0x89, b'P', b'N', b'G', 0x0d, 0x0a, 0x1a, 0x0a]
        );
    }

    #[test]
    fn transform_matches_pretransformed_rect() {
        // A rect under Transform(translate + rotate) must land where the forward corner math
        // says: corner (lx, ly) -> (a*lx + b*ly + c, d*lx + e*ly + f).
        let (sin, cos) = 30.0_f32.to_radians().sin_cos();
        let (tx, ty) = (20.0_f32, 22.0_f32);
        let (w, h) = (24.0_f32, 10.0_f32);
        let json = bare_scene_json(
            (64, 64),
            &format!(
                r#"{{ "type": "Transform", "matrix": [{cos}, {nsin}, {tx}, {sin}, {cos}, {ty}],
                      "children": [
                        {{ "type": "Rect", "pos": [0, 0], "size": [{w}, {h}],
                           "fill": [30, 160, 90, 255] }}
                      ] }}"#,
                nsin = -sin,
            ),
        );
        let rendered = render(&json);
        let (pixels, pw, ph) = decode_pixels(&rendered);
        assert_eq!((pw, ph), (64, 64));

        // Reference: the same geometry via corner math, drawn as an AA path with no CTM.
        let map = |lx: f32, ly: f32| Point::new(cos * lx - sin * ly + tx, sin * lx + cos * ly + ty);
        let mut reference = surfaces::raster_n32_premul((64, 64)).expect("surface");
        let corners = [map(0.0, 0.0), map(w, 0.0), map(w, h), map(0.0, h)];
        let path = skia_safe::Path::polygon(&corners, true, None, None);
        let mut paint = Paint::default();
        paint.set_anti_alias(true);
        paint.set_color(Color::from_argb(255, 30, 160, 90));
        reference.canvas().draw_path(&path, &paint);
        let snap = reference.image_snapshot();
        let info = ImageInfo::new((64, 64), ColorType::RGBA8888, AlphaType::Unpremul, None);
        let row = 64 * 4;
        let mut ref_pixels = vec![0u8; row * 64];
        assert!(snap.read_pixels(&info, &mut ref_pixels, row, (0, 0), CachingHint::Allow));

        // AA coverage may round differently between the CTM rect and the corner-math path on
        // boundary pixels; interiors must agree. Tolerate a handful of edge pixels only.
        let mismatched = pixels
            .chunks_exact(4)
            .zip(ref_pixels.chunks_exact(4))
            .filter(|(a, b)| a.iter().zip(b.iter()).any(|(x, y)| x.abs_diff(*y) > 16))
            .count();
        assert!(mismatched < 20, "mismatched pixels: {mismatched}");
        // Interior sanity: the transformed rect's center carries the fill color exactly.
        let center = map(w * 0.5, h * 0.5);
        let idx = ((center.y.round() as usize) * 64 + center.x.round() as usize) * 4;
        assert_eq!(&pixels[idx..idx + 4], &[30, 160, 90, 255]);
    }

    #[test]
    fn transform_children_see_zero_offset() {
        // Group offset (7,3) -> translate; matrix scales by 2. The child's own pos must map
        // through the matrix ONLY (device x = 7 + 2*lx), not be offset by the group again.
        let json = bare_scene_json(
            (64, 64),
            r#"{ "type": "Group", "offset": [7, 3], "size": [64, 64], "children": [
                 { "type": "Transform", "matrix": [2, 0, 0, 0, 2, 0], "children": [
                     { "type": "Rect", "pos": [5, 5], "size": [10, 10], "fill": [255, 0, 0, 255] }
                   ] }
               ] }"#,
        );
        let rendered = render(&json);
        let (pixels, w, _) = decode_pixels(&rendered);
        let px = |x: usize, y: usize| {
            let idx = (y * w as usize + x) * 4;
            [
                pixels[idx],
                pixels[idx + 1],
                pixels[idx + 2],
                pixels[idx + 3],
            ]
        };
        // Correct placement: rect spans x 17..37, y 13..33.
        assert_eq!(px(20, 15), [255, 0, 0, 255]);
        assert_eq!(px(36, 32), [255, 0, 0, 255]);
        // A double-applied offset would land it at x 31..51, y 19..39 instead.
        assert_eq!(px(45, 36)[3], 0);
        // And a dropped matrix (offset-only render) would fill x 12..22, y 8..18.
        assert_eq!(px(14, 14)[3], 0);
    }

    #[test]
    fn unknown_node_kind_fails_parse() {
        // The loud failure is load-bearing: an older wheel meeting newer IR must fail the whole
        // scene parse (-> PyValueError -> Python fail-open to Pillow), never skip the node.
        let json = scene_json(r#"{ "type": "Bogus" }"#);
        assert!(serde_json::from_str::<Scene>(&json).is_err());
    }

    fn fixture_path(name: &str) -> PathBuf {
        std::path::Path::new(env!("CARGO_MANIFEST_DIR"))
            .join("tests/fixtures")
            .join(name)
    }

    /// Decode a fixture PNG to unpremultiplied RGBA (grayscale PNGs come back R=G=B=L, A=255).
    ///
    /// Deliberately SkCodec, not `Image::from_encoded` + `read_pixels`: the image path
    /// rasterizes through PREMUL and corrupts straight-alpha RGB (the expected fixture's
    /// `204 @ a=63` comes back 202), while the codec decodes into the requested unpremul
    /// info natively and losslessly.
    fn decode_fixture_rgba(name: &str) -> (Vec<u8>, i32, i32) {
        let bytes = std::fs::read(fixture_path(name)).expect("fixture readable");
        let mut codec =
            skia_safe::Codec::from_data(Data::new_copy(&bytes)).expect("fixture decodes");
        let dimensions = codec.dimensions();
        let (w, h) = (dimensions.width, dimensions.height);
        let info = ImageInfo::new((w, h), ColorType::RGBA8888, AlphaType::Unpremul, None);
        let row = w as usize * 4;
        let mut buf = vec![0u8; row * h as usize];
        let result = codec.get_pixels_with_options(&info, &mut buf, row, None);
        assert!(
            matches!(result, skia_safe::codec::Result::Success),
            "fixture pixel decode failed"
        );
        (buf, w, h)
    }

    /// Run the render arm's pixel routine over a golden fixture triple (field L-PNG + scalars
    /// JSON emitted from the Python reference) and gate on per-channel |delta| <= 1 against the
    /// Python-rendered expected RGBA. Returns the observed max delta.
    fn run_sdf_quad_golden(name: &str) -> u8 {
        let (field_rgba, fw, fh) = decode_fixture_rgba(&format!("sdf_quad_{name}_field.png"));
        let field: Vec<u8> = field_rgba.chunks_exact(4).map(|px| px[0]).collect();
        let (expected, ew, eh) = decode_fixture_rgba(&format!("sdf_quad_{name}_expected.png"));
        assert_eq!((fw, fh), (ew, eh), "field/expected size mismatch");
        let scalars =
            std::fs::read_to_string(fixture_path(&format!("sdf_quad_{name}_scalars.json")))
                .expect("scalars readable");
        let shading: SdfShading = serde_json::from_str(&scalars).expect("scalars parse");
        let actual = shade_sdf_field(&field, fw as usize, fh as usize, fw as usize, &shading);
        assert_eq!(actual.len(), expected.len());
        let mut max_delta = 0u8;
        for (idx, (a, e)) in actual.iter().zip(expected.iter()).enumerate() {
            let delta = a.abs_diff(*e);
            assert!(
                delta <= 1,
                "{name}: channel {} of pixel {} is off by {delta} (got {a}, want {e})",
                idx % 4,
                idx / 4,
            );
            max_delta = max_delta.max(delta);
        }
        println!("sdf_quad golden {name}: max_delta={max_delta}");
        max_delta
    }

    #[test]
    fn sdf_quad_golden_face_only() {
        run_sdf_quad_golden("face_only");
    }

    #[test]
    fn sdf_quad_golden_underlay() {
        run_sdf_quad_golden("underlay");
    }

    #[test]
    fn sdf_quad_golden_gradient_bold() {
        run_sdf_quad_golden("gradient_bold");
    }

    #[test]
    fn sdf_quad_underlay_shift_samples_shifted_positions() {
        // 4x4 field of distinct bytes. The face pass is forced to zero (face_scale 0, face_w 1)
        // and the underlay to identity (scale 1, w 0, alpha 1), so the patch alpha at (x, y)
        // must be EXACTLY the shifted field byte: shifted[y][x] = field[y + sy][x + sx] with
        // shift [1, -1], and out-of-bounds (row 0 / rightmost column) zero-filled.
        let field: Vec<u8> = (0..16).map(|i| (i * 16) as u8).collect();
        let shading = SdfShading {
            face_color: [255, 0, 0],
            face_scale: 0.0,
            face_w: 1.0,
            alpha: 1.0,
            underlay: Some(SdfUnderlay {
                color: [0, 0, 255],
                scale: 1.0,
                w: 0.0,
                shift: [1, -1],
            }),
        };
        let patch = shade_sdf_field(&field, 4, 4, 4, &shading);
        let alpha_at = |x: usize, y: usize| patch[(y * 4 + x) * 4 + 3];
        for x in 0..4 {
            assert_eq!(
                alpha_at(x, 0),
                0,
                "row 0 samples y=-1 and must be zero-filled"
            );
        }
        for y in 0..4 {
            assert_eq!(
                alpha_at(3, y),
                0,
                "column 3 samples x=4 and must be zero-filled"
            );
        }
        for y in 1..4 {
            for x in 0..3 {
                assert_eq!(
                    alpha_at(x, y),
                    field[(y - 1) * 4 + (x + 1)],
                    "shifted sample at ({x}, {y})"
                );
            }
        }
    }

    fn sdf_scene_json(field: &str) -> String {
        bare_scene_json(
            (16, 16),
            &format!(
                r#"{{ "type": "Group", "offset": [2, 3], "size": [16, 16], "children": [
                     {{ "type": "SdfQuad", "pos": [1, 1], "field": "{field}",
                        "shading": {{ "face_color": [255, 204, 0], "face_scale": 12.0,
                                      "face_w": 4.9, "alpha": 0.9 }} }}
                   ] }}"#
            ),
        )
    }

    /// `RenderedImage` has no `Debug`, so `expect_err` can't unwrap the error directly.
    #[test]
    fn transform_rejects_self_image_child() {
        // Device-bounds snapshots assume an identity CTM; inside a Transform the scene must
        // fail WHOLE (-> Python fail-open), never render a silently wrong snapshot.
        let json = bare_scene_json(
            (32, 24),
            r#"{ "type": "Transform", "matrix": [1.0, 0.0, 0.0, 1.0, 4.0, 2.0], "children": [
                 { "type": "SelfImage", "pos": [0, 0], "size": [8, 8], "source_rect": [0, 0, 8, 8] }
               ] }"#,
        );
        let scene: Scene = serde_json::from_str(&json).expect("parses");
        let err = expect_scene_error(&scene, HashMap::new());
        assert!(err.contains("SelfImage"), "unexpected error: {err}");
    }

    #[test]
    fn transform_rejects_sdf_quad_through_nested_group() {
        // The walk must carry the in-Transform flag through Group children, and the
        // rejection must fire before SdfQuad field resolution (no mem image supplied).
        let json = bare_scene_json(
            (32, 24),
            r#"{ "type": "Transform", "matrix": [0.5, 0.0, 0.0, 0.5, 0.0, 0.0], "children": [
                 { "type": "Group", "offset": [2, 2], "size": [16, 16], "children": [
                   { "type": "SdfQuad", "pos": [1, 1], "field": "mem:any",
                     "shading": { "face_color": [255, 204, 0], "face_scale": 12.0,
                                  "face_w": 4.9, "alpha": 0.9 } }
                 ] }
               ] }"#,
        );
        let scene: Scene = serde_json::from_str(&json).expect("parses");
        let err = expect_scene_error(&scene, HashMap::new());
        assert!(err.contains("SdfQuad"), "unexpected error: {err}");
    }

    #[test]
    fn transform_still_renders_plain_children() {
        // The preflight must not reject the supported Transform contents.
        let json = bare_scene_json(
            (32, 24),
            r#"{ "type": "Transform", "matrix": [1.0, 0.0, 0.0, 1.0, 4.0, 2.0], "children": [
                 { "type": "Rect", "pos": [0, 0], "size": [8, 8], "fill": [255, 0, 0, 255] }
               ] }"#,
        );
        let scene: Scene = serde_json::from_str(&json).expect("parses");
        render_scene_inner(&scene, HashMap::new()).expect("renders");
    }

    fn expect_scene_error(scene: &Scene, mem: HashMap<String, MemImage>) -> String {
        match render_scene_inner(scene, mem) {
            Err(err) => err,
            Ok(_) => panic!("scene must error"),
        }
    }

    fn a8_mem_image(width: i32, height: i32, bytes: &[u8]) -> MemImage {
        MemImage::Raw {
            width,
            height,
            row_bytes: width as usize,
            color_type: ColorType::Alpha8,
            alpha_type: AlphaType::Unpremul,
            data: Data::new_copy(bytes),
            _buffer: None,
            _owner: None,
        }
    }

    fn rgba_mem_image(width: i32, height: i32, bytes: &[u8]) -> MemImage {
        MemImage::Raw {
            width,
            height,
            row_bytes: width as usize * 4,
            color_type: ColorType::RGBA8888,
            alpha_type: AlphaType::Unpremul,
            data: Data::new_copy(bytes),
            _buffer: None,
            _owner: None,
        }
    }

    #[test]
    fn paste_lerp_blend_deserializes() {
        let blend: ImageBlend = serde_json::from_str(r#""paste_lerp""#).expect("blend parses");
        assert_eq!(blend, ImageBlend::PasteLerp);
    }

    #[test]
    fn paste_lerp_uses_source_alpha_to_lerp_all_straight_channels() {
        let root = r#"{
            "type": "Group", "size": [1, 1], "children": [
                { "type": "Rect", "pos": [0, 0], "size": [1, 1],
                  "fill": [0, 0, 255, 255], "blend": "src" },
                { "type": "Image", "path": "mem:source", "pos": [0, 0],
                  "size": [1, 1], "fit": "stretch", "sampling": "nearest",
                  "blend": "paste_lerp" }
            ]
        }"#;
        let scene: Scene =
            serde_json::from_str(&bare_scene_json((1, 1), root)).expect("scene parses");
        let mut mem = HashMap::new();
        mem.insert(
            "source".to_string(),
            rgba_mem_image(1, 1, &[255, 255, 255, 128]),
        );
        let rendered = render_scene_inner(&scene, mem).expect("renders");
        let (pixels, _, _) = decode_pixels(&rendered);
        let expected = [128_u8, 128, 255, 191];
        for (actual, expected) in pixels[..4].iter().zip(expected) {
            assert!(
                actual.abs_diff(expected) <= 1,
                "paste_lerp pixel differs: {:?}",
                &pixels[..4]
            );
        }
        assert_ne!(pixels[3], 255, "paste_lerp must also interpolate alpha");
    }

    #[test]
    fn paste_lerp_preflights_unsupported_scene_before_asset_access() {
        let root = r#"{
            "type": "Image", "path": "missing.png", "pos": [0, 0],
            "size": [8, 8], "fit": "contain", "blend": "paste_lerp"
        }"#;
        let scene: Scene =
            serde_json::from_str(&bare_scene_json((8, 8), root)).expect("scene parses");
        let err = expect_scene_error(&scene, HashMap::new());
        assert!(
            err.contains("paste_lerp Image requires stretch fit")
                && !err.contains("asset load failed"),
            "unexpected error: {err}"
        );

        let transformed = r#"{
            "type": "Transform", "matrix": [1, 0, 0, 1, 0, 0], "children": [{
                "type": "Image", "path": "missing.png", "pos": [0, 0],
                "size": [8, 8], "fit": "stretch", "blend": "paste_lerp"
            }]
        }"#;
        let scene: Scene =
            serde_json::from_str(&bare_scene_json((8, 8), transformed)).expect("scene parses");
        let err = expect_scene_error(&scene, HashMap::new());
        assert!(
            err.contains("paste_lerp Image inside Transform") && !err.contains("asset load failed"),
            "unexpected error: {err}"
        );

        let masked = r#"{
            "type": "Group", "size": [8, 8], "mask": "missing-mask.png", "children": [{
                "type": "Image", "path": "missing.png", "pos": [0, 0],
                "size": [8, 8], "fit": "stretch", "blend": "paste_lerp"
            }]
        }"#;
        let scene: Scene =
            serde_json::from_str(&bare_scene_json((8, 8), masked)).expect("scene parses");
        let err = expect_scene_error(&scene, HashMap::new());
        assert!(
            err.contains("paste_lerp Image inside a masked Group saveLayer")
                && !err.contains("asset load failed"),
            "unexpected error: {err}"
        );
    }

    #[test]
    fn paste_lerp_respects_group_clip_and_runs_inside_unity_subscene() {
        let clipped = r#"{
            "type": "Group", "size": [2, 1], "children": [
                { "type": "Rect", "pos": [0, 0], "size": [2, 1],
                  "fill": [255, 0, 0, 255], "blend": "src" },
                { "type": "Group", "size": [1, 1], "clip": { "kind": "rect" },
                  "children": [{
                    "type": "Image", "path": "mem:source", "pos": [0, 0],
                    "size": [2, 1], "fit": "stretch", "sampling": "nearest",
                    "blend": "paste_lerp"
                  }]
                }
            ]
        }"#;
        let scene: Scene =
            serde_json::from_str(&bare_scene_json((2, 1), clipped)).expect("scene parses");
        let mut mem = HashMap::new();
        mem.insert(
            "source".to_string(),
            rgba_mem_image(1, 1, &[0, 255, 0, 128]),
        );
        let rendered = render_scene_inner(&scene, mem).expect("clipped scene renders");
        let (pixels, _, _) = decode_pixels(&rendered);
        assert!(pixels[0].abs_diff(127) <= 1 && pixels[1].abs_diff(128) <= 1);
        assert_eq!(&pixels[4..8], &[255, 0, 0, 255]);

        let subscene = r#"{
            "type": "UnitySubscene", "size": [1, 1], "anchor": [0.5, 0.5],
            "object_scale": [1, 1], "post_scale": [1, 1], "rotation": 0,
            "sampling": "nearest", "children": [
                { "type": "Rect", "pos": [0, 0], "size": [1, 1],
                  "fill": [0, 0, 255, 255], "blend": "src" },
                { "type": "Image", "path": "mem:source", "pos": [0, 0],
                  "size": [1, 1], "fit": "stretch", "sampling": "nearest",
                  "blend": "paste_lerp" }
            ]
        }"#;
        let scene: Scene =
            serde_json::from_str(&bare_scene_json((1, 1), subscene)).expect("scene parses");
        let mut mem = HashMap::new();
        mem.insert(
            "source".to_string(),
            rgba_mem_image(1, 1, &[255, 255, 255, 128]),
        );
        let rendered = render_scene_inner(&scene, mem).expect("UnitySubscene renders");
        let (pixels, _, _) = decode_pixels(&rendered);
        assert!(
            pixels[3].abs_diff(191) <= 1,
            "unexpected pixel: {:?}",
            &pixels[..4]
        );
    }

    fn test_sliced_image_node(size: [f32; 2], border: [i32; 4]) -> SlicedImageNode {
        SlicedImageNode {
            path: "mem:sprite".to_string(),
            pos: [0.0, 0.0],
            size,
            border,
            tint: None,
            alpha: 1.0,
        }
    }

    fn sliced_image_root(path: &str, size: (i32, i32), border: [i32; 4], extras: &str) -> String {
        format!(
            r#"{{
                "type": "SlicedImage",
                "path": "{path}",
                "pos": [0, 0],
                "size": [{}, {}],
                "border": [{}, {}, {}, {}]
                {extras}
            }}"#,
            size.0, size.1, border[0], border[1], border[2], border[3],
        )
    }

    #[test]
    fn sliced_image_preserves_colored_three_by_three_regions() {
        let colors = [
            [255, 0, 0, 255],
            [0, 255, 0, 255],
            [0, 0, 255, 255],
            [255, 255, 0, 255],
            [255, 0, 255, 255],
            [0, 255, 255, 255],
            [120, 30, 10, 255],
            [20, 140, 60, 255],
            [70, 40, 180, 255],
        ];
        let source: Vec<u8> = colors.into_iter().flatten().collect();
        let root = sliced_image_root("mem:grid", (9, 9), [1, 1, 1, 1], "");
        let scene: Scene = serde_json::from_str(&bare_scene_json((9, 9), &root)).expect("parses");
        let mut mem = HashMap::new();
        mem.insert("grid".to_string(), rgba_mem_image(3, 3, &source));
        let rendered = render_scene_inner(&scene, mem).expect("renders");
        let (pixels, width, _) = decode_pixels(&rendered);
        let pixel = |x: usize, y: usize| &pixels[(y * width as usize + x) * 4..][..4];

        for (row, y) in [0, 4, 8].into_iter().enumerate() {
            for (column, x) in [0, 4, 8].into_iter().enumerate() {
                assert_eq!(
                    pixel(x, y),
                    &colors[row * 3 + column],
                    "region ({column}, {row})"
                );
            }
        }
    }

    #[test]
    fn sliced_image_matches_general_prefab_44_to_548_by_64_geometry() {
        let colors = [
            [240, 20, 20, 255],
            [20, 240, 20, 255],
            [20, 20, 240, 255],
            [240, 240, 20, 255],
            [240, 20, 240, 255],
            [20, 240, 240, 255],
            [120, 40, 20, 255],
            [20, 120, 40, 255],
            [40, 20, 120, 255],
        ];
        let mut source = vec![0u8; 44 * 44 * 4];
        for y in 0..44usize {
            let row = if y < 21 {
                0
            } else if y < 23 {
                1
            } else {
                2
            };
            for x in 0..44usize {
                let column = if x < 21 {
                    0
                } else if x < 23 {
                    1
                } else {
                    2
                };
                source[(y * 44 + x) * 4..][..4].copy_from_slice(&colors[row * 3 + column]);
            }
        }

        let root = sliced_image_root("mem:sprite", (548, 64), [21, 21, 21, 21], "");
        let scene: Scene =
            serde_json::from_str(&bare_scene_json((548, 64), &root)).expect("parses");
        let mut mem = HashMap::new();
        mem.insert("sprite".to_string(), rgba_mem_image(44, 44, &source));
        let rendered = render_scene_inner(&scene, mem).expect("renders");
        let (pixels, width, _) = decode_pixels(&rendered);
        let pixel = |x: usize, y: usize| &pixels[(y * width as usize + x) * 4..][..4];

        for (row, y) in [10, 32, 53].into_iter().enumerate() {
            for (column, x) in [10, 274, 537].into_iter().enumerate() {
                assert_eq!(
                    pixel(x, y),
                    &colors[row * 3 + column],
                    "region ({column}, {row})"
                );
            }
        }
    }

    #[test]
    fn sliced_image_shrinks_borders_before_recutting_the_source() {
        let node = test_sliced_image_node([20.0, 16.0], [21, 21, 21, 21]);
        let (geometry, _) =
            sliced_image_geometry(&node, 44, 44, 8 * 1024 * 1024).expect("geometry must fit");
        assert_eq!(geometry.target_size, (20, 16));
        assert_eq!(geometry.x.source, [0, 10, 34, 44]);
        assert_eq!(geometry.x.target, [0, 10, 10, 20]);
        assert_eq!(geometry.y.source, [0, 8, 36, 44]);
        assert_eq!(geometry.y.target, [0, 8, 8, 16]);
    }

    #[test]
    fn sliced_image_applies_recolor_tint_and_node_alpha() {
        let source = [200, 100, 50, 255].repeat(9);
        let root = sliced_image_root(
            "mem:sprite",
            (3, 3),
            [1, 1, 1, 1],
            r#",
                "tint": { "color": [12, 34, 56, 128], "mode": "recolor" },
                "alpha": 0.5"#,
        );
        let scene: Scene = serde_json::from_str(&bare_scene_json((3, 3), &root)).expect("parses");
        let mut mem = HashMap::new();
        mem.insert("sprite".to_string(), rgba_mem_image(3, 3, &source));
        let rendered = render_scene_inner(&scene, mem).expect("renders");
        let (pixels, _, _) = decode_pixels(&rendered);
        let center = &pixels[(4 * 4)..][..4];
        for (actual, expected) in center.iter().zip([12, 34, 56, 64]) {
            assert!(
                actual.abs_diff(expected) <= 2,
                "tinted center differs: {center:?}"
            );
        }
    }

    #[test]
    fn sliced_image_is_fail_soft_generally_but_strict_in_unity_subscene() {
        let missing = sliced_image_root("missing.png", (4, 4), [1, 1, 1, 1], "");
        let scene: Scene =
            serde_json::from_str(&bare_scene_json((8, 8), &missing)).expect("parses");
        let rendered =
            render_scene_inner(&scene, HashMap::new()).expect("general scene must skip asset");
        let (pixels, _, _) = decode_pixels(&rendered);
        assert!(pixels.iter().all(|value| *value == 0));

        let strict = format!(
            r#"{{
                "type": "UnitySubscene", "size": [4, 4], "anchor": [2, 2],
                "object_scale": [1, 1], "post_scale": [1, 1], "rotation": 0,
                "children": [{missing}]
            }}"#
        );
        let scene: Scene = serde_json::from_str(&bare_scene_json((8, 8), &strict)).expect("parses");
        let err = expect_scene_error(&scene, HashMap::new());
        assert!(
            err.contains("UnitySubscene asset load failed") && err.contains("missing.png"),
            "unexpected error: {err}"
        );

        let corrupt = sliced_image_root("mem:bad", (4, 4), [1, 1, 1, 1], "");
        let strict = format!(
            r#"{{
                "type": "UnitySubscene", "size": [4, 4], "anchor": [2, 2],
                "object_scale": [1, 1], "post_scale": [1, 1], "rotation": 0,
                "children": [{corrupt}]
            }}"#
        );
        let scene: Scene = serde_json::from_str(&bare_scene_json((8, 8), &strict)).expect("parses");
        let mut mem = HashMap::new();
        mem.insert(
            "bad".to_string(),
            MemImage::Encoded {
                data: Data::new_copy(b"not-an-image"),
                _owner: None,
            },
        );
        let err = expect_scene_error(&scene, mem);
        assert!(
            err.contains("failed to decode mem image"),
            "unexpected error: {err}"
        );
    }

    #[test]
    fn sliced_image_respects_target_source_and_scene_budgets() {
        let root = sliced_image_root("missing.png", (8, 8), [1, 1, 1, 1], "");
        let json = bare_scene_json((8, 8), &root).replace(
            r#""root":"#,
            r#""limits":{"max_node_pixels":16,"max_scene_bytes":1024},"root":"#,
        );
        let scene: Scene = serde_json::from_str(&json).expect("parses");
        let err = expect_scene_error(&scene, HashMap::new());
        assert!(
            err.contains("SlicedImage target") && err.contains("exceeds limit 16"),
            "geometry must fail before the missing asset is touched: {err}"
        );

        let json = bare_scene_json((8, 8), &root).replace(
            r#""root":"#,
            r#""limits":{"max_node_pixels":64,"max_scene_bytes":300},"root":"#,
        );
        let scene: Scene = serde_json::from_str(&json).expect("parses");
        let err = expect_scene_error(&scene, HashMap::new());
        assert!(
            err.contains("output, request buffers"),
            "256-byte output + 256-byte patch must exceed the scene limit: {err}"
        );

        let root = sliced_image_root("mem:large", (1, 1), [0, 0, 0, 0], "");
        let json = bare_scene_json((4, 4), &root).replace(
            r#""root":"#,
            r#""limits":{"max_node_pixels":16,"max_scene_bytes":1024},"root":"#,
        );
        let scene: Scene = serde_json::from_str(&json).expect("parses");
        let mut mem = HashMap::new();
        mem.insert("large".to_string(), rgba_mem_image(8, 8, &[255; 8 * 8 * 4]));
        let err = expect_scene_error(&scene, mem);
        assert!(
            err.contains("SlicedImage source") && err.contains("exceeds limit 16"),
            "unexpected error: {err}"
        );
    }

    #[test]
    fn sdf_quad_unknown_mem_image_errors() {
        // A dangling field reference must fail the WHOLE scene (error, not panic, not skip).
        let scene: Scene = serde_json::from_str(&sdf_scene_json("mem:nope")).expect("parses");
        let err = expect_scene_error(&scene, HashMap::new());
        assert!(err.contains("SdfQuad"), "unexpected error: {err}");
    }

    #[test]
    fn sdf_quad_wrong_color_type_errors() {
        // A resolvable mem image of the wrong color type is just as much an emitter bug.
        let mut mem = HashMap::new();
        mem.insert(
            "field".to_string(),
            MemImage::Raw {
                width: 2,
                height: 2,
                row_bytes: 8,
                color_type: ColorType::RGBA8888,
                alpha_type: AlphaType::Unpremul,
                data: Data::new_copy(&[0u8; 16]),
                _buffer: None,
                _owner: None,
            },
        );
        let scene: Scene = serde_json::from_str(&sdf_scene_json("mem:field")).expect("parses");
        let err = expect_scene_error(&scene, mem);
        assert!(err.contains("Alpha8"), "unexpected error: {err}");
    }

    #[test]
    fn sdf_quad_encoded_mem_image_errors() {
        let mut mem = HashMap::new();
        mem.insert(
            "field".to_string(),
            MemImage::Encoded {
                data: Data::new_copy(&[0x89, b'P', b'N', b'G']),
                _owner: None,
            },
        );
        let scene: Scene = serde_json::from_str(&sdf_scene_json("mem:field")).expect("parses");
        let err = expect_scene_error(&scene, mem);
        assert!(err.contains("Alpha8"), "unexpected error: {err}");
    }

    #[test]
    fn sdf_quad_renders_a8_mem_field_at_integer_pos() {
        // The tuple->MemImage extraction is cfg(not(test)), so cover the ColorType::Alpha8
        // handling at the MemImage level: an 8x8 A8 field drawn at group (2,3) + pos (1,1).
        // Over a transparent canvas src-over keeps the source alpha byte exactly (premul
        // conversion preserves alpha), so the canvas alpha must equal the patch alpha.
        let field: Vec<u8> = (0..64).map(|i| (i * 4) as u8).collect();
        let mut mem = HashMap::new();
        mem.insert("glyph".to_string(), a8_mem_image(8, 8, &field));
        let scene: Scene = serde_json::from_str(&sdf_scene_json("mem:glyph")).expect("parses");
        let rendered = render_scene_inner(&scene, mem).expect("renders");
        assert_eq!(rendered.metrics.sdf_quad_count, 1);
        assert!(rendered.metrics.sdf_quad_elapsed >= 0.0);

        let shading = SdfShading {
            face_color: [255, 204, 0],
            face_scale: 12.0,
            face_w: 4.9,
            alpha: 0.9,
            underlay: None,
        };
        let patch = shade_sdf_field(&field, 8, 8, 8, &shading);
        let (pixels, w, _) = decode_pixels(&rendered);
        let mut nonzero = 0u32;
        for y in 0..8usize {
            for x in 0..8usize {
                let canvas_idx = ((y + 4) * w as usize + (x + 3)) * 4;
                let patch_a = patch[(y * 8 + x) * 4 + 3];
                assert_eq!(pixels[canvas_idx + 3], patch_a, "alpha at patch ({x}, {y})");
                nonzero += u32::from(patch_a > 0);
            }
        }
        assert!(nonzero > 0, "the shaded patch must not be empty");
        // Nothing may land outside the 8x8 patch footprint at (3, 4).
        assert_eq!(pixels[3], 0, "canvas origin must stay transparent");
    }

    fn test_sdf_shape_node() -> SdfShapeNode {
        SdfShapeNode {
            path: "shape.png".to_string(),
            anchor: [8.0, 8.0],
            sdf_scale: [1.0, 1.0],
            post_scale: [1.0, 1.0],
            rotation: 0.0,
            field_channel: SdfShapeFieldChannel::Red,
            fill_color: [17, 34, 51],
            fill_alpha: 1.0,
            outline_color: [255, 255, 255],
            outline_alpha: 0.0,
            outer_fill_ratio: 0.0,
            face_dilate: 0.0,
            softness: 0.0,
        }
    }

    #[test]
    fn sdf_shape_shades_unpremultiplied_red_and_alpha_independently() {
        let field = [0_u8, 128, 0, 128, 255, 128, 0, 128, 0];
        let alpha = [255_u8, 255, 255, 255, 64, 255, 255, 255, 255];
        let mut pixels = Vec::with_capacity(3 * 3 * 4);
        for (&red, &a) in field.iter().zip(alpha.iter()) {
            pixels.extend_from_slice(&[red, 0, 0, a]);
        }
        let source = SdfShapeSource {
            pixels,
            width: 3,
            height: 3,
        };
        let patch = shade_sdf_shape(&source, &test_sdf_shape_node(), 3, 3).expect("shades");
        let center = &patch[(1 * 3 + 1) * 4..][..4];
        assert_eq!(&center[..3], &[17, 34, 51]);
        assert_eq!(
            center[3], 64,
            "texture alpha must remain independent of the red SDF"
        );
        assert_eq!(patch[3], 0, "zero-distance corner must stay transparent");
    }

    #[test]
    fn sdf_shape_rejects_huge_patch_before_allocation() {
        let mut node = test_sdf_shape_node();
        node.sdf_scale = [10.0, 10.0];
        let err =
            sdf_shape_dimensions(&node, 1024, 1024, 8 * 1024 * 1024).expect_err("must reject");
        assert!(err.contains("exceeds limit"), "unexpected error: {err}");
    }

    #[test]
    fn sdf_shape_asset_node_renders_without_mem_transport() {
        let fixture_dir = fixture_path("sdf_quad_face_only_field.png")
            .parent()
            .expect("fixture parent")
            .to_string_lossy()
            .into_owned();
        let root = r#"{
            "type": "SdfShape",
            "path": "sdf_quad_face_only_field.png",
            "anchor": [32, 24],
            "sdf_scale": [1, 1],
            "post_scale": [1, 1],
            "rotation": 0,
            "field_channel": "red",
            "fill_color": [20, 120, 240],
            "fill_alpha": 1,
            "outline_color": [255, 255, 255],
            "outline_alpha": 0,
            "outer_fill_ratio": 0,
            "face_dilate": 0,
            "softness": 0
        }"#;
        let json = bare_scene_json((64, 48), root).replace("/tmp/does-not-matter", &fixture_dir);
        let scene: Scene = serde_json::from_str(&json).expect("parses");
        let rendered = render_scene_inner(&scene, HashMap::new()).expect("renders");
        let (pixels, _, _) = decode_pixels(&rendered);
        assert!(
            pixels
                .chunks_exact(4)
                .any(|pixel| pixel[3] > 0 && pixel[..3] == [20, 120, 240]),
            "SdfShape must draw the shaded asset"
        );
    }

    #[test]
    fn unity_image_asset_node_renders_intrinsic_source() {
        let fixture_dir = fixture_path("sdf_quad_face_only_field.png")
            .parent()
            .expect("fixture parent")
            .to_string_lossy()
            .into_owned();
        let root = r#"{
            "type": "UnityImage",
            "path": "sdf_quad_face_only_field.png",
            "anchor": [32, 24],
            "object_scale": [1, 1],
            "post_scale": [0.5, 0.5],
            "rotation": 12,
            "sampling": "catmull_rom"
        }"#;
        let json = bare_scene_json((64, 48), root).replace("/tmp/does-not-matter", &fixture_dir);
        let scene: Scene = serde_json::from_str(&json).expect("parses");
        let rendered = render_scene_inner(&scene, HashMap::new()).expect("renders");
        let (pixels, _, _) = decode_pixels(&rendered);
        assert!(
            pixels.chunks_exact(4).any(|pixel| pixel[3] > 0),
            "UnityImage must draw the strict asset"
        );
    }

    #[test]
    fn unity_image_dimensions_preserve_sequential_rounding_and_budget() {
        let mut node = UnityImageNode {
            path: "image.png".to_string(),
            anchor: [0.0, 0.0],
            object_scale: [0.5, 0.5],
            post_scale: [0.5, 0.5],
            rotation: 0.0,
            sampling: ImageSampling::CatmullRom,
            alpha: 1.0,
        };
        let dimensions = unity_image_dimensions(&node, 3, 3, 8 * 1024 * 1024).expect("dimensions");
        assert_eq!(dimensions.first, (2, 2));
        assert_eq!(dimensions.final_size, (1, 1));

        node.object_scale = [8.0, 8.0];
        node.post_scale = [1.0, 1.0];
        let err =
            unity_image_dimensions(&node, 1024, 1024, 8 * 1024 * 1024).expect_err("must reject");
        assert!(err.contains("exceeds limit"), "unexpected error: {err}");
    }

    #[test]
    fn unity_image_missing_asset_fails_the_whole_scene() {
        let root = r#"{
            "type": "UnityImage",
            "path": "missing.png",
            "anchor": [16, 12],
            "object_scale": [1, 1],
            "post_scale": [1, 1],
            "rotation": 0
        }"#;
        let json = bare_scene_json((32, 24), root);
        let scene: Scene = serde_json::from_str(&json).expect("parses");
        let err = expect_scene_error(&scene, HashMap::new());
        assert!(err.contains("missing.png"), "unexpected error: {err}");
    }

    #[test]
    fn unity_image_respects_scene_byte_budget_before_decode() {
        let fixture_dir = fixture_path("sdf_quad_face_only_field.png")
            .parent()
            .expect("fixture parent")
            .to_string_lossy()
            .into_owned();
        let root = r#"{
            "type": "UnityImage",
            "path": "sdf_quad_face_only_field.png",
            "anchor": [16, 12],
            "object_scale": [1, 1],
            "post_scale": [1, 1],
            "rotation": 0
        }"#;
        let json = bare_scene_json((32, 24), root)
            .replace("/tmp/does-not-matter", &fixture_dir)
            .replace(
                r#""root":"#,
                r#""limits":{"max_node_pixels":8388608,"max_scene_bytes":32},"root":"#,
            );
        let scene: Scene = serde_json::from_str(&json).expect("parses");
        let err = expect_scene_error(&scene, HashMap::new());
        assert!(err.contains("scene limit"), "unexpected error: {err}");
    }

    #[test]
    fn unity_subscene_contains_src_writes_before_parent_composite() {
        let root = r#"{
            "type": "Group", "offset": [0, 0], "size": [8, 8], "children": [
                { "type": "Rect", "pos": [0, 0], "size": [8, 8],
                  "fill": [255, 0, 0, 255] },
                { "type": "UnitySubscene", "size": [4, 4], "anchor": [4, 4],
                  "object_scale": [1, 1], "post_scale": [1, 1], "rotation": 0,
                  "sampling": "catmull_rom", "children": [
                    { "type": "Rect", "pos": [0, 0], "size": [4, 4],
                      "fill": [0, 0, 255, 255] },
                    { "type": "Image", "path": "mem:clear", "pos": [1, 1],
                      "size": [2, 2], "fit": "stretch", "sampling": "nearest",
                      "blend": "src" }
                  ] }
            ]
        }"#;
        let scene: Scene = serde_json::from_str(&bare_scene_json((8, 8), root)).expect("parses");
        let mut mem = HashMap::new();
        mem.insert("clear".to_string(), rgba_mem_image(2, 2, &[0; 16]));
        let rendered = render_scene_inner(&scene, mem).expect("renders");
        let (pixels, width, _) = decode_pixels(&rendered);
        let pixel = |x: usize, y: usize| &pixels[(y * width as usize + x) * 4..][..4];

        assert_eq!(pixel(2, 2), &[0, 0, 255, 255]);
        assert_eq!(
            pixel(3, 3),
            &[255, 0, 0, 255],
            "transparent Src inside the subscene must reveal, not erase, the parent"
        );
    }

    #[test]
    fn raster_subscene_contains_src_writes_and_is_safe_inside_masked_group() {
        let root = r#"{
            "type": "Group", "offset": [0, 0], "size": [4, 4], "children": [
                { "type": "Rect", "pos": [0, 0], "size": [4, 4],
                  "fill": [0, 0, 255, 255], "blend": "src" },
                { "type": "Group", "offset": [0, 0], "size": [4, 4],
                  "mask": "mem:mask", "children": [
                    { "type": "RasterSubscene", "natural_size": [2, 2],
                      "pos": [1, 1], "dst_size": [2, 2], "sampling": "nearest",
                      "children": [
                        { "type": "Image", "path": "mem:layer", "pos": [0, 0],
                          "size": [2, 2], "fit": "stretch", "sampling": "nearest",
                          "blend": "src" }
                      ] }
                  ] }
            ]
        }"#;
        let scene: Scene =
            serde_json::from_str(&bare_scene_json((4, 4), root)).expect("scene parses");
        let mut mem = HashMap::new();
        mem.insert(
            "layer".to_string(),
            rgba_mem_image(2, 2, &[255, 0, 0, 255, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0]),
        );
        mem.insert("mask".to_string(), rgba_mem_image(1, 1, &[255; 4]));
        let rendered = render_scene_inner(&scene, mem).expect("renders");
        let (pixels, width, _) = decode_pixels(&rendered);
        let pixel = |x: usize, y: usize| &pixels[(y * width as usize + x) * 4..][..4];

        assert_eq!(pixel(1, 1), &[255, 0, 0, 255]);
        assert_eq!(
            pixel(2, 1),
            &[0, 0, 255, 255],
            "transparent Src pixels must reveal, not erase, the opaque parent"
        );
        assert_eq!(pixel(1, 2), &[0, 0, 255, 255]);
    }

    #[test]
    fn raster_subscene_scene_scale_matches_ordinary_image_final_resize() {
        let root = r#"{
            "type": "Group", "size": [4, 2], "children": [
                { "type": "RasterSubscene", "natural_size": [4, 1],
                  "pos": [0, 0], "dst_size": [2, 2], "sampling": "catmull_rom",
                  "children": [
                    { "type": "Image", "path": "mem:stripes", "pos": [0, 0],
                      "size": [4, 1], "fit": "stretch", "sampling": "nearest",
                      "blend": "src" }
                  ] },
                { "type": "Image", "path": "mem:stripes", "pos": [2, 0],
                  "size": [2, 2], "fit": "stretch", "sampling": "catmull_rom" }
            ]
        }"#;
        let json =
            bare_scene_json((4, 2), root).replace("\"canvas\":", "\"scale\": 1.5, \"canvas\":");
        let scene: Scene = serde_json::from_str(&json).expect("scene parses");
        let mut mem = HashMap::new();
        mem.insert(
            "stripes".to_string(),
            rgba_mem_image(
                4,
                1,
                &[
                    0, 0, 0, 255, 255, 255, 255, 255, 0, 0, 0, 255, 255, 255, 255, 255,
                ],
            ),
        );
        let rendered = render_scene_inner(&scene, mem).expect("renders");
        let (pixels, width, height) = decode_pixels(&rendered);
        let pixel = |x: usize, y: usize| &pixels[(y * width as usize + x) * 4..][..4];

        assert_eq!((width, height), (6, 3));
        let raster_pixels: Vec<_> = (0..3).map(|x| pixel(x, 1).to_vec()).collect();
        let direct_pixels: Vec<_> = (3..6).map(|x| pixel(x, 1).to_vec()).collect();
        for (raster, direct) in raster_pixels.iter().zip(&direct_pixels) {
            assert!(
                raster
                    .iter()
                    .zip(direct)
                    .all(|(left, right)| left.abs_diff(*right) <= 6),
                "RasterSubscene must match an ordinary Image under final Scene.scale: \
                 raster={raster_pixels:?} direct={direct_pixels:?}"
            );
        }
        assert!(
            raster_pixels.windows(2).any(|pair| pair[0] != pair[1]),
            "high-frequency fixture must retain a comparison signal"
        );
    }

    #[test]
    fn raster_subscene_shadow_uses_completed_snapshot_alpha() {
        let root = r#"{
            "type": "RasterSubscene", "natural_size": [2, 2],
            "pos": [1, 0], "dst_size": [2, 2], "sampling": "nearest",
            "shadow": {
                "alpha": 1, "offset": [3, 0], "sigma": 0,
                "color": [255, 0, 0, 255]
            },
            "children": [
                { "type": "Rect", "pos": [0, 0], "size": [1, 2],
                  "fill": [255, 255, 255, 255], "blend": "src" }
            ]
        }"#;
        let scene: Scene =
            serde_json::from_str(&bare_scene_json((7, 2), root)).expect("scene parses");
        let rendered = render_scene_inner(&scene, HashMap::new()).expect("renders");
        let (pixels, width, _) = decode_pixels(&rendered);
        let pixel = |x: usize, y: usize| &pixels[(y * width as usize + x) * 4..][..4];

        assert_eq!(pixel(1, 0), &[255, 255, 255, 255]);
        assert_eq!(pixel(4, 0), &[255, 0, 0, 255]);
        assert_eq!(
            pixel(5, 0)[3],
            0,
            "shadow must follow the snapshot alpha, not its destination rectangle"
        );
    }

    #[test]
    fn raster_subscene_preflights_contract_before_child_asset_access() {
        let invalid_cases = [
            (
                r#""natural_size":[1,1],"pos":[0,0],"dst_size":[-1,1]"#,
                "dst_size",
            ),
            (
                r#""natural_size":[1,1],"pos":[0,0],"dst_size":[1,1],"alpha":2"#,
                "alpha",
            ),
            (
                r#""natural_size":[1,1],"pos":[0,0],"dst_size":[1,1],
                   "shadow":{"alpha":1,"offset":[0,0],"sigma":-1,"color":[0,0,0,255]}"#,
                "shadow parameters",
            ),
            (
                r#""natural_size":[1,1],"pos":[0,0],"dst_size":[1,1],
                   "sampling":"pillow_lanczos""#,
                "pillow_lanczos",
            ),
        ];
        for (fields, expected) in invalid_cases {
            let root = format!(
                r#"{{"type":"RasterSubscene",{fields},"children":[
                    {{"type":"Image","path":"missing.png","pos":[0,0],
                      "size":[1,1],"fit":"stretch"}}
                ]}}"#
            );
            let scene: Scene =
                serde_json::from_str(&bare_scene_json((1, 1), &root)).expect("scene parses");
            let err = expect_scene_error(&scene, HashMap::new());
            assert!(
                err.contains(expected) && !err.contains("missing.png"),
                "contract must fail before child assets: {err}"
            );
        }

        let masked_child = r#"{
            "type": "RasterSubscene", "natural_size": [1, 1],
            "pos": [0, 0], "dst_size": [1, 1], "children": [
                { "type": "Group", "size": [1, 1], "mask": "missing-mask.png",
                  "children": [
                    { "type": "Image", "path": "missing.png", "pos": [0, 0],
                      "size": [1, 1], "fit": "stretch", "blend": "paste_lerp" }
                  ] }
            ]
        }"#;
        let scene: Scene =
            serde_json::from_str(&bare_scene_json((1, 1), masked_child)).expect("scene parses");
        let err = expect_scene_error(&scene, HashMap::new());
        assert!(
            err.contains("paste_lerp Image inside a masked Group saveLayer")
                && !err.contains("missing.png"),
            "nested masked Group must remain outside paste_lerp's contract: {err}"
        );
    }

    #[test]
    fn raster_subscene_is_strict_and_accounts_nested_surface_peak() {
        let missing = r#"{
            "type": "RasterSubscene", "natural_size": [4, 4],
            "pos": [0, 0], "dst_size": [4, 4], "children": [
                { "type": "Image", "path": "missing.png", "pos": [0, 0],
                  "size": [4, 4], "fit": "stretch" }
            ]
        }"#;
        let scene: Scene =
            serde_json::from_str(&bare_scene_json((4, 4), missing)).expect("scene parses");
        let err = expect_scene_error(&scene, HashMap::new());
        assert!(
            err.contains("RasterSubscene asset load failed") && err.contains("missing.png"),
            "unexpected error: {err}"
        );

        let nested = r#"{
            "type": "RasterSubscene", "natural_size": [4, 4],
            "pos": [0, 0], "dst_size": [4, 4], "children": [
                { "type": "RasterSubscene", "natural_size": [4, 4],
                  "pos": [0, 0], "dst_size": [4, 4], "children": [] }
            ]
        }"#;
        let json = bare_scene_json((4, 4), nested).replace(
            r#""root":"#,
            r#""limits":{"max_node_pixels":16,"max_scene_bytes":191},"root":"#,
        );
        let scene: Scene = serde_json::from_str(&json).expect("scene parses");
        let err = expect_scene_error(&scene, HashMap::new());
        assert!(
            err.contains("scene limit"),
            "64-byte output + 128-byte nested peak must exceed 191: {err}"
        );

        let transformed = r#"{
            "type": "Transform", "matrix": [1, 0, 0, 1, 0, 0], "children": [
                { "type": "RasterSubscene", "natural_size": [1, 1],
                  "pos": [0, 0], "dst_size": [1, 1], "children": [] }
            ]
        }"#;
        let scene: Scene =
            serde_json::from_str(&bare_scene_json((1, 1), transformed)).expect("scene parses");
        let err = expect_scene_error(&scene, HashMap::new());
        assert!(
            err.contains("RasterSubscene inside Transform"),
            "unexpected error: {err}"
        );
    }

    #[test]
    fn raster_subscene_allows_paste_lerp_child_under_scene_scale() {
        let root = r#"{
            "type": "Group", "size": [2, 2], "mask": "mem:mask", "children": [
                { "type": "RasterSubscene", "natural_size": [1, 1],
                  "pos": [0, 0], "dst_size": [2, 2], "sampling": "nearest",
                  "children": [
                    { "type": "Rect", "pos": [0, 0], "size": [1, 1],
                      "fill": [0, 0, 255, 255], "blend": "src" },
                    { "type": "Image", "path": "mem:source", "pos": [0, 0],
                      "size": [1, 1], "fit": "stretch", "sampling": "nearest",
                      "blend": "paste_lerp" }
                  ] }
            ]
        }"#;
        let json =
            bare_scene_json((2, 2), root).replace("\"canvas\":", "\"scale\": 1.5, \"canvas\":");
        let scene: Scene = serde_json::from_str(&json).expect("scene parses");
        let mut mem = HashMap::new();
        mem.insert(
            "source".to_string(),
            rgba_mem_image(1, 1, &[255, 255, 255, 128]),
        );
        mem.insert("mask".to_string(), rgba_mem_image(1, 1, &[255; 4]));
        let rendered = render_scene_inner(&scene, mem).expect("renders");
        let (pixels, width, height) = decode_pixels(&rendered);

        assert_eq!((width, height), (3, 3));
        assert!(
            pixels[3].abs_diff(191) <= 1,
            "unexpected paste_lerp result: {:?}",
            &pixels[..4]
        );
    }

    #[test]
    fn unity_subscene_respects_node_and_scene_budgets() {
        let root = r#"{
            "type": "UnitySubscene", "size": [8, 8], "anchor": [4, 4],
            "object_scale": [1, 1], "post_scale": [1, 1], "rotation": 0,
            "children": []
        }"#;
        let node_limited = bare_scene_json((8, 8), root).replace(
            r#""root":"#,
            r#""limits":{"max_node_pixels":16,"max_scene_bytes":1024},"root":"#,
        );
        let scene: Scene = serde_json::from_str(&node_limited).expect("parses");
        let err = expect_scene_error(&scene, HashMap::new());
        assert!(err.contains("exceeds limit 16"), "unexpected error: {err}");

        let scene_limited = bare_scene_json((8, 8), root).replace(
            r#""root":"#,
            r#""limits":{"max_node_pixels":64,"max_scene_bytes":32},"root":"#,
        );
        let scene: Scene = serde_json::from_str(&scene_limited).expect("parses");
        let err = expect_scene_error(&scene, HashMap::new());
        assert!(err.contains("scene limit"), "unexpected error: {err}");
    }

    #[test]
    fn transform_rejects_unity_subscene_child() {
        let root = r#"{
            "type": "Transform", "matrix": [1, 0, 0, 1, 0, 0], "children": [
                { "type": "UnitySubscene", "size": [4, 4], "anchor": [2, 2],
                  "object_scale": [1, 1], "post_scale": [1, 1], "rotation": 0,
                  "children": [] }
            ]
        }"#;
        let scene: Scene = serde_json::from_str(&bare_scene_json((8, 8), root)).expect("parses");
        let err = expect_scene_error(&scene, HashMap::new());
        assert!(
            err.contains("UnitySubscene inside Transform"),
            "unexpected error: {err}"
        );
    }

    #[test]
    fn group_clip_restore_does_not_clip_following_sibling() {
        let root = r#"{
            "type": "Group", "offset": [0, 0], "size": [8, 8], "children": [
                { "type": "Group", "offset": [0, 0], "size": [2, 2],
                  "clip": { "kind": "rect" }, "children": [
                    { "type": "Rect", "pos": [0, 0], "size": [8, 8],
                      "fill": [255, 0, 0, 255] }
                  ] },
                { "type": "Rect", "pos": [5, 5], "size": [2, 2],
                  "fill": [0, 255, 0, 255] }
            ]
        }"#;
        let scene: Scene = serde_json::from_str(&bare_scene_json((8, 8), root)).expect("parses");
        let rendered = render_scene_inner(&scene, HashMap::new()).expect("renders");
        let (pixels, width, _) = decode_pixels(&rendered);
        let pixel = |x: usize, y: usize| &pixels[(y * width as usize + x) * 4..][..4];
        assert_eq!(pixel(1, 1), &[255, 0, 0, 255]);
        assert_eq!(
            pixel(5, 5),
            &[0, 255, 0, 255],
            "the clip save must be restored before drawing the next sibling"
        );
    }

    #[test]
    fn unity_subscene_missing_image_and_mask_fail_the_whole_scene() {
        let missing_image = r#"{
            "type": "UnitySubscene", "size": [4, 4], "anchor": [2, 2],
            "object_scale": [1, 1], "post_scale": [1, 1], "rotation": 0,
            "children": [
                { "type": "Image", "path": "missing.png", "pos": [0, 0],
                  "size": [4, 4], "fit": "stretch" }
            ]
        }"#;
        let scene: Scene =
            serde_json::from_str(&bare_scene_json((8, 8), missing_image)).expect("parses");
        let err = expect_scene_error(&scene, HashMap::new());
        assert!(
            err.contains("UnitySubscene asset load failed") && err.contains("missing.png"),
            "unexpected error: {err}"
        );

        let missing_mask = r#"{
            "type": "UnitySubscene", "size": [4, 4], "anchor": [2, 2],
            "object_scale": [1, 1], "post_scale": [1, 1], "rotation": 0,
            "children": [
                { "type": "Group", "offset": [0, 0], "size": [4, 4],
                  "mask": "missing-mask.png", "children": [] }
            ]
        }"#;
        let scene: Scene =
            serde_json::from_str(&bare_scene_json((8, 8), missing_mask)).expect("parses");
        let err = expect_scene_error(&scene, HashMap::new());
        assert!(
            err.contains("UnitySubscene asset load failed") && err.contains("missing-mask.png"),
            "unexpected error: {err}"
        );
    }

    #[test]
    fn unity_subscene_corrupt_mem_image_fails_the_whole_scene() {
        let root = r#"{
            "type": "UnitySubscene", "size": [4, 4], "anchor": [2, 2],
            "object_scale": [1, 1], "post_scale": [1, 1], "rotation": 0,
            "children": [
                { "type": "Image", "path": "mem:bad", "pos": [0, 0],
                  "size": [4, 4], "fit": "stretch" }
            ]
        }"#;
        let scene: Scene = serde_json::from_str(&bare_scene_json((8, 8), root)).expect("parses");
        let mut mem = HashMap::new();
        mem.insert(
            "bad".to_string(),
            MemImage::Encoded {
                data: Data::new_copy(b"not-an-image"),
                _owner: None,
            },
        );
        let err = expect_scene_error(&scene, mem);
        assert!(
            err.contains("failed to decode mem image"),
            "unexpected error: {err}"
        );
    }

    #[test]
    fn unity_subscene_preflight_precedes_child_asset_prepare() {
        let root = r#"{
            "type": "UnitySubscene", "size": [8, 8], "anchor": [4, 4],
            "object_scale": [1, 1], "post_scale": [1, 1], "rotation": 0,
            "children": [
                { "type": "Image", "path": "missing.png", "pos": [0, 0],
                  "size": [8, 8], "fit": "stretch" }
            ]
        }"#;
        let json = bare_scene_json((8, 8), root).replace(
            r#""root":"#,
            r#""limits":{"max_node_pixels":16,"max_scene_bytes":1024},"root":"#,
        );
        let scene: Scene = serde_json::from_str(&json).expect("parses");
        let err = expect_scene_error(&scene, HashMap::new());
        assert!(
            err.contains("exceeds limit 16") && !err.contains("missing.png"),
            "geometry must fail before child asset access: {err}"
        );
    }

    #[test]
    fn scene_budget_includes_output_mem_and_nested_subscene_peak() {
        let plain_root = r#"{ "type": "Group", "offset": [0, 0], "size": [8, 8], "children": [] }"#;
        let json = bare_scene_json((8, 8), plain_root).replace(
            r#""root":"#,
            r#""limits":{"max_node_pixels":64,"max_scene_bytes":300},"root":"#,
        );
        let scene: Scene = serde_json::from_str(&json).expect("parses");
        let mut mem = HashMap::new();
        mem.insert("unused".to_string(), rgba_mem_image(4, 4, &[0; 64]));
        let err = expect_scene_error(&scene, mem);
        assert!(
            err.contains("output, request buffers"),
            "256-byte output + 64-byte mem must exceed 300: {err}"
        );

        let nested = r#"{
            "type": "UnitySubscene", "size": [4, 4], "anchor": [4, 4],
            "object_scale": [1, 1], "post_scale": [1, 1], "rotation": 0,
            "children": [
                { "type": "UnitySubscene", "size": [4, 4], "anchor": [2, 2],
                  "object_scale": [1, 1], "post_scale": [1, 1], "rotation": 0,
                  "children": [] }
            ]
        }"#;
        let json = bare_scene_json((8, 8), nested).replace(
            r#""root":"#,
            r#""limits":{"max_node_pixels":64,"max_scene_bytes":350},"root":"#,
        );
        let scene: Scene = serde_json::from_str(&json).expect("parses");
        let err = expect_scene_error(&scene, HashMap::new());
        assert!(
            err.contains("output, request buffers"),
            "256-byte output + 128-byte nested peak must exceed 350: {err}"
        );
    }
}
