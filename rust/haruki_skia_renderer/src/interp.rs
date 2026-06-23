//! General Render IR v2 interpreter.
//!
//! Renders a `Scene` (tree of `Node`s) with Skia. Coordinates are resolved to
//! absolute canvas space (the canvas matrix stays identity), so backdrop
//! snapshots used by `BlurGlass` line up with the drawing coordinate system.
//! Reuses infrastructure from `lib.rs` (`pub(crate)` items): image decode,
//! font loading, surface encode, blur glass, triangle background, cover image.

use std::collections::HashMap;
use std::path::PathBuf;

use skia_safe::{
    AlphaType, BlendMode, BlurStyle, Canvas, ClipOp, Color, Color4f, ColorType, FilterMode, Font,
    IRect, Image, ImageInfo, MaskFilter, MipmapMode, Paint, PaintStyle, Point, RRect, Rect,
    RoundOut, SamplingOptions, Shader, Surface, TextBlob, TileMode, Typeface,
    canvas::SrcRectConstraint, color_filters, gradient, image::CachingHint, image_filters, surfaces,
};

use crate::ir::*;
use crate::{
    RenderedImage, draw_blur_glass_rect, draw_sekai_triangle_background, encode_surface,
    load_image_cached, load_typeface,
};

/// Resolved typefaces for the scene's font roles.
struct FontRegistry {
    regular: Typeface,
    bold: Typeface,
    heavy: Typeface,
    /// Opt-in color-emoji typeface; emoji codepoints route here when present.
    emoji: Option<Typeface>,
}

impl FontRegistry {
    fn build(fonts: &FontsIr) -> Self {
        let regular = load_typeface(&fonts.dir, &fonts.default);
        let bold = load_typeface(&fonts.dir, &fonts.bold);
        let heavy = match &fonts.heavy {
            Some(name) => load_typeface(&fonts.dir, name),
            None => bold.clone(),
        };
        // Only load an emoji typeface when explicitly configured (otherwise emoji codepoints
        // keep falling back to the main font, unchanged).
        let emoji = fonts
            .emoji
            .as_ref()
            .map(|name| load_typeface(&fonts.dir, name));
        Self {
            regular,
            bold,
            heavy,
            emoji,
        }
    }

    fn resolve(&self, role: FontRole) -> &Typeface {
        match role {
            FontRole::Bold => &self.bold,
            FontRole::Heavy => &self.heavy,
            FontRole::Default => &self.regular,
        }
    }

    fn emoji_font(&self, size: f32) -> Option<Font> {
        self.emoji
            .as_ref()
            .map(|t| Font::from_typeface(t.clone(), size))
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
        | 0x200D)              // zero-width joiner (keep ZWJ sequences together)
}

/// Split text into consecutive (is_emoji, run) segments for per-font drawing.
fn classify_runs(text: &str) -> Vec<(bool, String)> {
    let mut runs: Vec<(bool, String)> = Vec::new();
    for ch in text.chars() {
        let e = is_emoji(ch);
        match runs.last_mut() {
            Some(last) if last.0 == e => last.1.push(ch),
            _ => runs.push((e, ch.to_string())),
        }
    }
    runs
}

fn run_font<'a>(is_emoji_run: bool, main: &'a Font, emoji: Option<&'a Font>) -> &'a Font {
    if is_emoji_run { emoji.unwrap_or(main) } else { main }
}

/// Interpreter state shared across the node tree (assets, fonts, canvas dims).
struct Interp {
    base: PathBuf,
    fonts: FontRegistry,
    cache: HashMap<String, Image>,
    canvas_w: f32,
    canvas_h: f32,
}

impl Interp {
    fn load(&mut self, path: &str) -> Option<Image> {
        if !is_safe_asset_path(path) {
            return None;
        }
        if let Some(image) = self.cache.get(path) {
            return Some(image.clone());
        }
        // L1 (per-render, path-keyed) misses fall through to the process-wide decoded-image
        // cache, which validates by mtime/size and persists across requests.
        match load_image_cached(&self.base, path) {
            Ok(image) => {
                self.cache.insert(path.to_string(), image.clone());
                Some(image)
            }
            Err(_) => None,
        }
    }
}

pub(crate) fn render_scene_inner(scene: &Scene) -> Result<RenderedImage, String> {
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
        cache: HashMap::new(),
        canvas_w: scene.canvas.width as f32,
        canvas_h: scene.canvas.height as f32,
    };

    if let Some(background) = &scene.background {
        render_node(&mut surface, &mut interp, (0.0, 0.0), background);
    }
    render_node(&mut surface, &mut interp, (0.0, 0.0), &scene.root);

    encode_surface(surface, &scene.export_format, scene.jpg_quality)
}

fn render_node(surface: &mut Surface, interp: &mut Interp, off: (f32, f32), node: &Node) {
    match node {
        Node::Group(group) => {
            let child_off = (off.0 + group.offset[0], off.1 + group.offset[1]);
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
        }
        Node::Rect(rect) => render_rect(surface.canvas(), rect, off),
        Node::RoundRect(rr) => render_round_rect(surface.canvas(), rr, off),
        Node::PieSlice(pie) => render_pie_slice(surface.canvas(), pie, off),
        Node::Image(image) => {
            if let Some(decoded) = interp.load(&image.path) {
                draw_image_fit(surface.canvas(), &decoded, image, off);
            }
        }
        Node::Text(text) => {
            let abs = (text.pos[0] + off.0, text.pos[1] + off.1);
            // Adaptive color samples the backdrop (needs the surface), so resolve it here and
            // pass a solid fill down; otherwise use the node's own fill (solid or gradient).
            let adaptive_fill;
            let fill: &Fill = if let Some(ad) = &text.adaptive {
                let color =
                    resolve_adaptive_color(surface, &interp.fonts, text, abs, ad);
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
            // The glass only samples its own region (panel + a small blur margin), so snapshot
            // just that sub-rect instead of the whole canvas. A full-canvas image_snapshot per
            // panel is forced to a full copy (the shadow write right after breaks copy-on-write),
            // which is costly when there is one panel per card.
            let mut bounds = rect.with_outset((12.0, 12.0));
            let canvas_rect = Rect::from_xywh(0.0, 0.0, interp.canvas_w, interp.canvas_h);
            let backdrop = if bounds.intersect(canvas_rect) {
                let ibounds: IRect = bounds.round_out();
                surface
                    .image_snapshot_with_bounds(ibounds)
                    .map(|img| (img, (ibounds.left as f32, ibounds.top as f32)))
            } else {
                None
            };
            let canvas = surface.canvas();
            draw_blur_glass_rect(
                canvas,
                backdrop.as_ref().map(|(img, origin)| (img, *origin)),
                rect,
                glass.radius,
                color_of(glass.fill),
                glass.shadow_alpha,
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
                bg.size_fixed_rate,
            );
        }
        Node::ImageBg(bg) => {
            if let Some(decoded) = interp.load(&bg.path) {
                draw_image_bg(surface.canvas(), &decoded, interp.canvas_w, interp.canvas_h, bg);
            }
        }
        Node::Watermark(watermark) => {
            let canvas = surface.canvas();
            let font = Font::from_typeface(
                interp.fonts.resolve(watermark.font.role).clone(),
                watermark.font.size,
            );
            let emoji = interp.fonts.emoji_font(watermark.font.size);
            let emoji_ref = emoji.as_ref();
            let mut paint = Paint::default();
            paint.set_anti_alias(true);
            paint.set_color(color_of(watermark.fill));
            for line in &watermark.lines {
                let abs = (line.pos[0] + off.0, line.pos[1] + off.1);
                let (x, y) =
                    text_layout(&font, emoji_ref, &line.text, abs, line.align, Baseline::CjkTop, 0.0);
                draw_text_core(canvas, &font, emoji_ref, &line.text, x, y, 0.0, &paint);
            }
        }
    }
}

fn color_of(c: Color4) -> Color {
    Color::from_argb(c[3], c[0], c[1], c[2])
}

fn image_sampling() -> SamplingOptions {
    // Bilinear + mipmaps. For mild downscales (thumbnails ~1.3x) this stays at the base
    // level and matches Pillow's soft BILINEAR character; for large downscales (skill icon
    // ~3x) the mipmaps area-average so it doesn't alias the way plain bilinear does.
    SamplingOptions::new(FilterMode::Linear, MipmapMode::Linear)
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
fn resolve_gradient_stops(stops: &[GradientStop], fallback: [Color4; 2]) -> (Vec<Color4f>, Vec<f32>) {
    if stops.len() >= 2 {
        let mut sorted = stops.to_vec();
        sorted.sort_by(|a, b| a.pos.partial_cmp(&b.pos).unwrap_or(std::cmp::Ordering::Equal));
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

fn gradient_shader(spec: &GradientSpec, off: (f32, f32)) -> Option<Shader> {
    match spec {
        // `method` (combine vs separate) is honored as combine — Skia's native projection.
        GradientSpec::Linear { c1, c2, stops, p1, p2, .. } => {
            let fallback = [c1.unwrap_or([0, 0, 0, 255]), c2.unwrap_or([0, 0, 0, 255])];
            let (colors, positions) = resolve_gradient_stops(stops, fallback);
            let grad_colors = gradient::Colors::new(&colors, Some(&positions), TileMode::Clamp, None);
            let grad = gradient::Gradient::new(grad_colors, gradient::Interpolation::default());
            gradient::shaders::linear_gradient(
                (
                    Point::new(p1[0] + off.0, p1[1] + off.1),
                    Point::new(p2[0] + off.0, p2[1] + off.1),
                ),
                &grad,
                None,
            )
        }
        GradientSpec::Radial { c1, c2, stops, center, radius_px } => {
            // Painter convention: stop 0 = center (c2), stop 1 = edge (c1).
            let fallback = [c2.unwrap_or([0, 0, 0, 255]), c1.unwrap_or([0, 0, 0, 255])];
            let (colors, positions) = resolve_gradient_stops(stops, fallback);
            let grad_colors = gradient::Colors::new(&colors, Some(&positions), TileMode::Clamp, None);
            let grad = gradient::Gradient::new(grad_colors, gradient::Interpolation::default());
            gradient::shaders::radial_gradient(
                (Point::new(center[0] + off.0, center[1] + off.1), radius_px.max(0.01)),
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

fn draw_image_fit(canvas: &Canvas, image: &Image, node: &ImageNode, off: (f32, f32)) {
    let iw = image.width() as f32;
    let ih = image.height() as f32;
    if iw <= 0.0 || ih <= 0.0 {
        return;
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
            (None, Rect::from_xywh(x + (rw - w) * 0.5, y + (rh - h) * 0.5, w, h))
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
    let sampling = image_sampling();
    let src_arg = src.as_ref().map(|s| (s, SrcRectConstraint::Strict));
    let alpha = node.alpha.clamp(0.0, 1.0);

    // Alpha-silhouette drop shadow, drawn behind the image (mirrors Painter paste shadow).
    if let Some(sh) = &node.shadow {
        let mut shadow_paint = Paint::default();
        shadow_paint.set_anti_alias(true);
        let strength = (sh.alpha.clamp(0.0, 1.0) * (sh.color[3] as f32 / 255.0) * alpha).clamp(0.0, 1.0);
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
        let sdst = Rect::from_xywh(dst.left + sh.offset[0], dst.top + sh.offset[1], dst.width(), dst.height());
        canvas.draw_image_rect_with_sampling_options(image, src_arg, sdst, sampling, &shadow_paint);
    }

    let mut paint = Paint::default();
    paint.set_anti_alias(true);
    paint.set_alpha_f(alpha);
    if let Some(tint) = &node.tint {
        paint.set_color_filter(tint_filter(tint));
    }
    canvas.draw_image_rect_with_sampling_options(image, src_arg, dst, sampling, &paint);
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
    let sampling = image_sampling();
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
        TintMode::Multiply => {
            color_filters::blend(Color::from_argb(c[3], c[0], c[1], c[2]), BlendMode::Modulate)
        }
        // SrcOver a translucent color over each pixel = lerp toward color by `strength`.
        TintMode::Mix => {
            let a = (tint.strength.clamp(0.0, 1.0) * 255.0).round() as u8;
            color_filters::blend(Color::from_argb(a, c[0], c[1], c[2]), BlendMode::SrcOver)
        }
    }
}

/// Total advance of `text` (with the emoji font for emoji runs) including `letter_spacing`.
fn measure_advance(main: &Font, emoji: Option<&Font>, text: &str, letter_spacing: f32) -> f32 {
    let has_emoji = emoji.is_some() && text.chars().any(is_emoji);
    // Fast path: plain text, no spacing, no emoji routing — a single measure_str.
    if !has_emoji && letter_spacing == 0.0 {
        return main.measure_str(text, None).0;
    }
    if letter_spacing == 0.0 {
        return classify_runs(text)
            .iter()
            .map(|(e, run)| run_font(*e, main, emoji).measure_str(run, None).0)
            .sum();
    }
    let mut total = 0.0;
    let mut count = 0;
    for (e, run) in classify_runs(text) {
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
    let advance = measure_advance(main, emoji, text, letter_spacing);
    let x = match align {
        HAlign::Left => abs.0,
        HAlign::Center => abs.0 - advance * 0.5,
        HAlign::Right => abs.0 - advance,
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
    let has_emoji = emoji.is_some() && text.chars().any(is_emoji);
    if !has_emoji && letter_spacing == 0.0 {
        if let Some(blob) = TextBlob::new(text, main) {
            canvas.draw_text_blob(&blob, Point::new(x, y), paint);
        }
        return;
    }
    let mut cx = x;
    for (e, run) in classify_runs(text) {
        let font = run_font(e, main, emoji);
        if letter_spacing == 0.0 {
            if let Some(blob) = TextBlob::new(&run, font) {
                canvas.draw_text_blob(&blob, Point::new(cx, y), paint);
            }
            cx += font.measure_str(&run, None).0;
        } else {
            for ch in run.chars() {
                let mut buf = [0u8; 4];
                let s: &str = ch.encode_utf8(&mut buf);
                if let Some(blob) = TextBlob::new(s, font) {
                    canvas.draw_text_blob(&blob, Point::new(cx, y), paint);
                }
                cx += font.measure_str(s, None).0 + letter_spacing;
            }
        }
    }
}

/// Draw a `TextNode`: optional outline under the fill (solid or gradient), with letter spacing
/// and emoji-font routing.
fn draw_styled_text(canvas: &Canvas, fonts: &FontRegistry, node: &TextNode, abs: (f32, f32), off: (f32, f32), fill: &Fill) {
    if node.text.is_empty() {
        return;
    }
    let font = Font::from_typeface(fonts.resolve(node.font.role).clone(), node.font.size);
    let emoji = fonts.emoji_font(node.font.size);
    let emoji_ref = emoji.as_ref();
    let (x, y) = text_layout(&font, emoji_ref, &node.text, abs, node.align, node.baseline, node.letter_spacing);

    if let Some(stroke) = &node.stroke {
        let mut sp = Paint::default();
        sp.set_anti_alias(true);
        sp.set_style(PaintStyle::Stroke);
        sp.set_stroke_width(stroke.width);
        sp.set_color(color_of(stroke.color));
        draw_text_core(canvas, &font, emoji_ref, &node.text, x, y, node.letter_spacing, &sp);
    }

    let mut fp = Paint::default();
    fp.set_anti_alias(true);
    apply_fill(&mut fp, fill, off);
    draw_text_core(canvas, &font, emoji_ref, &node.text, x, y, node.letter_spacing, &fp);
}

/// Pick the adaptive fill color from the average luminance of the backdrop under the text box.
fn resolve_adaptive_color(
    surface: &mut Surface,
    fonts: &FontRegistry,
    node: &TextNode,
    abs: (f32, f32),
    ad: &AdaptiveColor,
) -> Color4 {
    let font = Font::from_typeface(fonts.resolve(node.font.role).clone(), node.font.size);
    let emoji = fonts.emoji_font(node.font.size);
    let emoji_ref = emoji.as_ref();
    let (x, y) = text_layout(&font, emoji_ref, &node.text, abs, node.align, node.baseline, node.letter_spacing);
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
    if lum < ad.threshold { ad.light } else { ad.dark }
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
        render_scene_inner(&scene).expect("renders")
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
            &rendered.bytes[..8],
            &[0x89, b'P', b'N', b'G', 0x0d, 0x0a, 0x1a, 0x0a]
        );
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
            &rendered.bytes[..8],
            &[0x89, b'P', b'N', b'G', 0x0d, 0x0a, 0x1a, 0x0a]
        );
    }

    #[test]
    fn parses_image_extensions() {
        // tint + alpha-silhouette shadow + crop fit must deserialize and render (the asset is
        // absent in the test base dir, so the image is skipped, but parsing must succeed).
        let json = scene_json(
            r#"
            { "type": "Image", "pos": [4, 4], "size": [20, 20], "path": "missing.png",
              "fit": "crop",
              "tint": { "color": [255, 128, 0, 255], "mode": "multiply" },
              "shadow": { "alpha": 0.6, "offset": [4, 4], "sigma": 3.0, "color": [0,0,0,255] } }
            "#,
        );
        let rendered = render(&json);
        assert_eq!(rendered.width, 64);
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
            &rendered.bytes[..8],
            &[0x89, b'P', b'N', b'G', 0x0d, 0x0a, 0x1a, 0x0a]
        );
    }

    #[test]
    fn rejects_wrong_version() {
        let json = scene_json("").replace("\"version\": 2", "\"version\": 1");
        let scene: Scene = serde_json::from_str(&json).expect("scene parses");
        assert!(render_scene_inner(&scene).is_err());
    }
}
