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
    Data, FilterMode, Font, FontHinting, IRect, Image, ImageInfo, MaskFilter, Matrix, MipmapMode,
    Paint, PaintStyle, Point, RRect, Rect, RoundOut, SamplingOptions, Shader, Surface, TextBlob,
    TileMode, Typeface, canvas::SrcRectConstraint, color_filters, gradient, image::CachingHint,
    image_filters, surfaces,
};

use crate::ir::*;
use crate::{
    AssetDescriptor, NativeMetrics, RasterCacheOutcome, RenderedImage, decode_asset_descriptor,
    draw_blur_glass_rect, draw_sekai_triangle_background, encode_surface, load_asset_descriptor,
    load_typeface_checked, raster_cache_snapshot, rasterize_asset_cached,
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

#[derive(Clone, Copy)]
struct TextFontProfile {
    hinting: FontHinting,
    force_auto_hinting: bool,
    linear_metrics: bool,
}

static TEXT_FONT_PROFILE: OnceLock<TextFontProfile> = OnceLock::new();
static TEXT_COVERAGE_GAMMA: OnceLock<f32> = OnceLock::new();
static PROFILE_ENABLED: OnceLock<bool> = OnceLock::new();

fn profile_enabled() -> bool {
    *PROFILE_ENABLED.get_or_init(|| {
        std::env::var("HARUKI_SKIA_PROFILE")
            .ok()
            .is_some_and(|value| !matches!(value.as_str(), "" | "0" | "false" | "False"))
    })
}

fn default_text_font_profile() -> TextFontProfile {
    if cfg!(target_os = "linux") {
        TextFontProfile {
            hinting: FontHinting::Slight,
            force_auto_hinting: false,
            linear_metrics: false,
        }
    } else {
        TextFontProfile {
            hinting: FontHinting::Normal,
            force_auto_hinting: false,
            linear_metrics: false,
        }
    }
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

fn text_font(typeface: Typeface, size: f32) -> Font {
    let profile = TEXT_FONT_PROFILE.get_or_init(|| {
        let default = default_text_font_profile();
        match std::env::var("HARUKI_SKIA_TEXT_HINTING")
            .ok()
            .map(|name| name.to_ascii_lowercase())
            .as_deref()
        {
            Some("none") => TextFontProfile {
                hinting: FontHinting::None,
                force_auto_hinting: false,
                linear_metrics: false,
            },
            Some("slight") => TextFontProfile {
                hinting: FontHinting::Slight,
                force_auto_hinting: false,
                linear_metrics: false,
            },
            Some("full") => TextFontProfile {
                hinting: FontHinting::Full,
                force_auto_hinting: false,
                linear_metrics: false,
            },
            Some("auto") => TextFontProfile {
                hinting: FontHinting::Full,
                force_auto_hinting: true,
                linear_metrics: false,
            },
            Some("linear") => TextFontProfile {
                hinting: FontHinting::Normal,
                force_auto_hinting: false,
                linear_metrics: true,
            },
            Some("normal") => TextFontProfile {
                hinting: FontHinting::Normal,
                force_auto_hinting: false,
                linear_metrics: false,
            },
            _ => default,
        }
    });
    let mut font = Font::from_typeface(typeface, size);
    font.set_hinting(profile.hinting)
        .set_force_auto_hinting(profile.force_auto_hinting)
        .set_linear_metrics(profile.linear_metrics);
    font
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
    canvas_w: f32,
    canvas_h: f32,
    /// True while rendering inside a `Transform` subtree (non-identity CTM). Image draws must
    /// then sample exactly once through the CTM, so `draw_image_node` skips the pre-rasterized
    /// raster-cache path (which would resample its integral-size intermediate a second time).
    in_transform: bool,
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
    let mut surface = surfaces::raster_n32_premul((scene.canvas.width, scene.canvas.height))
        .ok_or_else(|| "failed to create raster surface".to_string())?;
    let mut interp = Interp {
        base: PathBuf::from(&scene.assets_base_dir),
        fonts: FontRegistry::build(&scene.fonts),
        direct_images: HashMap::new(),
        asset_descriptors: HashMap::new(),
        mem_images,
        canvas_w: scene.canvas.width as f32,
        canvas_h: scene.canvas.height as f32,
        in_transform: false,
        metrics: NativeMetrics::default(),
    };
    interp.metrics.font_fallbacks = interp.fonts.fallbacks;
    interp.metrics.setup_elapsed = total_started.elapsed().as_secs_f64();

    // SdfQuad field references are validated up front so a bad one fails the WHOLE scene
    // (-> PyRuntimeError -> Python fail-open to Pillow) instead of silently skipping glyphs.
    if let Some(background) = &scene.background {
        validate_sdf_quad_fields(background, &interp.mem_images)?;
    }
    validate_sdf_quad_fields(&scene.root, &interp.mem_images)?;

    prewarm_scene_images(scene, &mut interp);

    let draw_started = Instant::now();
    if let Some(background) = &scene.background {
        render_node(&mut surface, &mut interp, (0.0, 0.0), background);
    }
    render_node(&mut surface, &mut interp, (0.0, 0.0), &scene.root);
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

fn render_node(surface: &mut Surface, interp: &mut Interp, off: (f32, f32), node: &Node) {
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
                render_node(surface, interp, child_off, child);
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
                render_node(surface, interp, (0.0, 0.0), child);
            }
            interp.in_transform = was_in_transform;
            surface.canvas().restore_to_count(save_count);
        }
        Node::Rect(rect) => render_rect(surface.canvas(), rect, off),
        Node::RoundRect(rr) => render_round_rect(surface.canvas(), rr, off),
        Node::PieSlice(pie) => render_pie_slice(surface.canvas(), pie, off),
        Node::Image(image) => draw_image_node(surface.canvas(), interp, image, off),
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
                        image_sampling(node.sampling),
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
        Node::Text(text) => {
            let abs = (text.pos[0] + off.0, text.pos[1] + off.1);
            // Adaptive color samples the backdrop (needs the surface), so resolve it here and
            // pass a solid fill down; otherwise use the node's own fill (solid or gradient).
            let adaptive_fill;
            let fill: &Fill = if let Some(ad) = &text.adaptive {
                if ad.pixelwise {
                    // Per-pixel light/dark selection needs its own masked draw path.
                    draw_pixelwise_adaptive_text(surface, &interp.fonts, text, abs, off, ad);
                    return;
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
            }
        }
        Node::Watermark(watermark) => {
            let canvas = surface.canvas();
            let font = text_font(
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
}

fn color_of(c: Color4) -> Color {
    Color::from_argb(c[3], c[0], c[1], c[2])
}

fn image_sampling(mode: ImageSampling) -> SamplingOptions {
    // Bilinear + mipmaps. For mild downscales (thumbnails ~1.3x) this stays at the base
    // level and matches Pillow's soft BILINEAR character; for large downscales (skill icon
    // ~3x) the mipmaps area-average so it doesn't alias the way plain bilinear does.
    match mode {
        ImageSampling::Nearest => SamplingOptions::default(),
        ImageSampling::Linear => SamplingOptions::new(FilterMode::Linear, MipmapMode::None),
        ImageSampling::Cubic => CubicResampler::mitchell().into(),
        ImageSampling::CatmullRom => CubicResampler::catmull_rom().into(),
        ImageSampling::LinearMipmap => SamplingOptions::new(FilterMode::Linear, MipmapMode::Linear),
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

fn render_rect(canvas: &Canvas, node: &RectNode, off: (f32, f32)) {
    let rect = Rect::from_xywh(
        node.pos[0] + off.0,
        node.pos[1] + off.1,
        node.size[0],
        node.size[1],
    );
    if let Some(fill) = &node.fill {
        canvas.draw_rect(rect, &fill_paint(fill, off));
    }
    if let Some(stroke) = &node.stroke {
        canvas.draw_rect(rect, &stroke_paint(stroke, node.stroke_width, off));
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
            if !image.path.starts_with("mem:") && seen.insert(image_prewarm_key(image)) =>
        {
            requests.push(ImagePrewarmRequest { node: image, off });
        }
        // Deliberately do NOT recurse into Transform: a prewarm target size is only meaningful
        // under identity CTM — under the matrix the device footprint differs from the node's
        // dst size, and `draw_image_node` skips the raster cache inside a Transform anyway, so
        // a prewarmed entry could never be consumed.
        Node::Transform(_) => {}
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
            image_sampling(request.node.sampling),
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
    }
}

fn draw_image_node(canvas: &Canvas, interp: &mut Interp, node: &ImageNode, off: (f32, f32)) {
    if node.path.starts_with("mem:") {
        interp.metrics.raster_cache_bypasses += 1;
        if let Some(image) = interp.load_mem(&node.path)
            && let Some(placement) = image_placement(image.width(), image.height(), node, off)
        {
            draw_image_placed(
                canvas,
                &image,
                placement,
                image_sampling(node.sampling),
                node,
            );
        }
        return;
    }

    let (descriptor, source) = match interp.describe_asset(&node.path) {
        Ok(loaded) => loaded,
        Err(err) => {
            eprintln!(
                "haruki_skia_renderer: asset load failed, node skipped: {} ({err})",
                node.path
            );
            return;
        }
    };
    let Some(placement) = image_placement(descriptor.width, descriptor.height, node, off) else {
        return;
    };
    let sampling = image_sampling(node.sampling);

    // Inside a Transform the CTM is non-identity: the raster cache pre-rasterizes at the
    // integral dst size and drawing that intermediate would resample it a SECOND time through
    // the CTM. Sampling must happen exactly once (source pixels -> device through the matrix),
    // so skip the cache and draw the decoded source directly.
    if interp.in_transform {
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
                return;
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
                eprintln!(
                    "haruki_skia_renderer: asset decode failed, node skipped: {} ({err})",
                    node.path
                );
                return;
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
    let sampling = image_sampling(ImageSampling::default());
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
    let font = text_font(fonts.resolve_ref(&node.font).clone(), node.font.size);
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
    let font = text_font(fonts.resolve_ref(&node.font).clone(), node.font.size);
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
    let font = text_font(fonts.resolve_ref(&node.font).clone(), node.font.size);
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
        let nearest = image_sampling(ImageSampling::Nearest);
        assert_eq!(nearest.filter, FilterMode::Nearest);
        assert_eq!(nearest.mipmap, MipmapMode::None);

        let linear = image_sampling(ImageSampling::Linear);
        assert_eq!(linear.filter, FilterMode::Linear);
        assert_eq!(linear.mipmap, MipmapMode::None);

        let cubic = image_sampling(ImageSampling::Cubic);
        assert!(cubic.use_cubic);
        // "cubic" stays Mitchell (B = C = 1/3) — it must not be repurposed as Catmull-Rom.
        assert_eq!(cubic.cubic.b, 1.0 / 3.0);
        assert_eq!(cubic.cubic.c, 1.0 / 3.0);

        let catmull = image_sampling(ImageSampling::CatmullRom);
        assert!(catmull.use_cubic);
        // Catmull-Rom = Keys a=-0.5 (PIL BICUBIC): B = 0, C = 0.5.
        assert_eq!(catmull.cubic.b, 0.0);
        assert_eq!(catmull.cubic.c, 0.5);

        let parsed: ImageSampling =
            serde_json::from_str("\"catmull_rom\"").expect("catmull_rom variant parses");
        assert_eq!(parsed, ImageSampling::CatmullRom);

        let mipmap = image_sampling(ImageSampling::LinearMipmap);
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
}
