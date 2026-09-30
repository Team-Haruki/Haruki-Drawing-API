//! Immutable background tiles in their own bounded pool. Capture the pixels of an ordinary
//! full background draw; never render cropped/translated gradients (rounding would differ).
//! Keys carry resolved palette bytes and the exact caller-provided scatter, so the clock is
//! neither frozen nor approximated. Page content is never cached here.
//!
//! The tiles used to share the asset raster pool. The key must carry the canvas height (the
//! scatter is generated for it) and page heights follow their content, so production hit about
//! 1 background in 400 while each miss admitted up to a quarter of that pool and evicted resized
//! assets that did repeat. Quantising the height would change the scatter and every page's
//! pixels, so the tiles moved out instead: `HARUKI_SKIA_BACKGROUND_CACHE_MB` sizes this pool
//! and zero disables it, leaving the asset pool to assets.

use std::sync::{
    Arc, OnceLock,
    atomic::{AtomicU64, Ordering},
};

use moka::sync::Cache;
use skia_safe::{BlendMode, IRect, Paint, Surface};

use crate::{
    RasterCacheConfig, RasterCacheKey, RasterCacheValue, env_mb, ir::TriangleBgNode,
    raster_cache_config, triangle_palette, weighted_raster_pool,
};

const MAX_KEY_BYTES: usize = 64 * 1024;
const TILE_ROWS: i32 = 512;
const DEFAULT_BACKGROUND_CACHE_MB: u64 = 64;
static HITS: AtomicU64 = AtomicU64::new(0);
static MISSES: AtomicU64 = AtomicU64::new(0);
static BYPASSES: AtomicU64 = AtomicU64::new(0);
static EVICTIONS: AtomicU64 = AtomicU64::new(0);
static POOL_CONFIG: OnceLock<RasterCacheConfig> = OnceLock::new();
static POOL: OnceLock<Option<Cache<RasterCacheKey, RasterCacheValue>>> = OnceLock::new();

pub(crate) struct PoolSnapshot {
    pub(crate) max_bytes: u64,
    pub(crate) entries: u64,
    pub(crate) bytes: u64,
    pub(crate) evictions: u64,
}

/// Tiles keep the asset pool's per-entry limit; only the total budget is separate.
fn pool_config() -> &'static RasterCacheConfig {
    POOL_CONFIG.get_or_init(|| RasterCacheConfig {
        max_bytes: env_mb(
            "HARUKI_SKIA_BACKGROUND_CACHE_MB",
            DEFAULT_BACKGROUND_CACHE_MB,
        ),
        max_entry_bytes: raster_cache_config().max_entry_bytes,
        oversample: 1,
    })
}

fn pool() -> Option<&'static Cache<RasterCacheKey, RasterCacheValue>> {
    POOL.get_or_init(|| {
        let config = pool_config();
        (config.max_bytes > 0).then(|| weighted_raster_pool(config.max_bytes, &EVICTIONS))
    })
    .as_ref()
}

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
        pool_config(),
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
    // One long background must not flush every other background out of the pool.
    if total_bytes > config.max_bytes / 2 || total_bytes > available_scene_bytes as u64 {
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
    let Some(cache) = pool() else {
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
    let Some(cache) = pool() else {
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

pub(crate) fn pool_snapshot() -> PoolSnapshot {
    let (entries, bytes) = pool()
        .map(|cache| (cache.entry_count(), cache.weighted_size()))
        .unwrap_or_default();
    PoolSnapshot {
        max_bytes: pool_config().max_bytes,
        entries,
        bytes,
        evictions: EVICTIONS.load(Ordering::Relaxed),
    }
}

pub(crate) fn run_pending_tasks() {
    if let Some(cache) = pool() {
        cache.run_pending_tasks();
    }
}

/// Drop every tile and reset the counters (the runtime cache clear).
pub(crate) fn clear() {
    if let Some(cache) = pool() {
        cache.invalidate_all();
        cache.run_pending_tasks();
    }
    HITS.store(0, Ordering::Relaxed);
    MISSES.store(0, Ordering::Relaxed);
    BYPASSES.store(0, Ordering::Relaxed);
    EVICTIONS.store(0, Ordering::Relaxed);
}

#[cfg(test)]
mod tests {
    use skia_safe::{Color, surfaces};

    use super::*;

    fn background(tris: usize) -> TriangleBgNode {
        TriangleBgNode {
            hour: 12.0,
            time_color: true,
            main_hue: 0.0,
            tris: vec![[62.25, 120.5, 12.5, 50.0, 200.0, 220.0, 250.0, 143.0, 1.0]; tris],
        }
    }

    fn config(max_mb: u64, max_entry_mb: u64) -> RasterCacheConfig {
        RasterCacheConfig {
            max_bytes: max_mb * 1024 * 1024,
            max_entry_bytes: max_entry_mb * 1024 * 1024,
            oversample: 1,
        }
    }

    #[test]
    fn admission_is_bounded_by_half_of_the_background_pool() {
        let bg = background(1);
        let mut surface = surfaces::raster_n32_premul((180, 1100)).expect("surface");
        let plan = make_plan(&mut surface, &bg, 180.0, 1100.0, &config(4, 1), usize::MAX)
            .expect("a 0.8 MiB background fits half of 4 MiB");
        assert!(plan.retained_bytes <= 2 * 1024 * 1024);
        assert!(
            plan.tiles
                .iter()
                .all(|(_, _, bytes)| *bytes as u64 <= 1024 * 1024)
        );
        let rows: i32 = plan
            .tiles
            .iter()
            .map(|(_, bounds, _)| bounds.height())
            .sum();
        assert_eq!(rows, 1100);

        // 0.8 MiB is more than half of a 1 MiB pool, and a zero pool disables reuse.
        assert!(make_plan(&mut surface, &bg, 180.0, 1100.0, &config(1, 1), usize::MAX).is_none());
        assert!(make_plan(&mut surface, &bg, 180.0, 1100.0, &config(0, 1), usize::MAX).is_none());
        // The scene's remaining budget still bounds a hit.
        assert!(make_plan(&mut surface, &bg, 180.0, 1100.0, &config(4, 1), 1024).is_none());
    }

    #[test]
    fn captured_tiles_never_enter_the_asset_raster_pool() {
        if pool_config().max_bytes == 0 {
            return;
        }
        let mut bg = background(1);
        // A scatter no other test uses keeps this key private to this test.
        bg.tris[0][0] = 17.125;
        let mut surface = surfaces::raster_n32_premul((64, 96)).expect("surface");
        surface.canvas().clear(Color::from_argb(255, 10, 20, 30));
        let plan = make_plan(&mut surface, &bg, 64.0, 96.0, pool_config(), usize::MAX)
            .expect("small background admitted");
        let keys: Vec<RasterCacheKey> = plan.tiles.iter().map(|(key, _, _)| key.clone()).collect();
        capture(&mut surface, plan);
        let tiles = pool().expect("background pool enabled");
        assert!(keys.iter().all(|key| tiles.contains_key(key)));
        if let Some(assets) = crate::raster_image_cache() {
            assert!(keys.iter().all(|key| !assets.contains_key(key)));
        }
    }
}
