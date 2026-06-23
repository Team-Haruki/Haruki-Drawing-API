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
    Canvas, ClipOp, Color, CubicResampler, Font, Image, Paint, PaintStyle, Point, RRect, Rect,
    SamplingOptions, Surface, TextBlob, TileMode, Typeface, canvas::SrcRectConstraint, gradient,
    surfaces,
};

use crate::ir::*;
use crate::{
    RenderedImage, draw_blur_glass_rect, draw_cover_image, draw_sekai_triangle_background,
    encode_surface, load_image, load_typeface,
};

/// Resolved typefaces for the scene's font roles.
struct FontRegistry {
    regular: Typeface,
    bold: Typeface,
    heavy: Typeface,
}

impl FontRegistry {
    fn build(fonts: &FontsIr) -> Self {
        let regular = load_typeface(&fonts.dir, &fonts.default);
        let bold = load_typeface(&fonts.dir, &fonts.bold);
        let heavy = match &fonts.heavy {
            Some(name) => load_typeface(&fonts.dir, name),
            None => bold.clone(),
        };
        Self {
            regular,
            bold,
            heavy,
        }
    }

    fn resolve(&self, role: FontRole) -> &Typeface {
        match role {
            FontRole::Bold => &self.bold,
            FontRole::Heavy => &self.heavy,
            FontRole::Default => &self.regular,
        }
    }
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
        match load_image(&self.base, path) {
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
            draw_one_text(
                surface.canvas(),
                &interp.fonts,
                &text.text,
                (text.pos[0] + off.0, text.pos[1] + off.1),
                text.font.role,
                text.font.size,
                text.align,
                text.baseline,
                color_of(text.fill),
            );
        }
        Node::BlurGlass(glass) => {
            let backdrop = surface.image_snapshot();
            let canvas = surface.canvas();
            let rect = Rect::from_xywh(
                glass.pos[0] + off.0,
                glass.pos[1] + off.1,
                glass.size[0],
                glass.size[1],
            );
            draw_blur_glass_rect(
                canvas,
                &backdrop,
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
            );
        }
        Node::ImageBg(bg) => {
            if let Some(decoded) = interp.load(&bg.path) {
                let full = Rect::from_xywh(0.0, 0.0, interp.canvas_w, interp.canvas_h);
                draw_cover_image(surface.canvas(), &decoded, full, 1.0);
            }
        }
        Node::Watermark(watermark) => {
            for line in &watermark.lines {
                draw_one_text(
                    surface.canvas(),
                    &interp.fonts,
                    &line.text,
                    (line.pos[0] + off.0, line.pos[1] + off.1),
                    watermark.font.role,
                    watermark.font.size,
                    line.align,
                    Baseline::CjkTop,
                    color_of(watermark.fill),
                );
            }
        }
    }
}

fn color_of(c: Color4) -> Color {
    Color::from_argb(c[3], c[0], c[1], c[2])
}

fn cubic_sampling() -> SamplingOptions {
    SamplingOptions::from(CubicResampler::catmull_rom())
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

/// A paint pre-configured with the node's fill (solid or gradient shader).
fn fill_paint(fill: &Fill, off: (f32, f32)) -> Paint {
    let mut paint = Paint::default();
    paint.set_anti_alias(true);
    paint.set_style(PaintStyle::Fill);
    match fill {
        Fill::Solid(c) => {
            paint.set_color(color_of(*c));
        }
        Fill::Gradient(spec) => match spec {
            GradientSpec::Linear { c1, c2, p1, p2, .. } => {
                let colors = [color_of(*c1).into(), color_of(*c2).into()];
                let gradient_colors =
                    gradient::Colors::new_evenly_spaced(&colors, TileMode::Clamp, None);
                let grad =
                    gradient::Gradient::new(gradient_colors, gradient::Interpolation::default());
                if let Some(shader) = gradient::shaders::linear_gradient(
                    (
                        Point::new(p1[0] + off.0, p1[1] + off.1),
                        Point::new(p2[0] + off.0, p2[1] + off.1),
                    ),
                    &grad,
                    None,
                ) {
                    paint.set_shader(shader);
                } else {
                    paint.set_color(color_of(*c2));
                }
            }
            // MVP: radial rendered as its center color; full radial shader is a later pass.
            GradientSpec::Radial { c2, .. } => {
                paint.set_color(color_of(*c2));
            }
        },
    }
    paint
}

fn stroke_paint(color: Color4, width: f32) -> Paint {
    let mut paint = Paint::default();
    paint.set_anti_alias(true);
    paint.set_style(PaintStyle::Stroke);
    paint.set_stroke_width(width);
    paint.set_color(color_of(color));
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
    if let Some(stroke) = node.stroke {
        canvas.draw_rect(rect, &stroke_paint(stroke, node.stroke_width));
    }
}

fn render_round_rect(canvas: &Canvas, node: &RoundRectNode, off: (f32, f32)) {
    let rect = Rect::from_xywh(
        node.pos[0] + off.0,
        node.pos[1] + off.1,
        node.size[0],
        node.size[1],
    );
    let radii = corner_radii(node.radius, &node.corners);
    let rrect = RRect::new_rect_radii(rect, &radii);
    if let Some(fill) = &node.fill {
        canvas.draw_rrect(rrect, &fill_paint(fill, off));
    }
    if let Some(stroke) = node.stroke {
        canvas.draw_rrect(rrect, &stroke_paint(stroke, node.stroke_width));
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
    if let Some(stroke) = node.stroke {
        canvas.draw_arc(
            oval,
            node.start_angle,
            sweep,
            true,
            &stroke_paint(stroke, node.stroke_width),
        );
    }
}

fn draw_image_fit(canvas: &Canvas, image: &Image, node: &ImageNode, off: (f32, f32)) {
    let iw = image.width() as f32;
    let ih = image.height() as f32;
    if iw <= 0.0 || ih <= 0.0 {
        return;
    }
    let x = node.pos[0] + off.0;
    let y = node.pos[1] + off.1;
    let mut paint = Paint::default();
    paint.set_anti_alias(true);
    paint.set_alpha_f(node.alpha.clamp(0.0, 1.0));
    let sampling = cubic_sampling();

    match node.fit {
        Fit::Stretch => {
            let dst = Rect::from_xywh(x, y, node.size[0], node.size[1]);
            canvas.draw_image_rect_with_sampling_options(image, None, dst, sampling, &paint);
        }
        Fit::Width => {
            let w = node.size[0];
            let h = w * ih / iw;
            let dst = Rect::from_xywh(x, y, w, h);
            canvas.draw_image_rect_with_sampling_options(image, None, dst, sampling, &paint);
        }
        Fit::Contain => {
            let dst = Rect::from_xywh(x, y, node.size[0], node.size[1]);
            let scale = (dst.width() / iw).min(dst.height() / ih);
            let w = iw * scale;
            let h = ih * scale;
            let fitted = Rect::from_xywh(
                dst.left + (dst.width() - w) * 0.5,
                dst.top + (dst.height() - h) * 0.5,
                w,
                h,
            );
            canvas.draw_image_rect_with_sampling_options(image, None, fitted, sampling, &paint);
        }
        Fit::Cover => {
            let dst = Rect::from_xywh(x, y, node.size[0], node.size[1]);
            let scale = (dst.width() / iw).max(dst.height() / ih);
            let sw = dst.width() / scale;
            let sh = dst.height() / scale;
            let src = Rect::from_xywh((iw - sw) * 0.5, (ih - sh) * 0.5, sw, sh);
            canvas.draw_image_rect_with_sampling_options(
                image,
                Some((&src, SrcRectConstraint::Strict)),
                dst,
                sampling,
                &paint,
            );
        }
    }
}

#[allow(clippy::too_many_arguments)]
fn draw_one_text(
    canvas: &Canvas,
    fonts: &FontRegistry,
    text: &str,
    abs: (f32, f32),
    role: FontRole,
    size: f32,
    align: HAlign,
    baseline: Baseline,
    color: Color,
) {
    if text.is_empty() {
        return;
    }
    let font = Font::from_typeface(fonts.resolve(role).clone(), size);
    let Some(blob) = TextBlob::new(text, &font) else {
        return;
    };
    let (advance, bounds) = font.measure_str(text, None);
    let x = match align {
        HAlign::Left => abs.0,
        HAlign::Center => abs.0 - advance * 0.5,
        HAlign::Right => abs.0 - advance,
    };
    let (_, metrics) = font.metrics();
    let baseline_y = match baseline {
        // Align the visual top of the ink to pos.y (Painter widget behaviour).
        Baseline::CjkTop => abs.1 - bounds.top,
        // Align the font ascender line to pos.y (raster-text default).
        Baseline::Ascender => abs.1 - metrics.ascent,
    };
    let mut paint = Paint::default();
    paint.set_anti_alias(true);
    paint.set_color(color);
    canvas.draw_text_blob(&blob, Point::new(x, baseline_y), &paint);
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
    fn rejects_wrong_version() {
        let json = scene_json("").replace("\"version\": 2", "\"version\": 1");
        let scene: Scene = serde_json::from_str(&json).expect("scene parses");
        assert!(render_scene_inner(&scene).is_err());
    }
}
