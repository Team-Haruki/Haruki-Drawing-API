use std::f32::consts::PI;
use std::fs;
use std::path::{Component, Path, PathBuf};
use std::time::Instant;

use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict};
use skia_safe::{
    BlurStyle, Canvas, ClipOp, Color, Data, EncodedImageFormat, FilterMode, FontMgr, Image,
    MaskFilter, MipmapMode, Paint, PaintStyle, Path as SkPath, Point, RRect, Rect, SamplingOptions,
    Surface, TileMode, Typeface, gradient, image_filters, surfaces,
};

/// Smooth (bilinear) sampling, matching Pillow's BILINEAR down/upscale in the blur
/// pipeline. `SamplingOptions::default()` is NEAREST, which makes downsampled blurs blocky.
fn linear_sampling() -> SamplingOptions {
    SamplingOptions::new(FilterMode::Linear, MipmapMode::None)
}

mod interp;
mod ir;

#[pyfunction]
fn render_scene(py: Python<'_>, ir_json: &[u8]) -> PyResult<Py<PyDict>> {
    let scene: ir::Scene = serde_json::from_slice(ir_json).map_err(|err| {
        pyo3::exceptions::PyValueError::new_err(format!("invalid scene IR: {err}"))
    })?;
    let rendered = py
        .detach(|| interp::render_scene_inner(&scene))
        .map_err(|err| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("scene render failed: {err}"))
        })?;

    let dict = PyDict::new(py);
    dict.set_item("image_bytes", PyBytes::new(py, &rendered.bytes))?;
    dict.set_item("media_type", rendered.media_type)?;
    dict.set_item("filename", rendered.filename)?;
    dict.set_item("image_width", rendered.width)?;
    dict.set_item("image_height", rendered.height)?;
    dict.set_item("image_mode", "RGBA")?;
    dict.set_item("encode_elapsed", rendered.encode_elapsed)?;
    Ok(dict.unbind())
}

#[pymodule(gil_used = false)]
fn haruki_skia_renderer(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(render_scene, m)?)?;
    Ok(())
}

struct RenderedImage {
    bytes: Vec<u8>,
    media_type: &'static str,
    filename: &'static str,
    width: i32,
    height: i32,
    encode_elapsed: f64,
}

#[derive(Clone, Copy)]
struct Rgba(u8, u8, u8, u8);

impl Rgba {
    fn color(self) -> Color {
        Color::from_argb(self.3, self.0, self.1, self.2)
    }
}

struct PinkPalette {
    grad1: [u8; 3],
    grad2: [u8; 3],
    overlay1: Rgba,
    overlay2: Rgba,
    white_alpha: u8,
}

#[derive(Clone, Copy)]
struct PinkPaletteStop {
    hour: f32,
    grad1: [u8; 3],
    grad2: [u8; 3],
    overlay1: Rgba,
    overlay2: Rgba,
    white_alpha: u8,
}

type NormPoint = (f32, f32);
type GradientPoints = (NormPoint, NormPoint, NormPoint, NormPoint);

struct SimpleRng {
    state: u64,
    spare_normal: Option<f32>,
}

impl SimpleRng {
    fn new(seed: u64) -> Self {
        Self {
            state: seed.max(1),
            spare_normal: None,
        }
    }

    fn next_u64(&mut self) -> u64 {
        let mut x = self.state;
        x ^= x >> 12;
        x ^= x << 25;
        x ^= x >> 27;
        self.state = x;
        x.wrapping_mul(0x2545_F491_4F6C_DD1D)
    }

    fn next_f32(&mut self) -> f32 {
        ((self.next_u64() >> 40) as f32) / ((1u64 << 24) as f32)
    }

    fn uniform(&mut self, min: f32, max: f32) -> f32 {
        min + (max - min) * self.next_f32()
    }

    fn normal(&mut self, mean: f32, stddev: f32) -> f32 {
        if let Some(value) = self.spare_normal.take() {
            return mean + value * stddev;
        }
        let u1 = self.next_f32().max(1.0e-6);
        let u2 = self.next_f32();
        let radius = (-2.0 * u1.ln()).sqrt();
        let theta = 2.0 * PI * u2;
        self.spare_normal = Some(radius * theta.sin());
        mean + radius * theta.cos() * stddev
    }
}

fn draw_sekai_triangle_background(canvas: &Canvas, width: f32, height: f32, hour: f32) {
    let mut paint = Paint::default();
    paint.set_anti_alias(true);
    let (primary_p1, primary_p2, overlay_p1, overlay_p2) = gradient_points(width, height);
    let palette = pink_palette(hour);

    draw_linear_gradient(
        canvas,
        width,
        height,
        primary_p1,
        primary_p2,
        Rgba(palette.grad1[0], palette.grad1[1], palette.grad1[2], 255),
        Rgba(palette.grad2[0], palette.grad2[1], palette.grad2[2], 255),
    );
    draw_linear_gradient(
        canvas,
        width,
        height,
        overlay_p1,
        overlay_p2,
        palette.overlay1,
        palette.overlay2,
    );
    paint.set_color(Color::from_argb(palette.white_alpha, 255, 255, 255));
    canvas.draw_rect(Rect::from_xywh(0.0, 0.0, width, height), &paint);

    let factor = width.min(height) / 2048.0 * 1.5;
    let size_factor = factor;
    let dense_factor = 1.0;
    let aspect = width / height.max(1.0);
    let aspect_density_boost = 1.55_f32.min(1.15_f32.max(aspect.powf(0.22)));
    let wide_shift = 0.12_f32.min(0.0_f32.max((aspect - 1.0) * 0.08));

    let grad1 = palette.grad1;
    let grad2 = palette.grad2;
    let mid = mix_rgb(grad1, grad2, 0.5);
    let preset_colors = [
        brighten_rgb(mix_rgb(grad1, [255, 206, 232], 0.72), 0.20),
        brighten_rgb(mix_rgb(mid, [238, 214, 255], 0.68), 0.18),
        brighten_rgb(mix_rgb(grad2, [208, 232, 255], 0.66), 0.20),
        brighten_rgb(mix_rgb(mid, [255, 228, 176], 0.56), 0.18),
    ];

    let seed = ((width as u64) << 32) ^ (height as u64) ^ ((hour * 1000.0) as u64).rotate_left(17);
    let mut rng = SimpleRng::new(seed);
    draw_random_triangles(
        canvas,
        &mut rng,
        width,
        height,
        (28.0 * dense_factor * aspect_density_boost) as usize,
        (128.0 * size_factor, 16.0 * size_factor),
        size_factor,
        wide_shift,
        &preset_colors,
    );
    draw_random_triangles(
        canvas,
        &mut rng,
        width,
        height,
        (280.0 * dense_factor * aspect_density_boost) as usize,
        (64.0 * size_factor, 16.0 * size_factor),
        size_factor,
        wide_shift,
        &preset_colors,
    );
}

fn lerp_u8(a: u8, b: u8, t: f32) -> u8 {
    (a as f32 * (1.0 - t) + b as f32 * t).round() as u8
}

fn lerp_rgb(a: [u8; 3], b: [u8; 3], t: f32) -> [u8; 3] {
    [
        lerp_u8(a[0], b[0], t),
        lerp_u8(a[1], b[1], t),
        lerp_u8(a[2], b[2], t),
    ]
}

fn lerp_rgba(a: Rgba, b: Rgba, t: f32) -> Rgba {
    Rgba(
        lerp_u8(a.0, b.0, t),
        lerp_u8(a.1, b.1, t),
        lerp_u8(a.2, b.2, t),
        lerp_u8(a.3, b.3, t),
    )
}

fn mix_rgb(a: [u8; 3], b: [u8; 3], ratio: f32) -> [u8; 3] {
    lerp_rgb(a, b, ratio)
}

fn brighten_rgb(color: [u8; 3], amount: f32) -> [u8; 3] {
    [
        (color[0] as f32 + (255.0 - color[0] as f32) * amount).min(255.0) as u8,
        (color[1] as f32 + (255.0 - color[1] as f32) * amount).min(255.0) as u8,
        (color[2] as f32 + (255.0 - color[2] as f32) * amount).min(255.0) as u8,
    ]
}

fn pink_palette(hour: f32) -> PinkPalette {
    const PALETTES: [PinkPaletteStop; 7] = [
        PinkPaletteStop {
            hour: 0.0,
            grad1: [128, 106, 170],
            grad2: [194, 145, 210],
            overlay1: Rgba(255, 194, 228, 60),
            overlay2: Rgba(198, 184, 255, 42),
            white_alpha: 72,
        },
        PinkPaletteStop {
            hour: 5.0,
            grad1: [248, 218, 234],
            grad2: [205, 214, 255],
            overlay1: Rgba(255, 240, 246, 84),
            overlay2: Rgba(255, 214, 236, 52),
            white_alpha: 76,
        },
        PinkPaletteStop {
            hour: 9.0,
            grad1: [236, 208, 228],
            grad2: [172, 205, 255],
            overlay1: Rgba(255, 244, 248, 76),
            overlay2: Rgba(226, 221, 255, 52),
            white_alpha: 72,
        },
        PinkPaletteStop {
            hour: 12.0,
            grad1: [230, 198, 224],
            grad2: [160, 198, 255],
            overlay1: Rgba(255, 246, 249, 72),
            overlay2: Rgba(214, 219, 255, 50),
            white_alpha: 70,
        },
        PinkPaletteStop {
            hour: 15.0,
            grad1: [242, 186, 216],
            grad2: [173, 186, 242],
            overlay1: Rgba(255, 220, 236, 88),
            overlay2: Rgba(255, 192, 224, 54),
            white_alpha: 64,
        },
        PinkPaletteStop {
            hour: 19.0,
            grad1: [176, 132, 190],
            grad2: [146, 156, 226],
            overlay1: Rgba(255, 194, 224, 62),
            overlay2: Rgba(213, 188, 255, 40),
            white_alpha: 58,
        },
        PinkPaletteStop {
            hour: 24.0,
            grad1: [128, 106, 170],
            grad2: [194, 145, 210],
            overlay1: Rgba(255, 194, 228, 60),
            overlay2: Rgba(198, 184, 255, 42),
            white_alpha: 72,
        },
    ];

    let hour = hour.clamp(0.0, 23.999);
    for pair in PALETTES.windows(2) {
        let current = pair[0];
        let next = pair[1];
        if hour >= current.hour && hour <= next.hour {
            let t = if next.hour == current.hour {
                0.0
            } else {
                (hour - current.hour) / (next.hour - current.hour)
            };
            return PinkPalette {
                grad1: lerp_rgb(current.grad1, next.grad1, t),
                grad2: lerp_rgb(current.grad2, next.grad2, t),
                overlay1: lerp_rgba(current.overlay1, next.overlay1, t),
                overlay2: lerp_rgba(current.overlay2, next.overlay2, t),
                white_alpha: lerp_u8(current.white_alpha, next.white_alpha, t),
            };
        }
    }

    let fallback = PALETTES[PALETTES.len() - 1];
    PinkPalette {
        grad1: fallback.grad1,
        grad2: fallback.grad2,
        overlay1: fallback.overlay1,
        overlay2: fallback.overlay2,
        white_alpha: fallback.white_alpha,
    }
}

fn gradient_points(width: f32, height: f32) -> GradientPoints {
    let aspect = width / height.max(1.0);
    let wide_bias = 0.2_f32.min(0.0_f32.max((aspect - 1.0) * 0.12));
    let tall_bias = 0.16_f32.min(0.0_f32.max((1.0 - aspect) * 0.16));
    (
        (0.02 + tall_bias * 0.3, 0.96 - wide_bias),
        (0.98 - tall_bias * 0.3, 0.08 + wide_bias * 0.8),
        (0.04 + tall_bias * 0.2, 0.06 + wide_bias * 0.3),
        (0.96 - tall_bias * 0.2, 0.94 - wide_bias * 0.5),
    )
}

fn draw_linear_gradient(
    canvas: &Canvas,
    width: f32,
    height: f32,
    p1: (f32, f32),
    p2: (f32, f32),
    c1: Rgba,
    c2: Rgba,
) {
    let colors = [c1.color().into(), c2.color().into()];
    let gradient_colors = gradient::Colors::new_evenly_spaced(&colors, TileMode::Clamp, None);
    let gradient = gradient::Gradient::new(gradient_colors, gradient::Interpolation::default());
    if let Some(shader) = gradient::shaders::linear_gradient(
        (
            Point::new(p1.0 * width, p1.1 * height),
            Point::new(p2.0 * width, p2.1 * height),
        ),
        &gradient,
        None,
    ) {
        let mut paint = Paint::default();
        paint.set_shader(shader);
        canvas.draw_rect(Rect::from_xywh(0.0, 0.0, width, height), &paint);
    }
}

#[allow(clippy::too_many_arguments)]
fn draw_random_triangles(
    canvas: &Canvas,
    rng: &mut SimpleRng,
    width: f32,
    height: f32,
    count: usize,
    size_dist: (f32, f32),
    size_factor: f32,
    wide_shift: f32,
    preset_colors: &[[u8; 3]],
) {
    let std_size_lower = 64.0 * size_factor;
    let std_size_upper = 128.0 * size_factor;

    for _ in 0..count {
        let (x, y) = if rng.next_f32() < 0.78 {
            let edge = weighted_edge(rng, wide_shift);
            match edge {
                0 => (
                    rng.uniform(-0.04 * width, 0.18 * width),
                    rng.uniform(0.0, height),
                ),
                1 => (
                    rng.uniform((0.82 - wide_shift) * width, 1.03 * width),
                    rng.uniform(0.0, height),
                ),
                2 => (
                    rng.uniform(0.0, width),
                    rng.uniform(-0.04 * height, (0.20 + wide_shift * 0.5) * height),
                ),
                _ => (
                    rng.uniform(0.0, width),
                    rng.uniform((0.80 - wide_shift * 0.8) * height, 1.03 * height),
                ),
            }
        } else {
            (
                rng.uniform(0.12 * width, 0.88 * width),
                rng.uniform(0.12 * height, 0.88 * height),
            )
        };

        if x < 0.0 || x >= width || y < 0.0 || y >= height {
            continue;
        }

        let rot = rng.uniform(0.0, 360.0);
        let mut size = rng
            .normal(size_dist.0, size_dist.1)
            .round()
            .clamp(1.0, 1000.0);
        let dist =
            ((x - width * 0.5) / width * 2.0).powi(2) + ((y - height * 0.5) / height * 2.0).powi(2);
        size *= 0.28_f32.max(dist);

        let mut size_alpha_factor = 1.0;
        if size < std_size_lower {
            size_alpha_factor = size / std_size_lower.max(1.0);
        }
        if size > std_size_upper {
            size_alpha_factor = 1.0 - (size - std_size_upper * 1.5) / (std_size_upper * 1.5);
        }
        let mut alpha = rng.normal(122.0, 138.0) * 0.0_f32.max(1.5_f32.min(size_alpha_factor));
        if rng.next_f32() < 0.05 && size > std_size_lower {
            alpha = 255.0;
        }
        if alpha <= 34.0 {
            continue;
        }
        let color = preset_colors[(rng.next_u64() as usize) % preset_colors.len()];
        let tri_type = [0, 1, 1, 1, 2, 2][(rng.next_u64() as usize) % 6];
        draw_triangle(
            canvas,
            x,
            y,
            rot,
            size,
            Rgba(color[0], color[1], color[2], alpha.clamp(0.0, 255.0) as u8),
            tri_type,
        );
    }
}

fn weighted_edge(rng: &mut SimpleRng, wide_shift: f32) -> usize {
    let weights = [
        0.9,
        0.95 - wide_shift * 1.8,
        1.18 + wide_shift * 1.6,
        0.72 - wide_shift * 1.2,
    ];
    let total = weights.iter().sum::<f32>();
    let mut pick = rng.uniform(0.0, total);
    for (idx, weight) in weights.iter().enumerate() {
        if pick <= *weight {
            return idx;
        }
        pick -= *weight;
    }
    weights.len() - 1
}

fn draw_triangle(
    canvas: &Canvas,
    x: f32,
    y: f32,
    rot: f32,
    size: f32,
    color: Rgba,
    tri_type: usize,
) {
    let radius = size * 0.56;
    let type_angle_offset = [0.0, 18.0, -18.0][tri_type % 3];
    let mut points = [Point::new(0.0, 0.0); 3];
    for (idx, point) in points.iter_mut().enumerate() {
        let angle = (rot + type_angle_offset + idx as f32 * 120.0 - 90.0).to_radians();
        *point = Point::new(x + radius * angle.cos(), y + radius * angle.sin());
    }
    let path = SkPath::polygon(&points, true, None, None);
    let mut paint = Paint::default();
    paint.set_anti_alias(true);
    paint.set_color(color.color());
    canvas.draw_path(&path, &paint);
}

fn draw_blur_glass_rect(
    canvas: &Canvas,
    backdrop: &Image,
    rect: Rect,
    radius: f32,
    fill: Color,
    shadow_alpha: f32,
) {
    draw_glass_shadow(canvas, rect, radius, shadow_alpha);

    let full_rect = Rect::from_xywh(0.0, 0.0, backdrop.width() as f32, backdrop.height() as f32);
    let mut sample_rect = rect.with_outset((10.0, 10.0));
    if sample_rect.intersect(full_rect) {
        let downsample = 2.0;
        let temp_w = (sample_rect.width() / downsample).ceil().max(1.0) as i32;
        let temp_h = (sample_rect.height() / downsample).ceil().max(1.0) as i32;
        if let Some(mut temp_surface) = surfaces::raster_n32_premul((temp_w, temp_h)) {
            let temp_dst = Rect::from_xywh(0.0, 0.0, temp_w as f32, temp_h as f32);
            let mut copy_paint = Paint::default();
            copy_paint.set_anti_alias(true);
            temp_surface.canvas().draw_image_rect_with_sampling_options(
                backdrop,
                Some((&sample_rect, skia_safe::canvas::SrcRectConstraint::Strict)),
                temp_dst,
                linear_sampling(),
                &copy_paint,
            );

            let blurred =
                if let Some(mut blur_surface) = surfaces::raster_n32_premul((temp_w, temp_h)) {
                    let temp_image = temp_surface.image_snapshot();
                    let mut blur_paint = Paint::default();
                    blur_paint.set_anti_alias(true);
                    blur_paint.set_image_filter(image_filters::blur(
                        (4.0 / downsample, 4.0 / downsample),
                        TileMode::Clamp,
                        None,
                        None,
                    ));
                    blur_surface.canvas().draw_image_rect_with_sampling_options(
                        &temp_image,
                        None,
                        temp_dst,
                        linear_sampling(),
                        &blur_paint,
                    );
                    blur_surface.image_snapshot()
                } else {
                    temp_surface.image_snapshot()
                };

            canvas.save();
            canvas.clip_rrect(
                RRect::new_rect_xy(rect, radius, radius),
                ClipOp::Intersect,
                true,
            );
            let mut paste_paint = Paint::default();
            paste_paint.set_anti_alias(true);
            canvas.draw_image_rect_with_sampling_options(
                &blurred,
                None,
                sample_rect,
                linear_sampling(),
                &paste_paint,
            );
            canvas.restore();
        }
    }

    draw_glass_overlay(canvas, rect, radius, fill, 0.6);
}

fn draw_glass_shadow(canvas: &Canvas, rect: Rect, radius: f32, shadow_alpha: f32) {
    // Symmetric soft contact shadow on all four sides (mirrors Pillow: the panel shape
    // dimmed to shadow_alpha, GaussianBlur'd, interior removed). Clipping to the area
    // OUTSIDE the panel keeps only the outward halo, so the translucent glass fill drawn
    // afterwards never shows interior darkening. Black, not the old downward-only purple.
    let rrect = RRect::new_rect_xy(rect, radius, radius);
    canvas.save();
    canvas.clip_rrect(rrect, ClipOp::Difference, true);
    let mut paint = Paint::default();
    paint.set_anti_alias(true);
    paint.set_style(PaintStyle::Fill);
    for (sigma, factor) in [(2.0, 0.55), (5.0, 0.28), (9.0, 0.12)] {
        let alpha = (shadow_alpha * factor * 255.0).clamp(0.0, 255.0) as u8;
        paint.set_color(Color::from_argb(alpha, 0, 0, 0));
        paint.set_mask_filter(MaskFilter::blur(BlurStyle::Normal, sigma, true));
        canvas.draw_rrect(rrect, &paint);
    }
    paint.set_mask_filter(None);
    canvas.restore();
}

fn draw_glass_overlay(canvas: &Canvas, rect: Rect, radius: f32, fill: Color, edge_strength: f32) {
    let mut paint = Paint::default();
    paint.set_anti_alias(true);
    paint.set_color(fill);
    paint.set_style(PaintStyle::Fill);
    canvas.draw_rrect(RRect::new_rect_xy(rect, radius, radius), &paint);

    if edge_strength <= 0.0 {
        return;
    }

    // Directional glass bevel (mirrors Pillow's _impl_blurglass_roundrect edge pass):
    // a bright highlight on the top-left rim fading through transparent to a fainter
    // highlight on the bottom-right rim. This is what gives the panel its depth; the
    // previous uniform strokes read flat.
    let edge_w = (radius * 0.5)
        .min(4.0)
        .min(rect.width().min(rect.height()) / 16.0)
        .max(1.0);
    let a1 = (255.0 * edge_strength).clamp(0.0, 255.0) as u8;
    let a2 = (255.0 * edge_strength * 0.75).clamp(0.0, 255.0) as u8;
    let colors = [
        Color::from_argb(a1, 255, 255, 255).into(),
        Color::from_argb(0, 255, 255, 255).into(),
        Color::from_argb(a2, 255, 255, 255).into(),
    ];
    let gradient_colors = gradient::Colors::new_evenly_spaced(&colors, TileMode::Clamp, None);
    let grad = gradient::Gradient::new(gradient_colors, gradient::Interpolation::default());
    if let Some(shader) = gradient::shaders::linear_gradient(
        (
            Point::new(rect.left, rect.top),
            Point::new(rect.right, rect.bottom),
        ),
        &grad,
        None,
    ) {
        // A wide stroke centered on the rim, clipped to the panel and softened, so the
        // highlight fades *inward* into the interior (a broad pearly sheen) rather than a
        // thin hard line. Pillow's super-sampled outline reads this way.
        let rrect = RRect::new_rect_xy(rect, radius, radius);
        canvas.save();
        canvas.clip_rrect(rrect, ClipOp::Intersect, true);
        let mut edge = Paint::default();
        edge.set_anti_alias(true);
        edge.set_style(PaintStyle::Stroke);
        edge.set_stroke_width(edge_w * 1.8);
        edge.set_shader(shader);
        edge.set_mask_filter(MaskFilter::blur(
            BlurStyle::Normal,
            (edge_w * 0.45).max(0.6),
            true,
        ));
        canvas.draw_rrect(rrect, &edge);
        edge.set_mask_filter(None);
        canvas.restore();
    }

    // Faint inner contact line just inside the bevel for a subtly recessed feel
    // (approximates Pillow's soft inner shadow).
    let mut inner = Paint::default();
    inner.set_anti_alias(true);
    inner.set_style(PaintStyle::Stroke);
    inner.set_stroke_width(1.0);
    inner.set_color(Color::from_argb(28, 92, 78, 116));
    canvas.draw_rrect(
        RRect::new_rect_xy(
            rect.with_inset((edge_w, edge_w)),
            (radius - edge_w).max(0.0),
            (radius - edge_w).max(0.0),
        ),
        &inner,
    );
}

fn draw_cover_image(canvas: &Canvas, image: &Image, dst: Rect, alpha: f32) {
    let iw = image.width() as f32;
    let ih = image.height() as f32;
    if iw <= 0.0 || ih <= 0.0 {
        return;
    }
    let scale = (dst.width() / iw).max(dst.height() / ih);
    let sw = dst.width() / scale;
    let sh = dst.height() / scale;
    let src = Rect::from_xywh((iw - sw) * 0.5, (ih - sh) * 0.5, sw, sh);
    let mut paint = Paint::default();
    paint.set_anti_alias(true);
    paint.set_alpha_f(alpha);
    canvas.draw_image_rect_with_sampling_options(
        image,
        Some((&src, skia_safe::canvas::SrcRectConstraint::Strict)),
        dst,
        SamplingOptions::default(),
        &paint,
    );
}

fn encode_surface(
    mut surface: Surface,
    export_format: &str,
    jpg_quality: i32,
) -> Result<RenderedImage, String> {
    let started = Instant::now();
    let image = surface.image_snapshot();
    let format = if export_format == "jpg" {
        EncodedImageFormat::JPEG
    } else {
        EncodedImageFormat::PNG
    };
    let quality = if export_format == "jpg" {
        jpg_quality.clamp(1, 100) as u32
    } else {
        100
    };
    let data = image
        .encode(None, format, Some(quality))
        .ok_or_else(|| "failed to encode image".to_string())?;
    let bytes = data.as_bytes().to_vec();
    let width = image.width();
    let height = image.height();
    let (media_type, filename) = if export_format == "jpg" {
        ("image/jpeg", "image.jpg")
    } else {
        ("image/png", "image.png")
    };
    Ok(RenderedImage {
        bytes,
        media_type,
        filename,
        width,
        height,
        encode_elapsed: started.elapsed().as_secs_f64(),
    })
}

fn load_image(base: &Path, path: &str) -> Result<Image, String> {
    let full_path = resolve_asset_path(base, path)?;
    let bytes = fs::read(&full_path)
        .map_err(|err| format!("failed to read {}: {err}", full_path.display()))?;
    let data = Data::new_copy(&bytes);
    Image::from_encoded(data).ok_or_else(|| format!("failed to decode {}", full_path.display()))
}

fn resolve_asset_path(base: &Path, path: &str) -> Result<PathBuf, String> {
    if path.is_empty() {
        return Err("asset path must not be empty".to_string());
    }
    if path.contains('\\') {
        return Err(format!("asset path must use forward slash: {path}"));
    }
    let relative = Path::new(path);
    if relative.is_absolute() {
        return Err(format!("asset path must be relative: {path}"));
    }
    for component in relative.components() {
        match component {
            Component::Normal(_) => {}
            _ => return Err(format!("asset path contains unsupported component: {path}")),
        }
    }
    Ok(base.join(relative))
}

fn load_typeface(dir: &str, name: &str) -> Typeface {
    let mgr = FontMgr::default();
    for candidate in font_candidates(dir, name) {
        if candidate.exists()
            && let Ok(bytes) = fs::read(candidate)
            && let Some(typeface) = mgr.new_from_data(&bytes, None)
        {
            return typeface;
        }
    }
    mgr.match_family_style("sans-serif", skia_safe::FontStyle::normal())
        .or_else(|| mgr.legacy_make_typeface(None, skia_safe::FontStyle::normal()))
        .expect("Skia fallback typeface unavailable")
}

fn font_candidates(dir: &str, name: &str) -> Vec<PathBuf> {
    let base = PathBuf::from(dir);
    vec![
        PathBuf::from(name),
        base.join(name),
        base.join(format!("{name}.otf")),
        base.join(format!("{name}.ttf")),
        base.join(format!("{name}.ttc")),
        base.join("fonts").join(format!("{name}.otf")),
        base.join("fonts").join(format!("{name}.ttf")),
        base.join("fonts").join(format!("{name}.ttc")),
    ]
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn rejects_absolute_asset_paths() {
        assert!(resolve_asset_path(Path::new("/tmp/base"), "/tmp/x.png").is_err());
    }

    #[test]
    fn rejects_parent_asset_paths() {
        assert!(resolve_asset_path(Path::new("/tmp/base"), "../x.png").is_err());
    }
}
