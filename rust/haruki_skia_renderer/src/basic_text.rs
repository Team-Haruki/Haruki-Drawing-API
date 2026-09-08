//! FreeType BASIC layout shared by measurement and native text masks.
//!
//! Skia's platform scaler (CoreText on macOS) is not Pillow's FreeType scaler. In
//! particular, rounding a whole Skia run cannot reproduce hinted glyph advances.
//! This module uses FreeType directly; no Python objects or Pillow calls occur here.
//! The BASIC contract is tested against Pillow, including its legacy 26.6 kerning
//! convention and its bounds including the pen line (not just visible ink).

use std::{
    cell::RefCell,
    collections::VecDeque,
    path::PathBuf,
    sync::{
        Arc, OnceLock,
        atomic::{AtomicU64, Ordering},
    },
    time::SystemTime,
};

use freetype::{
    Face, Library,
    face::{KerningMode, LoadFlag},
};
use moka::sync::Cache;

use crate::{
    font_candidates,
    text_metrics::{TextMetricsRequest, TextMetricsResult, validate_text_metrics_requests},
};

const MAX_FACES: usize = 16;

#[derive(Clone, Debug, Hash, PartialEq, Eq)]
struct FontSource {
    path: PathBuf,
    modified: Option<SystemTime>,
    len: u64,
}

impl FontSource {
    fn resolve(dir: &str, name: &str) -> Result<Self, String> {
        let path = font_candidates(dir, name)
            .into_iter()
            .find(|p| p.is_file())
            .ok_or_else(|| format!("font could not be resolved without fallback: {name:?}"))?
            .canonicalize()
            .map_err(|e| e.to_string())?;
        let meta = path.metadata().map_err(|e| e.to_string())?;
        Ok(Self {
            path,
            modified: meta.modified().ok(),
            len: meta.len(),
        })
    }

    fn unchanged(&self) -> bool {
        self.path
            .metadata()
            .is_ok_and(|meta| meta.len() == self.len && meta.modified().ok() == self.modified)
    }
}

struct FaceEntry {
    source: FontSource,
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
    with_face_source(FontSource::resolve(dir, name)?, size, f)
}

fn with_face_source<T>(
    source: FontSource,
    size: f32,
    f: impl FnOnce(&Face) -> Result<T, String>,
) -> Result<T, String> {
    FACES.with(|cache| {
        let mut cache = cache.borrow_mut();
        let hit = cache.faces.iter().position(|e| e.source == source);
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
                .new_face(&source.path, 0)
                .map_err(|e| format!("FreeType font {:?}: {e}", source.path))?;
            FaceEntry { source, face }
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

#[derive(Clone, Debug, Hash, PartialEq, Eq)]
struct MaskKey {
    source: FontSource,
    size_bits: u32,
    text: String,
}

fn mask_weight(key: &MaskKey, mask: &BasicMask) -> usize {
    mask.pixels
        .capacity()
        .saturating_add(key.text.capacity())
        .saturating_add(key.source.path.capacity())
        .saturating_add(std::mem::size_of::<MaskKey>())
        .saturating_add(std::mem::size_of::<BasicMask>())
        .saturating_add(2 * std::mem::size_of::<usize>()) // Arc's reference counts.
}

struct TextMaskCache {
    values: Option<Cache<MaskKey, Arc<BasicMask>>>,
    max_bytes: u64,
    hits: AtomicU64,
    misses: AtomicU64,
    bypasses: AtomicU64,
}

impl TextMaskCache {
    fn new(max_bytes: u64) -> Self {
        Self {
            values: (max_bytes > 0).then(|| {
                Cache::builder()
                    .max_capacity(max_bytes)
                    .weigher(|key: &MaskKey, mask: &Arc<BasicMask>| {
                        mask_weight(key, mask).min(u32::MAX as usize) as u32
                    })
                    .build()
            }),
            max_bytes,
            hits: AtomicU64::new(0),
            misses: AtomicU64::new(0),
            bypasses: AtomicU64::new(0),
        }
    }

    fn get(&self, key: &MaskKey, max_pixels: u64) -> Result<Option<Arc<BasicMask>>, String> {
        let Some(values) = &self.values else {
            self.bypasses.fetch_add(1, Ordering::Relaxed);
            return Ok(None);
        };
        if let Some(mask) = values.get(key) {
            // A different request's larger budget cannot relax this request's limit.
            if mask.pixels.len() as u64 > max_pixels {
                return Err("native text mask exceeds pixel limit".into());
            }
            self.hits.fetch_add(1, Ordering::Relaxed);
            return Ok(Some(mask));
        }
        self.misses.fetch_add(1, Ordering::Relaxed);
        Ok(None)
    }

    fn insert(&self, key: MaskKey, mask: Arc<BasicMask>) {
        let weight = mask_weight(&key, &mask) as u64;
        let Some(values) = &self.values else {
            return;
        };
        if weight > self.max_bytes || weight > u64::from(u32::MAX) {
            self.bypasses.fetch_add(1, Ordering::Relaxed);
            return;
        }
        values.insert(key, mask);
    }

    fn snapshot(&self) -> TextMaskCacheSnapshot {
        let (entries, bytes) = self
            .values
            .as_ref()
            .map(|values| {
                values.run_pending_tasks();
                (values.entry_count(), values.weighted_size())
            })
            .unwrap_or_default();
        TextMaskCacheSnapshot {
            max_bytes: self.max_bytes,
            entries,
            bytes,
            hits: self.hits.load(Ordering::Relaxed),
            misses: self.misses.load(Ordering::Relaxed),
            bypasses: self.bypasses.load(Ordering::Relaxed),
        }
    }

    fn clear(&self) {
        if let Some(values) = &self.values {
            values.invalidate_all();
            values.run_pending_tasks();
        }
        self.hits.store(0, Ordering::Relaxed);
        self.misses.store(0, Ordering::Relaxed);
        self.bypasses.store(0, Ordering::Relaxed);
    }
}

static TEXT_MASK_CACHE: OnceLock<TextMaskCache> = OnceLock::new();

fn text_mask_cache() -> &'static TextMaskCache {
    TEXT_MASK_CACHE
        .get_or_init(|| TextMaskCache::new(crate::env_mb("HARUKI_SKIA_TEXT_MASK_CACHE_MB", 64)))
}

pub(crate) struct TextMaskCacheSnapshot {
    pub(crate) max_bytes: u64,
    pub(crate) entries: u64,
    pub(crate) bytes: u64,
    pub(crate) hits: u64,
    pub(crate) misses: u64,
    pub(crate) bypasses: u64,
}

pub(crate) fn text_mask_cache_snapshot() -> TextMaskCacheSnapshot {
    text_mask_cache().snapshot()
}

pub(crate) fn clear_text_mask_cache() {
    if let Some(cache) = TEXT_MASK_CACHE.get() {
        cache.clear();
    }
}

/// Cache immutable coverage and metrics; color, placement and the mutable Face stay outside.
pub(crate) fn raster_cached(
    dir: &str,
    name: &str,
    text: &str,
    size: f32,
    max_pixels: u64,
) -> Result<Arc<BasicMask>, String> {
    let cache = text_mask_cache();
    if cache.values.is_none() {
        cache.bypasses.fetch_add(1, Ordering::Relaxed);
        return raster_bounded(dir, name, text, size, max_pixels, usize::MAX).map(Arc::new);
    }
    validate_mask_request(dir, name, text, size)?;
    // Resolve candidates on every request: a newly arrived higher-priority font or a
    // changed symlink target must invalidate a hit, just like replacing the selected file.
    let key = MaskKey {
        source: FontSource::resolve(dir, name)?,
        size_bits: size.to_bits(),
        text: text.to_owned(),
    };
    if let Some(mask) = cache.get(&key, max_pixels)? {
        return Ok(mask);
    }
    // Build with this request's limit. Coalescing fallible builds would let a small-budget
    // request's error fail another request that has enough memory for the same text.
    let mask = Arc::new(with_face_source(key.source.clone(), size, |face| {
        raster_face(face, text, max_pixels, usize::MAX)
    })?);
    // Pin the selected source through the build; if it changed while FreeType loaded it,
    // return the valid in-flight result but do not retain it under the old signature.
    if key.source.unchanged() {
        cache.insert(key, mask.clone());
    }
    Ok(mask)
}

pub(crate) fn raster_bounded(
    dir: &str,
    name: &str,
    text: &str,
    size: f32,
    max_pixels: u64,
    max_dimension: usize,
) -> Result<BasicMask, String> {
    validate_mask_request(dir, name, text, size)?;
    with_face(dir, name, size, |face| {
        raster_face(face, text, max_pixels, max_dimension)
    })
}

fn validate_mask_request(dir: &str, name: &str, text: &str, size: f32) -> Result<(), String> {
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
    )
}

fn raster_face(
    face: &Face,
    text: &str,
    max_pixels: u64,
    max_dimension: usize,
) -> Result<BasicMask, String> {
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
        if bitmap.pixel_mode().map_err(|e| e.to_string())? != freetype::bitmap::PixelMode::Gray {
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
}

#[cfg(test)]
mod tests {
    use super::*;

    fn key(text: &str) -> MaskKey {
        MaskKey {
            source: FontSource {
                path: PathBuf::from("/font/cache-test.otf"),
                modified: None,
                len: 123,
            },
            size_bits: 20.0_f32.to_bits(),
            text: text.to_owned(),
        }
    }

    fn mask(pixels: usize) -> Arc<BasicMask> {
        Arc::new(BasicMask {
            metrics: TextMetricsResult {
                advance: 1.0,
                ink_bbox: [0.0; 4],
                pillow_bbox: [0.0; 4],
                ascent: 1.0,
                descent: 0.0,
                leading: 0.0,
                line_spacing: 1.0,
                font_top: 0.0,
                font_bottom: 1.0,
                cap_height: 1.0,
                x_height: 1.0,
            },
            reference_height: 1.0,
            pixels: vec![97; pixels],
            width: pixels,
            height: 1,
        })
    }

    #[test]
    fn mask_cache_checks_each_requests_limit_even_on_a_hit() {
        let cache = TextMaskCache::new(4096);
        let image = mask(64);
        cache.insert(key("text"), image.clone());
        assert!(cache.get(&key("text"), 63).is_err());
        let hit = cache.get(&key("text"), 64).unwrap().unwrap();
        assert!(Arc::ptr_eq(&image, &hit));
        assert_eq!(cache.snapshot().hits, 1);
    }

    #[test]
    fn mask_cache_disable_and_oversized_entries_do_not_retain_pixels() {
        let disabled = TextMaskCache::new(0);
        disabled.insert(key("disabled"), mask(100));
        assert!(disabled.get(&key("disabled"), 100).unwrap().is_none());
        assert_eq!(disabled.snapshot().bytes, 0);
        assert_eq!(disabled.snapshot().bypasses, 1);

        let bounded = TextMaskCache::new(1024);
        bounded.insert(key("oversized"), mask(1024)); // Key/metrics also need bytes.
        let snapshot = bounded.snapshot();
        assert_eq!(snapshot.entries, 0);
        assert_eq!(snapshot.bytes, 0);
        assert_eq!(snapshot.bypasses, 1);
    }

    #[test]
    fn mask_cache_evicts_by_weight_and_clear_preserves_in_flight_arcs() {
        let retained = mask(128);
        let capacity = (mask_weight(&key("a"), &retained) * 2) as u64;
        let cache = TextMaskCache::new(capacity);
        for text in ["a", "b", "c", "d", "e", "f"] {
            cache.insert(key(text), retained.clone());
        }
        let snapshot = cache.snapshot();
        assert!(snapshot.bytes <= capacity);
        assert!(snapshot.entries < 6);
        assert!(snapshot.bytes >= retained.pixels.len() as u64);
        cache.clear();
        let snapshot = cache.snapshot();
        assert_eq!(
            (
                snapshot.entries,
                snapshot.bytes,
                snapshot.hits,
                snapshot.misses
            ),
            (0, 0, 0, 0)
        );
        assert_eq!(retained.pixels, vec![97; 128]);
    }

    #[test]
    fn concurrent_mask_hits_keep_request_budgets_independent() {
        let cache = TextMaskCache::new(4096);
        cache.insert(key("shared"), mask(64));
        std::thread::scope(|scope| {
            let tasks: Vec<_> = (0..8)
                .map(|index| {
                    let cache = &cache;
                    scope.spawn(move || {
                        let limit = if index % 2 == 0 { 63 } else { 64 };
                        let result = cache.get(&key("shared"), limit);
                        assert_eq!(result.is_ok(), limit == 64);
                        if let Ok(Some(mask)) = result {
                            assert_eq!(mask.pixels.len(), 64);
                        }
                    })
                })
                .collect();
            for task in tasks {
                task.join().unwrap();
            }
        });
        assert_eq!(cache.snapshot().hits, 4);
    }
}
