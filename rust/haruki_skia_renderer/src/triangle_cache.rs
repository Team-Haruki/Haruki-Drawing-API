//! Immutable background tiles in the existing bounded raster pool. Capture the pixels of
//! an ordinary full background draw; never render cropped/translated gradients (rounding
//! would differ). Keys carry resolved palette bytes and the exact caller-provided scatter,
//! so the clock is neither frozen nor approximated. Page content is never cached here.

use std::sync::{
    Arc,
    atomic::{AtomicU64, Ordering},
};

use skia_safe::{BlendMode, IRect, Paint, Surface};

use crate::{
    RasterCacheConfig, RasterCacheKey, RasterCacheValue, ir::TriangleBgNode, raster_cache_config,
    raster_image_cache, triangle_palette,
};

const MAX_KEY_BYTES: usize = 64 * 1024;
const TILE_ROWS: i32 = 512;
static HITS: AtomicU64 = AtomicU64::new(0);
static MISSES: AtomicU64 = AtomicU64::new(0);
static BYPASSES: AtomicU64 = AtomicU64::new(0);

#[derive(Debug, Hash, PartialEq, Eq)]
pub(crate) struct BackgroundKey {
    width: i32,
    height: i32,
    palette: [u8; 15],
    triangles: Vec<[u32; 9]>,
}

pub(crate) struct Plan {
    tiles: Vec<(RasterCacheKey, IRect, u32)>,
    pub(crate) retained_bytes: usize,
}

pub(crate) fn plan(
    surface: &mut Surface,
    bg: &TriangleBgNode,
    width: f32,
    height: f32,
    available_scene_bytes: usize,
) -> Option<Plan> {
    let result = make_plan(
        surface,
        bg,
        width,
        height,
        raster_cache_config(),
        available_scene_bytes,
    );
    if result.is_none() {
        BYPASSES.fetch_add(1, Ordering::Relaxed);
    }
    result
}

fn make_plan(
    surface: &mut Surface,
    bg: &TriangleBgNode,
    width: f32,
    height: f32,
    config: &RasterCacheConfig,
    available_scene_bytes: usize,
) -> Option<Plan> {
    let (w, h) = (surface.width(), surface.height());
    let canvas = surface.canvas();
    // Only a full, opaque, untransformed background overwrites every captured pixel.
    // Clipped/translated/scaled backgrounds retain the ordinary drawing path.
    if width != w as f32
        || height != h as f32
        || !canvas.local_to_device_as_3x3().is_identity()
        || !canvas.is_clip_rect()
        || canvas.device_clip_bounds() != Some(IRect::from_wh(w, h))
        || config.max_bytes == 0
        || config.max_entry_bytes == 0
        || w <= 0
        || h <= 0
    {
        return None;
    }
    let key_bytes = bg
        .tris
        .len()
        .checked_mul(std::mem::size_of::<[u32; 9]>())?
        .checked_add(
            std::mem::size_of::<BackgroundKey>() + std::mem::size_of::<RasterCacheKey>(),
        )?;
    if key_bytes > MAX_KEY_BYTES || key_bytes as u64 >= config.max_entry_bytes {
        return None;
    }
    let row_bytes = (w as u64).checked_mul(4)?;
    let rows = ((config.max_entry_bytes - key_bytes as u64) / row_bytes)
        .min(TILE_ROWS as u64)
        .min(h as u64) as i32;
    if rows <= 0 {
        return None;
    }
    let tile_count = (h as u64).div_ceil(rows as u64);
    let total_bytes = row_bytes
        .checked_mul(h as u64)?
        .checked_add(tile_count.checked_mul(key_bytes as u64)?)?;
    // One long background must not evict the whole asset pool. This is a per-background
    // admission bound; all backgrounds AND assets still share the existing hard capacity.
    if total_bytes > config.max_bytes / 4 || total_bytes > available_scene_bytes as u64 {
        return None;
    }
    let (g1, g2, a, b, white) = triangle_palette(bg.hour, bg.time_color, bg.main_hue);
    let background = Arc::new(BackgroundKey {
        width: w,
        height: h,
        palette: [
            g1[0], g1[1], g1[2], g2[0], g2[1], g2[2], a.0, a.1, a.2, a.3, b.0, b.1, b.2, b.3, white,
        ],
        triangles: bg.tris.iter().map(|tri| tri.map(f32::to_bits)).collect(),
    });
    let mut tiles = Vec::with_capacity(tile_count as usize);
    let mut top = 0;
    while top < h {
        let height = rows.min(h - top);
        let bytes = row_bytes * height as u64 + key_bytes as u64;
        if bytes > u32::MAX as u64 {
            return None;
        }
        tiles.push((
            RasterCacheKey::TriangleTile {
                background: Arc::clone(&background),
                top,
                height,
            },
            IRect::from_xywh(0, top, w, height),
            bytes as u32,
        ));
        top += height;
    }
    Some(Plan {
        tiles,
        retained_bytes: total_bytes as usize,
    })
}

pub(crate) fn draw_cached(surface: &mut Surface, plan: &Plan) -> bool {
    let Some(cache) = raster_image_cache() else {
        return false;
    };
    // Resolve ALL tiles before touching the destination. Strong Image references survive
    // concurrent eviction, and a partial miss simply redraws the whole background.
    let mut images = Vec::with_capacity(plan.tiles.len());
    for (key, bounds, _) in &plan.tiles {
        if let Some(value) = cache.get(key) {
            images.push((value.image, *bounds));
        }
    }
    // Do not short-circuit on the first miss: Moka's frequency admission must see
    // accesses to every tile, otherwise only a prefix becomes hot under cache pressure.
    if images.len() != plan.tiles.len() {
        MISSES.fetch_add(1, Ordering::Relaxed);
        return false;
    }
    let mut paint = Paint::default();
    paint.set_blend_mode(BlendMode::Src);
    for (image, bounds) in images {
        surface
            .canvas()
            .draw_image(&image, (0.0, bounds.top as f32), Some(&paint));
    }
    HITS.fetch_add(1, Ordering::Relaxed);
    true
}

pub(crate) fn capture(surface: &mut Surface, plan: Plan) {
    let Some(cache) = raster_image_cache() else {
        return;
    };
    for (key, bounds, byte_size) in plan.tiles {
        // Partial eviction does not invalidate immutable tiles that survived. Avoid
        // copying and replacing those again while filling the missing portion.
        if cache.contains_key(&key) {
            continue;
        }
        if let Some(image) = surface.image_snapshot_with_bounds(bounds) {
            cache.insert(key, RasterCacheValue { image, byte_size });
        }
    }
}

pub(crate) fn stats() -> (u64, u64, u64) {
    (
        HITS.load(Ordering::Relaxed),
        MISSES.load(Ordering::Relaxed),
        BYPASSES.load(Ordering::Relaxed),
    )
}

pub(crate) fn clear_stats() {
    HITS.store(0, Ordering::Relaxed);
    MISSES.store(0, Ordering::Relaxed);
    BYPASSES.store(0, Ordering::Relaxed);
}
