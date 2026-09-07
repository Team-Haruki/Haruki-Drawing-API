//! FreeType BASIC layout shared by measurement and native text masks.
//!
//! Skia's platform scaler (CoreText on macOS) is not Pillow's FreeType scaler. In
//! particular, rounding a whole Skia run cannot reproduce hinted glyph advances.
//! This module uses FreeType directly; no Python objects or Pillow calls occur here.
//! The BASIC contract is tested against Pillow, including its legacy 26.6 kerning
//! convention and its bounds including the pen line (not just visible ink).

use std::{cell::RefCell, collections::VecDeque, path::PathBuf};

use freetype::{
    Face, Library,
    face::{KerningMode, LoadFlag},
};

use crate::{
    font_candidates,
    text_metrics::{TextMetricsRequest, TextMetricsResult, validate_text_metrics_requests},
};

const MAX_FACES: usize = 16;

struct FaceEntry {
    path: PathBuf,
    modified: Option<std::time::SystemTime>,
    len: u64,
    face: Face,
}

#[derive(Default)]
struct FaceCache {
    // Faces retain their library. A library and mutable faces belong to one thread;
    // no global FreeType lock serializes no-GIL rendering workers.
    faces: VecDeque<FaceEntry>,
    library: Option<Library>,
}

thread_local! { static FACES: RefCell<FaceCache> = RefCell::new(FaceCache::default()); }

pub(crate) fn with_face<T>(
    dir: &str,
    name: &str,
    size: f32,
    f: impl FnOnce(&Face) -> Result<T, String>,
) -> Result<T, String> {
    let path = font_candidates(dir, name)
        .into_iter()
        .find(|p| p.is_file())
        .ok_or_else(|| format!("font could not be resolved without fallback: {name:?}"))?;
    let meta = path.metadata().map_err(|e| e.to_string())?;
    FACES.with(|cache| {
        let mut cache = cache.borrow_mut();
        let hit = cache.faces.iter().position(|e| {
            e.path == path && e.len == meta.len() && e.modified == meta.modified().ok()
        });
        let entry = if let Some(i) = hit {
            cache.faces.remove(i).expect("existing face")
        } else {
            if cache.library.is_none() {
                cache.library = Some(Library::init().map_err(|e| e.to_string())?);
            }
            let face = cache
                .library
                .as_ref()
                .unwrap()
                .new_face(&path, 0)
                .map_err(|e| format!("FreeType font {path:?}: {e}"))?;
            FaceEntry {
                path,
                modified: meta.modified().ok(),
                len: meta.len(),
                face,
            }
        };
        entry
            .face
            .set_char_size((size * 64.0) as isize, (size * 64.0) as isize, 0, 0)
            .map_err(|e| e.to_string())?;
        let result = f(&entry.face);
        cache.faces.push_front(entry);
        cache.faces.truncate(MAX_FACES);
        result
    })
}

pub(crate) fn pixel(x: i64) -> i64 {
    (x + 32) >> 6
}

pub(crate) struct BasicLayout {
    pub(crate) metrics: TextMetricsResult,
    /// Glyph indices and pixel x origins, relative to the alphabetic baseline.
    pub(crate) glyphs: Vec<(u32, i64)>,
}

pub(crate) fn layout(face: &Face, text: &str) -> Result<BasicLayout, String> {
    let mut pen = 0_i64;
    let mut previous = 0;
    let mut bounds = [0_i64; 4]; // x_min, y_min, x_max, y_max, y points up
    let mut glyphs = Vec::with_capacity(text.chars().count());
    for ch in text.chars() {
        let index = face.get_char_index(ch as usize).unwrap_or(0);
        if previous != 0 && index != 0 && face.has_kerning() {
            let delta = face
                .get_kerning(previous, index, KerningMode::KerningDefault)
                .map_err(|e| e.to_string())?;
            // BASIC historically adds a pixel-valued kern to a 26.6 advance.
            // Preserve that contract; applying the full kern changes existing layouts.
            pen += pixel(delta.x as i64);
        }
        face.load_glyph(index, LoadFlag::DEFAULT)
            .map_err(|e| e.to_string())?;
        let slot = face.glyph();
        let bbox = slot
            .get_glyph()
            .map_err(|e| e.to_string())?
            .get_cbox(freetype::ffi::FT_GLYPH_BBOX_PIXELS);
        let x = pixel(pen);
        glyphs.push((index, x));
        bounds[0] = bounds[0].min(x + bbox.xMin as i64);
        bounds[1] = bounds[1].min(bbox.yMin as i64);
        bounds[2] = bounds[2].max(x + bbox.xMax as i64);
        bounds[3] = bounds[3].max(bbox.yMax as i64);
        pen += slot.metrics().horiAdvance as i64;
        bounds[2] = bounds[2].max(pixel(pen));
        previous = index;
    }
    let fm = face.size_metrics().ok_or("missing FreeType size metrics")?;
    let ascent = pixel(fm.ascender as i64) as f32;
    let descent = -pixel(fm.descender as i64) as f32;
    let ink = [
        bounds[0] as f32,
        -bounds[3] as f32,
        bounds[2] as f32,
        -bounds[1] as f32,
    ];
    let pillow_bbox = if text.is_empty() {
        [0.0; 4]
    } else {
        [ink[0], ink[1] + ascent, ink[2], ink[3] + ascent]
    };
    let line_spacing = pixel(fm.height as i64) as f32;
    let glyph_height = |ch: char| -> Result<f32, String> {
        face.load_glyph(
            face.get_char_index(ch as usize).unwrap_or(0),
            LoadFlag::DEFAULT,
        )
        .map_err(|e| e.to_string())?;
        Ok(face.glyph().metrics().height as f32 / 64.0)
    };
    let scale_y = fm.y_scale as f32 / 65536.0 / 64.0;
    Ok(BasicLayout {
        glyphs,
        metrics: TextMetricsResult {
            advance: pen as f32 / 64.0,
            ink_bbox: ink,
            pillow_bbox,
            ascent,
            descent,
            leading: line_spacing - ascent - descent,
            line_spacing,
            font_top: -(face.raw().bbox.yMax as f32) * scale_y,
            font_bottom: -(face.raw().bbox.yMin as f32) * scale_y,
            cap_height: glyph_height('H')?,
            x_height: glyph_height('x')?,
        },
    })
}

pub(crate) fn measure_batch(
    dir: &str,
    name: &str,
    requests: &[TextMetricsRequest],
) -> Result<Vec<TextMetricsResult>, String> {
    validate_text_metrics_requests(dir, name, requests)?;
    requests
        .iter()
        .map(|r| with_face(dir, name, r.size, |face| Ok(layout(face, &r.text)?.metrics)))
        .collect()
}

pub(crate) struct BasicMask {
    pub(crate) metrics: TextMetricsResult,
    pub(crate) reference_height: f32,
    pub(crate) pixels: Vec<u8>,
    pub(crate) width: usize,
    pub(crate) height: usize,
}

pub(crate) fn raster(
    dir: &str,
    name: &str,
    text: &str,
    size: f32,
    max_pixels: u64,
) -> Result<BasicMask, String> {
    raster_bounded(dir, name, text, size, max_pixels, usize::MAX)
}

pub(crate) fn raster_bounded(
    dir: &str,
    name: &str,
    text: &str,
    size: f32,
    max_pixels: u64,
    max_dimension: usize,
) -> Result<BasicMask, String> {
    if text.len() > crate::text_metrics::MAX_TEXT_METRICS_CHARS * 4 {
        return Err("native text mask exceeds character limit".into());
    }
    validate_text_metrics_requests(
        dir,
        name,
        &[TextMetricsRequest {
            text: text.to_owned(),
            size,
        }],
    )?;
    with_face(dir, name, size, |face| {
        let laid = layout(face, text)?;
        let bbox = laid.metrics.ink_bbox;
        let width = (bbox[2] - bbox[0]) as usize;
        let height = (bbox[3] - bbox[1]) as usize;
        if width > max_dimension || height > max_dimension {
            return Err("native text mask exceeds dimension limit".into());
        }
        let count = width
            .checked_mul(height)
            .filter(|&n| n as u64 <= max_pixels)
            .ok_or("native text mask exceeds pixel limit")?;
        let mut pixels = Vec::new();
        pixels
            .try_reserve_exact(count)
            .map_err(|_| "cannot allocate native text mask")?;
        pixels.resize(count, 0_u8);
        for (index, x) in laid.glyphs {
            face.load_glyph(index, LoadFlag::DEFAULT | LoadFlag::RENDER)
                .map_err(|e| e.to_string())?;
            let slot = face.glyph();
            let bitmap = slot.bitmap();
            if bitmap.width() == 0 || bitmap.rows() == 0 {
                continue;
            }
            if bitmap.pixel_mode().map_err(|e| e.to_string())? != freetype::bitmap::PixelMode::Gray
            {
                return Err("BASIC text requires an 8-bit grayscale outline font".into());
            }
            let stride = bitmap.pitch().unsigned_abs() as usize;
            for row in 0..bitmap.rows() as usize {
                let y = row as i64 - slot.bitmap_top() as i64 - bbox[1] as i64;
                if y < 0 || y >= height as i64 {
                    continue;
                }
                let src_row = if bitmap.pitch() < 0 {
                    bitmap.rows() as usize - row - 1
                } else {
                    row
                };
                for col in 0..bitmap.width() as usize {
                    let dx = x + slot.bitmap_left() as i64 + col as i64 - bbox[0] as i64;
                    if dx < 0 || dx >= width as i64 {
                        continue;
                    }
                    let alpha = bitmap.buffer()[src_row * stride + col] as u32;
                    let dst = &mut pixels[y as usize * width + dx as usize];
                    let v = *dst as u32 * (255 - alpha) + 128;
                    *dst = (alpha + ((v + (v >> 8)) >> 8)) as u8;
                }
            }
        }
        let reference = layout(face, "哇")?.metrics.ink_bbox;
        Ok(BasicMask {
            metrics: laid.metrics,
            reference_height: reference[3] - reference[1],
            pixels,
            width,
            height,
        })
    })
}
