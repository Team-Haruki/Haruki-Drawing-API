use std::cell::Cell;
use std::collections::HashMap;
use std::f32::consts::PI;
use std::fs;
use std::path::{Component, Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::Instant;

use moka::sync::Cache;
use mtpng::encoder::{Encoder as MtpngEncoder, Options as MtpngOptions};
use mtpng::{ColorType as MtpngColorType, CompressionLevel, Header as MtpngHeader};
use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict};
use skia_safe::{
    AlphaType, BlurStyle, Canvas, ClipOp, Color, ColorType, Data, EncodedImageFormat, FilterMode,
    FontMgr, Image, ImageInfo, MaskFilter, MipmapMode, Paint, PaintStyle, Path as SkPath, Point,
    RRect, Rect, SamplingOptions, Surface, TileMode, Typeface, canvas::SrcRectConstraint, gradient,
    image_filters, png_encoder, surfaces,
};

/// Smooth (bilinear) sampling, matching Pillow's BILINEAR down/upscale in the blur
/// pipeline. `SamplingOptions::default()` is NEAREST, which makes downsampled blurs blocky.
fn linear_sampling() -> SamplingOptions {
    SamplingOptions::new(FilterMode::Linear, MipmapMode::None)
}

mod interp;
mod ir;

#[pyfunction]
#[pyo3(signature = (ir_json, images = None))]
fn render_scene(
    py: Python<'_>,
    ir_json: &[u8],
    images: Option<&Bound<'_, PyDict>>,
) -> PyResult<Py<PyDict>> {
    let scene: ir::Scene = serde_json::from_slice(ir_json).map_err(|err| {
        pyo3::exceptions::PyValueError::new_err(format!("invalid scene IR: {err}"))
    })?;
    // Runtime in-memory images, referenced from the IR as "mem:<key>". A value is either
    // encoded bytes (PNG/JPEG) or a (width, height, rgba_bytes) tuple of raw pixels (no
    // encode/decode). Extracted here under the GIL, materialized lazily during rendering.
    let mut mem_images: HashMap<String, interp::MemImage> = HashMap::new();
    if let Some(dict) = images {
        for (key, value) in dict.iter() {
            let key: String = key.extract()?;
            let mem = if let Ok((width, height, bytes)) = value.extract::<(i32, i32, &[u8])>() {
                interp::MemImage::Raw {
                    width,
                    height,
                    data: Data::new_copy(bytes),
                }
            } else {
                interp::MemImage::Encoded(Data::new_copy(value.extract::<&[u8]>()?))
            };
            mem_images.insert(key, mem);
        }
    }
    let rendered = py
        .detach(|| interp::render_scene_inner(&scene, mem_images))
        .map_err(|err| {
            pyo3::exceptions::PyRuntimeError::new_err(format!("scene render failed: {err}"))
        })?;

    let dict = PyDict::new(py);
    dict.set_item("image_bytes", PyBytes::new(py, rendered.bytes.as_bytes()))?;
    dict.set_item("media_type", rendered.media_type)?;
    dict.set_item("filename", rendered.filename)?;
    dict.set_item("image_width", rendered.width)?;
    dict.set_item("image_height", rendered.height)?;
    dict.set_item("image_mode", "RGBA")?;
    dict.set_item("encode_elapsed", rendered.encode_elapsed)?;
    let metrics = PyDict::new(py);
    metrics.set_item("total_elapsed", rendered.metrics.total_elapsed)?;
    metrics.set_item("setup_elapsed", rendered.metrics.setup_elapsed)?;
    metrics.set_item("draw_elapsed", rendered.metrics.draw_elapsed)?;
    metrics.set_item("scale_elapsed", rendered.metrics.scale_elapsed)?;
    metrics.set_item("asset_load_elapsed", rendered.metrics.asset_load_elapsed)?;
    metrics.set_item(
        "raster_prewarm_elapsed",
        rendered.metrics.raster_prewarm_elapsed,
    )?;
    metrics.set_item(
        "raster_prewarm_requests",
        rendered.metrics.raster_prewarm_requests,
    )?;
    metrics.set_item("raster_prewarm_hits", rendered.metrics.raster_prewarm_hits)?;
    metrics.set_item(
        "raster_prewarm_misses",
        rendered.metrics.raster_prewarm_misses,
    )?;
    metrics.set_item(
        "raster_prewarm_coalesced",
        rendered.metrics.raster_prewarm_coalesced,
    )?;
    metrics.set_item(
        "raster_cache_build_elapsed",
        rendered.metrics.raster_cache_build_elapsed,
    )?;
    metrics.set_item(
        "raster_cache_wait_elapsed",
        rendered.metrics.raster_cache_wait_elapsed,
    )?;
    metrics.set_item("raster_cache_hits", rendered.metrics.raster_cache_hits)?;
    metrics.set_item("raster_cache_misses", rendered.metrics.raster_cache_misses)?;
    metrics.set_item(
        "raster_cache_coalesced",
        rendered.metrics.raster_cache_coalesced,
    )?;
    metrics.set_item(
        "raster_cache_bypasses",
        rendered.metrics.raster_cache_bypasses,
    )?;
    metrics.set_item(
        "raster_cache_entries",
        rendered.metrics.raster_cache_entries,
    )?;
    metrics.set_item("raster_cache_bytes", rendered.metrics.raster_cache_bytes)?;
    metrics.set_item(
        "zero_blur_fast_paths",
        rendered.metrics.zero_blur_fast_paths,
    )?;
    dict.set_item("native_metrics", metrics)?;
    Ok(dict.unbind())
}

/// Capability level of the IR this build understands. Bump when nodes/fields are added so
/// the Python side can refuse (fail-open to Pillow) instead of silently dropping features
/// when an older wheel meets newer IR. 4 = capability 3 + per-image sampling intent.
pub const IR_CAPABILITY: u32 = 4;

#[pymodule(gil_used = false)]
fn haruki_skia_renderer(m: &Bound<'_, PyModule>) -> PyResult<()> {
    m.add_function(wrap_pyfunction!(render_scene, m)?)?;
    m.add_function(wrap_pyfunction!(renderer_cache_stats, m)?)?;
    m.add_function(wrap_pyfunction!(clear_renderer_caches, m)?)?;
    m.add("IR_CAPABILITY", IR_CAPABILITY)?;
    Ok(())
}

struct RenderedImage {
    bytes: EncodedBytes,
    media_type: &'static str,
    filename: &'static str,
    width: i32,
    height: i32,
    encode_elapsed: f64,
    metrics: NativeMetrics,
}

enum EncodedBytes {
    Skia(Data),
    Owned(Vec<u8>),
}

impl EncodedBytes {
    fn as_bytes(&self) -> &[u8] {
        match self {
            Self::Skia(data) => data.as_bytes(),
            Self::Owned(bytes) => bytes,
        }
    }
}

#[derive(Default)]
pub(crate) struct NativeMetrics {
    pub(crate) total_elapsed: f64,
    pub(crate) setup_elapsed: f64,
    pub(crate) draw_elapsed: f64,
    pub(crate) scale_elapsed: f64,
    pub(crate) asset_load_elapsed: f64,
    pub(crate) raster_prewarm_elapsed: f64,
    pub(crate) raster_prewarm_requests: u64,
    pub(crate) raster_prewarm_hits: u64,
    pub(crate) raster_prewarm_misses: u64,
    pub(crate) raster_prewarm_coalesced: u64,
    pub(crate) raster_cache_build_elapsed: f64,
    pub(crate) raster_cache_wait_elapsed: f64,
    pub(crate) raster_cache_hits: u64,
    pub(crate) raster_cache_misses: u64,
    pub(crate) raster_cache_coalesced: u64,
    pub(crate) raster_cache_bypasses: u64,
    pub(crate) raster_cache_entries: u64,
    pub(crate) raster_cache_bytes: u64,
    pub(crate) zero_blur_fast_paths: u64,
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

/// (grad1, grad2, overlay1, overlay2, white_veil_alpha, triangle_preset_colors).
type TrianglePalette = ([u8; 3], [u8; 3], Rgba, Rgba, u8, Vec<[u8; 3]>);

#[allow(clippy::too_many_arguments)]
fn draw_sekai_triangle_background(
    canvas: &Canvas,
    width: f32,
    height: f32,
    hour: f32,
    time_color: bool,
    main_hue: f32,
    size_fixed_rate: f32,
) {
    let mut paint = Paint::default();
    paint.set_anti_alias(true);
    let (primary_p1, primary_p2, overlay_p1, overlay_p2) = gradient_points(width, height);

    // Resolve the base/overlay gradient colors, white veil, and triangle preset colors for
    // either the time-of-day pink palette or a custom-hue palette (mirrors Painter).
    let (grad1, grad2, overlay1, overlay2, white_alpha, preset_colors): TrianglePalette =
        if time_color {
            let palette = pink_palette(hour);
            let mid = mix_rgb(palette.grad1, palette.grad2, 0.5);
            let preset = vec![
                brighten_rgb(mix_rgb(palette.grad1, [255, 206, 232], 0.72), 0.20),
                brighten_rgb(mix_rgb(mid, [238, 214, 255], 0.68), 0.18),
                brighten_rgb(mix_rgb(palette.grad2, [208, 232, 255], 0.66), 0.20),
                brighten_rgb(mix_rgb(mid, [255, 228, 176], 0.56), 0.18),
            ];
            (
                palette.grad1,
                palette.grad2,
                palette.overlay1,
                palette.overlay2,
                palette.white_alpha,
                preset,
            )
        } else {
            let ofs = 0.025;
            let g1 = hls_to_rgb_trunc(main_hue, 1.0, 0.5);
            let g2 = hls_to_rgb_trunc(main_hue + ofs, 0.5, 0.9);
            let ov1 = hls_to_rgb_trunc(main_hue, 0.7, 0.9);
            let ov2 = hls_to_rgb_trunc(main_hue - ofs, 0.5, 0.5);
            let preset = vec![
                brighten_rgb([255, 189, 246], 0.22),
                brighten_rgb([183, 246, 255], 0.22),
                brighten_rgb([255, 247, 146], 0.22),
            ];
            (
                g1,
                g2,
                Rgba(ov1[0], ov1[1], ov1[2], 100),
                Rgba(ov2[0], ov2[1], ov2[2], 100),
                100,
                preset,
            )
        };

    draw_linear_gradient(
        canvas,
        width,
        height,
        primary_p1,
        primary_p2,
        Rgba(grad1[0], grad1[1], grad1[2], 255),
        Rgba(grad2[0], grad2[1], grad2[2], 255),
    );
    draw_linear_gradient(
        canvas, width, height, overlay_p1, overlay_p2, overlay1, overlay2,
    );
    paint.set_color(Color::from_argb(white_alpha, 255, 255, 255));
    canvas.draw_rect(Rect::from_xywh(0.0, 0.0, width, height), &paint);

    let factor = width.min(height) / 2048.0 * 1.5;
    // size_fixed_rate=0 keeps the exact legacy expressions (size scales, density fixed).
    let (size_factor, dense_factor) = if size_fixed_rate == 0.0 {
        (factor, 1.0)
    } else {
        (
            1.0 + (factor - 1.0) * (1.0 - size_fixed_rate),
            1.0 + (factor * factor - 1.0) * size_fixed_rate,
        )
    };
    let aspect = width / height.max(1.0);
    let aspect_density_boost = 1.55_f32.min(1.15_f32.max(aspect.powf(0.22)));
    let wide_shift = 0.12_f32.min(0.0_f32.max((aspect - 1.0) * 0.08));

    let seed_extra = if time_color {
        (hour * 1000.0) as u64
    } else {
        ((main_hue * 100000.0) as u64).wrapping_add(7919)
    };
    let seed = ((width as u64) << 32) ^ (height as u64) ^ seed_extra.rotate_left(17);
    let ml = if time_color {
        time_lightness(hour)
    } else {
        1.0
    };
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
        ml,
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
        ml,
    );
}

/// Painter's time-of-day lightness (timecolors "l" column, painter.py:1447-1455): triangles
/// are damped by ml**0.5 at night so they fade with the palette. Piecewise-linear over the
/// fractional hour; the custom-hue path uses ml = 1.0.
fn time_lightness(hour: f32) -> f32 {
    const STOPS: [(f32, f32); 7] = [
        (0.0, 0.1),
        (5.0, 0.2),
        (9.0, 0.8),
        (12.0, 1.0),
        (15.0, 0.8),
        (19.0, 0.2),
        (24.0, 0.1),
    ];
    let h = hour.clamp(0.0, 24.0);
    for w in STOPS.windows(2) {
        let ((h1, l1), (h2, l2)) = (w[0], w[1]);
        if h >= h1 && h < h2 {
            return l1 + (l2 - l1) * ((h - h1) / (h2 - h1));
        }
    }
    STOPS[STOPS.len() - 1].1
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

/// `colorsys.hls_to_rgb` with Painter's `int(255*c)` truncation and `(h+1)%1` hue wrap.
fn hls_to_rgb_trunc(h: f32, l: f32, s: f32) -> [u8; 3] {
    let h = (h + 1.0).rem_euclid(1.0);
    if s == 0.0 {
        let v = (l * 255.0) as u8;
        return [v, v, v];
    }
    let m2 = if l <= 0.5 {
        l * (1.0 + s)
    } else {
        l + s - l * s
    };
    let m1 = 2.0 * l - m2;
    let v = |hue: f32| -> u8 {
        let hue = hue.rem_euclid(1.0);
        let c = if hue < 1.0 / 6.0 {
            m1 + (m2 - m1) * hue * 6.0
        } else if hue < 0.5 {
            m2
        } else if hue < 2.0 / 3.0 {
            m1 + (m2 - m1) * (2.0 / 3.0 - hue) * 6.0
        } else {
            m1
        };
        (c * 255.0) as u8
    };
    [v(h + 1.0 / 3.0), v(h), v(h - 1.0 / 3.0)]
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
    ml: f32,
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
        let mut alpha =
            rng.normal(122.0, 138.0) * 0.0_f32.max(1.5_f32.min(size_alpha_factor) * ml.sqrt());
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

/// Per-corner radii (UL, UR, LR, LL) with disabled corners at 0 (Painter's `corners`).
fn glass_corner_radii(radius: f32, corners: [bool; 4]) -> [Point; 4] {
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

#[allow(clippy::too_many_arguments)]
fn draw_blur_glass_rect(
    canvas: &Canvas,
    backdrop: Option<(&Image, (f32, f32))>,
    rect: Rect,
    radius: f32,
    panel_paint: &Paint,
    shadow_alpha: f32,
    blur: f32,
    corners: [bool; 4],
    shadow_width: f32,
) {
    draw_glass_shadow(canvas, rect, radius, shadow_alpha, corners, shadow_width);

    // `backdrop` is a snapshot of just the panel's region; `origin` is its top-left in canvas
    // space, so absolute sample coordinates map to the sub-image by subtracting it.
    if let Some((backdrop, origin)) = backdrop {
        let full_rect = Rect::from_xywh(
            origin.0,
            origin.1,
            backdrop.width() as f32,
            backdrop.height() as f32,
        );
        let mut sample_rect = rect.with_outset((10.0, 10.0));
        if sample_rect.intersect(full_rect) {
            let src_local = Rect::from_xywh(
                sample_rect.left - origin.0,
                sample_rect.top - origin.1,
                sample_rect.width(),
                sample_rect.height(),
            );
            // Mirror Painter's blur math (painter.py:1355-1363): downsample by
            // max(1, floor(blur/2)) then blur with sigma = blur / downsample.
            let downsample = (blur / 2.0).floor().max(1.0);
            let temp_w = (sample_rect.width() / downsample).ceil().max(1.0) as i32;
            let temp_h = (sample_rect.height() / downsample).ceil().max(1.0) as i32;
            if let Some(mut temp_surface) = surfaces::raster_n32_premul((temp_w, temp_h)) {
                let temp_dst = Rect::from_xywh(0.0, 0.0, temp_w as f32, temp_h as f32);
                let mut copy_paint = Paint::default();
                copy_paint.set_anti_alias(true);
                temp_surface.canvas().draw_image_rect_with_sampling_options(
                    backdrop,
                    Some((&src_local, skia_safe::canvas::SrcRectConstraint::Strict)),
                    temp_dst,
                    linear_sampling(),
                    &copy_paint,
                );

                let blurred =
                    if let Some(mut blur_surface) = surfaces::raster_n32_premul((temp_w, temp_h)) {
                        let temp_image = temp_surface.image_snapshot();
                        let mut blur_paint = Paint::default();
                        blur_paint.set_anti_alias(true);
                        let sigma = (blur / downsample).max(0.01);
                        blur_paint.set_image_filter(image_filters::blur(
                            (sigma, sigma),
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
                    RRect::new_rect_radii(rect, &glass_corner_radii(radius, corners)),
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
    }

    draw_glass_overlay(canvas, rect, radius, panel_paint, corners, 0.6);
}

fn draw_glass_shadow(
    canvas: &Canvas,
    rect: Rect,
    radius: f32,
    shadow_alpha: f32,
    corners: [bool; 4],
    shadow_width: f32,
) {
    // Symmetric soft contact shadow on all four sides (mirrors Pillow: the panel shape
    // dimmed to shadow_alpha, GaussianBlur'd, interior removed). Clipping to the area
    // OUTSIDE the panel keeps only the outward halo, so the translucent glass fill drawn
    // afterwards never shows interior darkening. Black, not the old downward-only purple.
    let rrect = RRect::new_rect_radii(rect, &glass_corner_radii(radius, corners));
    canvas.save();
    canvas.clip_rrect(rrect, ClipOp::Difference, true);
    let mut paint = Paint::default();
    paint.set_anti_alias(true);
    paint.set_style(PaintStyle::Fill);
    // Painter blurs the shadow mask with GaussianBlur(shadow_width * 0.5); scale the tuned
    // three-ring sigmas by shadow_width / 6 (the Painter default) to track the same spread.
    let spread = (shadow_width / 6.0).max(0.05);
    for (sigma, factor) in [
        (2.0 * spread, 0.55),
        (5.0 * spread, 0.28),
        (9.0 * spread, 0.12),
    ] {
        let alpha = (shadow_alpha * factor * 255.0).clamp(0.0, 255.0) as u8;
        paint.set_color(Color::from_argb(alpha, 0, 0, 0));
        paint.set_mask_filter(MaskFilter::blur(BlurStyle::Normal, sigma, true));
        canvas.draw_rrect(rrect, &paint);
    }
    paint.set_mask_filter(None);
    canvas.restore();
}

fn draw_glass_overlay(
    canvas: &Canvas,
    rect: Rect,
    radius: f32,
    panel_paint: &Paint,
    corners: [bool; 4],
    edge_strength: f32,
) {
    // `panel_paint` is pre-built (anti-alias + Fill style + solid color or gradient shader).
    canvas.draw_rrect(
        RRect::new_rect_radii(rect, &glass_corner_radii(radius, corners)),
        panel_paint,
    );

    if edge_strength <= 0.0 {
        return;
    }

    // Glass edge gloss: the light comes from the top-left AND the bottom-right toward the
    // center, so both of those corners carry a bright sheen that fades along the rim toward
    // the dim diagonal (top-right / bottom-left). The fade goes straight to transparent in
    // the middle — no shadow-colored layer — so the transition reads as natural light.
    let edge_w = (radius * 0.5)
        .min(4.0)
        .min(rect.width().min(rect.height()) / 16.0)
        .max(1.0);
    let a1 = (255.0 * edge_strength).clamp(0.0, 255.0) as u8;
    let a2 = (255.0 * edge_strength * 0.85).clamp(0.0, 255.0) as u8;
    // Evenly spaced at 0, .25, .5, .75, 1: bright corner -> half-bright shoulder ->
    // transparent middle -> half-bright shoulder -> bright corner. The shoulders are kept
    // at half strength (not faint) so the gloss reaches well along the edges before fading,
    // matching how far Pillow's highlight extends from each corner.
    let s1 = (a1 as f32 * 0.5) as u8;
    let s2 = (a2 as f32 * 0.5) as u8;
    let colors = [
        Color::from_argb(a1, 255, 255, 255).into(),
        Color::from_argb(s1, 255, 255, 255).into(),
        Color::from_argb(0, 255, 255, 255).into(),
        Color::from_argb(s2, 255, 255, 255).into(),
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
        // A crisp band hugging the rim (~edge_w wide) that drops straight to the interior
        // in ~1px, like Pillow. No mask blur: the stroke's own anti-aliasing gives the ~1px
        // soft inner edge. A blurred inner edge instead ramps over 2-3px and reads as an
        // intermediate band ("interlayer"). Clipped to the panel so AA never crosses the
        // edge into the contact shadow.
        let inset = edge_w * 0.5;
        canvas.save();
        canvas.clip_rrect(
            RRect::new_rect_radii(rect, &glass_corner_radii(radius, corners)),
            ClipOp::Intersect,
            true,
        );
        let mut edge = Paint::default();
        edge.set_anti_alias(true);
        edge.set_style(PaintStyle::Stroke);
        edge.set_stroke_width(edge_w);
        edge.set_shader(shader);
        canvas.draw_rrect(
            RRect::new_rect_radii(
                rect.with_inset((inset, inset)),
                &glass_corner_radii((radius - inset).max(0.0), corners),
            ),
            &edge,
        );
        canvas.restore();
    }
}

fn encode_surface(
    mut surface: Surface,
    export_format: &str,
    jpg_quality: i32,
) -> Result<RenderedImage, String> {
    let started = Instant::now();
    let width = surface.width();
    let height = surface.height();
    let data = if export_format == "jpg" {
        let image = surface.image_snapshot();
        let quality = jpg_quality.clamp(1, 100) as u32;
        EncodedBytes::Skia(
            image
                .encode(None, EncodedImageFormat::JPEG, Some(quality))
                .ok_or_else(|| "failed to encode image".to_string())?,
        )
    } else if std::env::var("HARUKI_SKIA_PNG_ENCODER").as_deref() != Ok("skia") {
        EncodedBytes::Owned(encode_surface_mtpng(&mut surface)?)
    } else {
        let image = surface.image_snapshot();
        // PNG is lossless, so deflate settings only trade encode speed vs file size, never
        // pixels. skia's default (used by Image::encode) is z_lib_level=6 + FilterFlag::ALL,
        // which runs the full 5-filter per-row search — the single biggest cost of the render.
        // Level 3 with just SUB|UP filters cuts that CPU substantially; output is byte-identical
        // when decoded, only the encoded size grows modestly.
        let mut opts = png_encoder::Options::default();
        opts.z_lib_level = 3;
        opts.filter_flags = png_encoder::FilterFlag::SUB | png_encoder::FilterFlag::UP;
        EncodedBytes::Skia(
            png_encoder::encode_image(None, &image, &opts)
                .ok_or_else(|| "failed to encode image".to_string())?,
        )
    };
    let (media_type, filename) = if export_format == "jpg" {
        ("image/jpeg", "image.jpg")
    } else {
        ("image/png", "image.png")
    };
    Ok(RenderedImage {
        bytes: data,
        media_type,
        filename,
        width,
        height,
        encode_elapsed: started.elapsed().as_secs_f64(),
        metrics: NativeMetrics::default(),
    })
}

fn encode_surface_mtpng(surface: &mut Surface) -> Result<Vec<u8>, String> {
    let width = surface.width();
    let height = surface.height();
    let row_bytes = width as usize * 4;
    let mut pixels = vec![0_u8; row_bytes * height as usize];
    let info = ImageInfo::new(
        (width, height),
        ColorType::RGBA8888,
        AlphaType::Unpremul,
        None,
    );
    if !surface.read_pixels(&info, &mut pixels, row_bytes, (0, 0)) {
        return Err("failed to read RGBA pixels for mtpng".to_string());
    }

    let mut header = MtpngHeader::new();
    header
        .set_size(width as u32, height as u32)
        .map_err(|err| format!("mtpng header size failed: {err}"))?;
    header
        .set_color(MtpngColorType::TruecolorAlpha, 8)
        .map_err(|err| format!("mtpng header color failed: {err}"))?;
    let mut options = MtpngOptions::new();
    options
        .set_compression_level(CompressionLevel::Fast)
        .map_err(|err| format!("mtpng options failed: {err}"))?;
    let mut encoder = MtpngEncoder::new(Vec::new(), &options);
    encoder
        .write_header(&header)
        .map_err(|err| format!("mtpng header encode failed: {err}"))?;
    encoder
        .write_image_rows(&pixels)
        .map_err(|err| format!("mtpng pixel encode failed: {err}"))?;
    encoder
        .finish()
        .map_err(|err| format!("mtpng finish failed: {err}"))
}

fn decode_image_file(full_path: &Path) -> Result<Image, String> {
    let data = Data::from_filename(full_path)
        .ok_or_else(|| format!("failed to memory-map {}", full_path.display()))?;
    Image::from_encoded(data).ok_or_else(|| format!("failed to decode {}", full_path.display()))
}

const MIB: u64 = 1024 * 1024;
const DEFAULT_RASTER_CACHE_MB: u64 = 256;
const DEFAULT_RASTER_CACHE_MAX_ENTRY_MB: u64 = 16;
const DEFAULT_RASTER_CACHE_OVERSAMPLE: i32 = 1;
const IMAGE_DIMENSION_CACHE_CAP: u64 = 16_384;

#[derive(Clone, Debug, Hash, PartialEq, Eq)]
struct AssetIdentity {
    full_path: PathBuf,
    mtime_ns: u128,
    file_size: u64,
}

#[derive(Clone)]
pub(crate) struct AssetDescriptor {
    identity: AssetIdentity,
    pub(crate) width: i32,
    pub(crate) height: i32,
}

pub(crate) struct LoadedAssetDescriptor {
    pub(crate) descriptor: AssetDescriptor,
    /// Reuse the lazy image opened while discovering dimensions on a metadata-cache miss.
    /// It is intentionally not retained globally: after target rasterization it can release
    /// the full-size decoded pixels instead of growing RSS with the source asset catalogue.
    pub(crate) source: Option<Image>,
}

#[derive(Clone, Debug, Hash, PartialEq, Eq)]
struct RasterCacheKey {
    asset: AssetIdentity,
    src_bits: [u32; 4],
    width: i32,
    height: i32,
    sampling: u8,
}

#[derive(Clone)]
struct RasterCacheValue {
    image: Image,
    byte_size: u32,
}

#[derive(Clone, Copy, Debug, PartialEq, Eq)]
pub(crate) enum RasterCacheOutcome {
    Hit,
    Miss,
    Coalesced,
}

pub(crate) struct RasterCacheResult {
    pub(crate) image: Image,
    pub(crate) outcome: RasterCacheOutcome,
}

#[derive(Clone, Copy)]
struct RasterCacheConfig {
    max_bytes: u64,
    max_entry_bytes: u64,
    oversample: i32,
}

#[derive(Clone, Copy)]
pub(crate) struct RasterCacheSnapshot {
    pub(crate) max_bytes: u64,
    pub(crate) max_entry_bytes: u64,
    pub(crate) oversample: i32,
    pub(crate) entries: u64,
    pub(crate) bytes: u64,
}

static RASTER_CACHE_CONFIG: OnceLock<RasterCacheConfig> = OnceLock::new();
static RASTER_IMAGE_CACHE: OnceLock<Option<Cache<RasterCacheKey, RasterCacheValue>>> =
    OnceLock::new();
static IMAGE_DIMENSION_CACHE: OnceLock<Cache<AssetIdentity, [i32; 2]>> = OnceLock::new();

fn env_mb(name: &str, default_mb: u64) -> u64 {
    std::env::var(name)
        .ok()
        .and_then(|value| value.parse::<u64>().ok())
        .unwrap_or(default_mb)
        .saturating_mul(MIB)
}

fn raster_cache_config() -> &'static RasterCacheConfig {
    RASTER_CACHE_CONFIG.get_or_init(|| RasterCacheConfig {
        max_bytes: env_mb("HARUKI_SKIA_RASTER_CACHE_MB", DEFAULT_RASTER_CACHE_MB),
        max_entry_bytes: env_mb(
            "HARUKI_SKIA_RASTER_CACHE_MAX_ENTRY_MB",
            DEFAULT_RASTER_CACHE_MAX_ENTRY_MB,
        ),
        oversample: std::env::var("HARUKI_SKIA_RASTER_CACHE_OVERSAMPLE")
            .ok()
            .and_then(|value| value.parse::<i32>().ok())
            .unwrap_or(DEFAULT_RASTER_CACHE_OVERSAMPLE)
            .clamp(1, 4),
    })
}

fn raster_image_cache() -> Option<&'static Cache<RasterCacheKey, RasterCacheValue>> {
    RASTER_IMAGE_CACHE
        .get_or_init(|| {
            let config = raster_cache_config();
            (config.max_bytes > 0).then(|| {
                Cache::builder()
                    .max_capacity(config.max_bytes)
                    .weigher(|_, value: &RasterCacheValue| value.byte_size)
                    .build()
            })
        })
        .as_ref()
}

fn image_dimension_cache() -> &'static Cache<AssetIdentity, [i32; 2]> {
    IMAGE_DIMENSION_CACHE.get_or_init(|| Cache::new(IMAGE_DIMENSION_CACHE_CAP))
}

fn asset_identity(full_path: PathBuf, meta: &fs::Metadata) -> AssetIdentity {
    let mtime_ns = meta
        .modified()
        .ok()
        .and_then(|time| time.duration_since(std::time::UNIX_EPOCH).ok())
        .map(|duration| duration.as_nanos())
        .unwrap_or(0);
    AssetIdentity {
        full_path,
        mtime_ns,
        file_size: meta.len(),
    }
}

/// Resolve and inspect an asset without retaining its full-size decoded pixels globally.
/// Dimension metadata is cached by `(path, mtime, size)`, while the optional lazy source image
/// is handed to the first target-raster build and dropped immediately afterwards.
pub(crate) fn load_asset_descriptor(
    base: &Path,
    path: &str,
) -> Result<LoadedAssetDescriptor, String> {
    let full_path = resolve_asset_path(base, path)?;
    let meta = fs::metadata(&full_path)
        .map_err(|err| format!("failed to stat {}: {err}", full_path.display()))?;
    let identity = asset_identity(full_path, &meta);
    if let Some([width, height]) = image_dimension_cache().get(&identity) {
        return Ok(LoadedAssetDescriptor {
            descriptor: AssetDescriptor {
                identity,
                width,
                height,
            },
            source: None,
        });
    }

    let source = decode_image_file(&identity.full_path)?;
    let width = source.width();
    let height = source.height();
    if width <= 0 || height <= 0 {
        return Err(format!(
            "decoded image has invalid dimensions: {}",
            identity.full_path.display()
        ));
    }
    image_dimension_cache().insert(identity.clone(), [width, height]);
    Ok(LoadedAssetDescriptor {
        descriptor: AssetDescriptor {
            identity,
            width,
            height,
        },
        source: Some(source),
    })
}

pub(crate) fn decode_asset_descriptor(descriptor: &AssetDescriptor) -> Result<Image, String> {
    decode_image_file(&descriptor.identity.full_path)
}

fn normalized_float_bits(value: f32) -> u32 {
    if value == 0.0 { 0 } else { value.to_bits() }
}

fn build_target_raster(
    source: &Image,
    source_rect: Rect,
    width: i32,
    height: i32,
    sampling: SamplingOptions,
) -> Result<Image, String> {
    let mut current = source.clone();
    let mut current_src = source_rect;

    // CPU raster images do not retain Skia's lazy mip chain. Build only the levels needed for
    // this destination so a 740px jacket is reduced 740→370→185→93→64 instead of one aliased
    // bilinear jump. Each previous level is released immediately; only the final raster is cached.
    if sampling.mipmap != MipmapMode::None {
        loop {
            let next_width = (current_src.width() * 0.5).ceil() as i32;
            let next_height = (current_src.height() * 0.5).ceil() as i32;
            if next_width < width || next_height < height {
                break;
            }
            if next_width == current_src.width().ceil() as i32
                && next_height == current_src.height().ceil() as i32
            {
                break;
            }
            current = draw_source_to_raster(
                &current,
                current_src,
                next_width,
                next_height,
                SamplingOptions::new(FilterMode::Linear, MipmapMode::None),
            )?;
            current_src = Rect::from_xywh(0.0, 0.0, next_width as f32, next_height as f32);
        }
    }

    let final_sampling = if sampling.use_cubic {
        sampling
    } else {
        SamplingOptions::new(sampling.filter, MipmapMode::None)
    };
    draw_source_to_raster(&current, current_src, width, height, final_sampling)
}

fn draw_source_to_raster(
    source: &Image,
    source_rect: Rect,
    width: i32,
    height: i32,
    sampling: SamplingOptions,
) -> Result<Image, String> {
    let mut surface = surfaces::raster_n32_premul((width, height))
        .ok_or_else(|| format!("failed to create target raster {width}x{height}"))?;
    surface.canvas().clear(Color::TRANSPARENT);
    let mut paint = Paint::default();
    paint.set_anti_alias(true);
    surface.canvas().draw_image_rect_with_sampling_options(
        source,
        Some((&source_rect, SrcRectConstraint::Strict)),
        Rect::from_xywh(0.0, 0.0, width as f32, height as f32),
        sampling,
        &paint,
    );
    Ok(surface.image_snapshot())
}

/// Return a process-wide cached raster at the image's actual draw size. `None` means the cache
/// is disabled or this entry exceeds its per-item budget, so the caller should draw the source
/// directly. Moka coalesces concurrent misses for the same key.
pub(crate) fn rasterize_asset_cached(
    descriptor: &AssetDescriptor,
    source: Option<&Image>,
    source_rect: Rect,
    width: i32,
    height: i32,
    sampling: SamplingOptions,
    sampling_key: u8,
) -> Result<Option<RasterCacheResult>, String> {
    if width <= 0 || height <= 0 {
        return Ok(None);
    }
    let config = raster_cache_config();
    let raster_width = width.saturating_mul(config.oversample);
    let raster_height = height.saturating_mul(config.oversample);
    let byte_size = (raster_width as u64)
        .saturating_mul(raster_height as u64)
        .saturating_mul(4);
    let Some(cache) = raster_image_cache() else {
        return Ok(None);
    };
    if byte_size == 0 || byte_size > config.max_entry_bytes || byte_size > u32::MAX as u64 {
        return Ok(None);
    }

    let key = RasterCacheKey {
        asset: descriptor.identity.clone(),
        src_bits: [
            normalized_float_bits(source_rect.left),
            normalized_float_bits(source_rect.top),
            normalized_float_bits(source_rect.right),
            normalized_float_bits(source_rect.bottom),
        ],
        width: raster_width,
        height: raster_height,
        sampling: sampling_key,
    };
    if let Some(value) = cache.get(&key) {
        return Ok(Some(RasterCacheResult {
            image: value.image,
            outcome: RasterCacheOutcome::Hit,
        }));
    }

    let did_build = Cell::new(false);
    let value = cache
        .try_get_with(key, || {
            did_build.set(true);
            let decoded = if source.is_none() {
                Some(decode_asset_descriptor(descriptor)?)
            } else {
                None
            };
            let source = source.or(decoded.as_ref()).expect("source image available");
            let image =
                build_target_raster(source, source_rect, raster_width, raster_height, sampling)?;
            Ok::<RasterCacheValue, String>(RasterCacheValue {
                image,
                byte_size: byte_size as u32,
            })
        })
        .map_err(|err| err.as_ref().clone())?;
    Ok(Some(RasterCacheResult {
        image: value.image,
        outcome: if did_build.get() {
            RasterCacheOutcome::Miss
        } else {
            RasterCacheOutcome::Coalesced
        },
    }))
}

pub(crate) fn raster_cache_snapshot() -> RasterCacheSnapshot {
    let config = raster_cache_config();
    let (entries, bytes) = raster_image_cache()
        .map(|cache| (cache.entry_count(), cache.weighted_size()))
        .unwrap_or_default();
    RasterCacheSnapshot {
        max_bytes: config.max_bytes,
        max_entry_bytes: config.max_entry_bytes,
        oversample: config.oversample,
        entries,
        bytes,
    }
}

#[pyfunction]
fn renderer_cache_stats(py: Python<'_>) -> PyResult<Py<PyDict>> {
    if let Some(cache) = raster_image_cache() {
        cache.run_pending_tasks();
    }
    image_dimension_cache().run_pending_tasks();
    let snapshot = raster_cache_snapshot();
    let dict = PyDict::new(py);
    dict.set_item("raster_cache_max_bytes", snapshot.max_bytes)?;
    dict.set_item("raster_cache_max_entry_bytes", snapshot.max_entry_bytes)?;
    dict.set_item("raster_cache_oversample", snapshot.oversample)?;
    dict.set_item("raster_cache_entries", snapshot.entries)?;
    dict.set_item("raster_cache_bytes", snapshot.bytes)?;
    dict.set_item(
        "dimension_cache_entries",
        image_dimension_cache().entry_count(),
    )?;
    Ok(dict.unbind())
}

#[pyfunction]
fn clear_renderer_caches() {
    if let Some(cache) = raster_image_cache() {
        cache.invalidate_all();
        cache.run_pending_tasks();
    }
    let dimensions = image_dimension_cache();
    dimensions.invalidate_all();
    dimensions.run_pending_tasks();
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

static TYPEFACE_CACHE: OnceLock<Mutex<HashMap<String, Typeface>>> = OnceLock::new();

/// Load a typeface, caching it process-wide by (dir, name) so each render does not
/// re-read and re-parse the font files (Typeface is cheap to clone — ref-counted).
fn load_typeface(dir: &str, name: &str) -> Typeface {
    let key = format!("{dir}\0{name}");
    let cache = TYPEFACE_CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    if let Ok(mut cache) = cache.lock() {
        if let Some(typeface) = cache.get(&key) {
            return typeface.clone();
        }
        let typeface = load_typeface_uncached(dir, name);
        cache.insert(key, typeface.clone());
        return typeface;
    }
    load_typeface_uncached(dir, name)
}

fn load_typeface_uncached(dir: &str, name: &str) -> Typeface {
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

    #[test]
    fn caches_target_sized_rasters() {
        if raster_cache_config().max_bytes == 0 {
            return;
        }
        let mut surface = surfaces::raster_n32_premul((64, 64)).expect("surface");
        surface.canvas().clear(Color::RED);
        let source = surface.image_snapshot();
        let unique = std::time::SystemTime::now()
            .duration_since(std::time::UNIX_EPOCH)
            .expect("clock")
            .as_nanos();
        let descriptor = AssetDescriptor {
            identity: AssetIdentity {
                full_path: PathBuf::from(format!("/virtual/cache-test-{unique}.png")),
                mtime_ns: unique,
                file_size: 1,
            },
            width: 64,
            height: 64,
        };
        let src = Rect::from_xywh(0.0, 0.0, 64.0, 64.0);
        let first = rasterize_asset_cached(
            &descriptor,
            Some(&source),
            src,
            16,
            16,
            linear_sampling(),
            1,
        )
        .expect("first raster")
        .expect("cache enabled");
        let cached_size = 16 * raster_cache_config().oversample;
        assert_eq!(first.image.dimensions(), (cached_size, cached_size).into());

        let second = rasterize_asset_cached(&descriptor, None, src, 16, 16, linear_sampling(), 1)
            .expect("second raster")
            .expect("cache enabled");
        assert_eq!(second.outcome, RasterCacheOutcome::Hit);
    }

    #[test]
    fn mtpng_round_trips_unpremultiplied_rgba_pixels() {
        let mut surface = surfaces::raster_n32_premul((3, 2)).expect("surface");
        surface.canvas().clear(Color::TRANSPARENT);
        let mut paint = Paint::default();
        paint.set_color(Color::from_argb(127, 20, 80, 140));
        surface
            .canvas()
            .draw_rect(Rect::from_xywh(0.0, 0.0, 2.0, 2.0), &paint);
        paint.set_color(Color::from_argb(255, 240, 10, 60));
        surface
            .canvas()
            .draw_rect(Rect::from_xywh(2.0, 0.0, 1.0, 1.0), &paint);

        let info = ImageInfo::new((3, 2), ColorType::RGBA8888, AlphaType::Unpremul, None);
        let mut expected = vec![0_u8; 3 * 2 * 4];
        assert!(surface.read_pixels(&info, &mut expected, 3 * 4, (0, 0)));

        let encoded = encode_surface_mtpng(&mut surface).expect("mtpng encode");
        let decoded = Image::from_encoded(Data::new_copy(&encoded)).expect("PNG decode");
        let mut actual = vec![0_u8; expected.len()];
        assert!(decoded.read_pixels(
            &info,
            &mut actual,
            3 * 4,
            (0, 0),
            skia_safe::image::CachingHint::Disallow,
        ));
        assert_eq!(actual, expected);
    }
}
