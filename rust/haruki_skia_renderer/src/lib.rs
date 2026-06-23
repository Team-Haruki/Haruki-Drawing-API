use std::collections::HashMap;
use std::env;
use std::f32::consts::PI;
use std::fs;
use std::path::{Component, Path, PathBuf};
use std::sync::{Mutex, OnceLock};
use std::time::{Duration, Instant};

use pyo3::prelude::*;
use pyo3::types::{PyBytes, PyDict};
use serde::Deserialize;
use skia_safe::{
    BlurStyle, Canvas, ClipOp, Color, Data, EncodedImageFormat, Font, FontMgr, Image, MaskFilter,
    Paint, PaintStyle, Path as SkPath, Point, RRect, Rect, SamplingOptions, Surface, TextBlob,
    TileMode, Typeface, gradient, image_filters, surfaces,
};

const BG_PADDING: f32 = 20.0;
const PANEL_WIDTH: f32 = 996.0;
const GRID_PADDING: f32 = 16.0;
const GRID_COLS: usize = 3;
const CARD_W: f32 = 316.0;
const CARD_H: f32 = 190.0;
const CARD_SEP: f32 = 8.0;
const THUMB: f32 = 100.0;
const TITLE_H: f32 = 50.0;
const TITLE_SEP: f32 = 16.0;
const BOX_GROUP_SEP: f32 = 4.0;

static FONT_CACHE: OnceLock<Mutex<HashMap<String, FontSet>>> = OnceLock::new();

#[derive(Debug, Deserialize)]
struct CardListIr {
    version: u32,
    assets_base_dir: String,
    export_format: String,
    jpg_quality: i32,
    #[serde(default)]
    background_hour: Option<f32>,
    now_ms: i64,
    title: Option<String>,
    background_img_path: Option<String>,
    watermark: WatermarkIr,
    fonts: FontsIr,
    icons: IconsIr,
    cards: Vec<CardIr>,
}

#[derive(Debug, Deserialize)]
struct CardBoxIr {
    version: u32,
    assets_base_dir: String,
    export_format: String,
    jpg_quality: i32,
    #[serde(default)]
    background_hour: Option<f32>,
    title: Option<String>,
    show_id: bool,
    show_box: bool,
    background_img_path: Option<String>,
    watermark: WatermarkIr,
    fonts: FontsIr,
    icons: IconsIr,
    #[serde(default)]
    character_icon_paths: HashMap<String, String>,
    #[serde(default)]
    character_color_codes: HashMap<String, String>,
    cards: Vec<BoxCardIr>,
}

#[derive(Debug, Deserialize)]
struct WatermarkIr {
    enabled: bool,
    text: String,
}

#[derive(Debug, Deserialize)]
struct FontsIr {
    dir: String,
    default: String,
    bold: String,
}

#[derive(Debug, Deserialize)]
struct IconsIr {
    term_limited: Option<String>,
    fes_limited: Option<String>,
    #[serde(default)]
    #[serde(rename = "skill")]
    _skill: Vec<String>,
}

#[derive(Debug, Deserialize)]
struct CardIr {
    card_id: i64,
    prefix: String,
    release_at: i64,
    supply_type: String,
    skill_type: Option<String>,
    skill_icon_path: Option<String>,
    thumbnail_info: Vec<ThumbnailIr>,
}

#[derive(Debug, Deserialize)]
struct ThumbnailIr {
    #[serde(rename = "card_id")]
    _card_id: i64,
    card_thumbnail_path: String,
    rare: String,
    frame_img_path: Option<String>,
    attr_img_path: Option<String>,
    rare_img_path: String,
    train_rank: Option<i64>,
    train_rank_img_path: Option<String>,
    level: Option<i64>,
    custom_text: Option<String>,
    is_pcard: bool,
}

#[derive(Debug, Deserialize)]
struct BoxCardIr {
    card_id: i64,
    character_id: Option<i64>,
    release_at: i64,
    supply_type: String,
    rare: String,
    is_after_training: bool,
    has_card: bool,
    thumbnail_info: Vec<ThumbnailIr>,
}

#[derive(Clone)]
struct FontSet {
    regular: Typeface,
    bold: Typeface,
}

#[pyfunction]
fn render_card_list(py: Python<'_>, ir_json: &[u8]) -> PyResult<Py<PyDict>> {
    let ir: CardListIr = serde_json::from_slice(ir_json).map_err(|err| {
        pyo3::exceptions::PyValueError::new_err(format!("invalid card list IR: {err}"))
    })?;
    let rendered = py.detach(|| render_card_list_inner(&ir)).map_err(|err| {
        pyo3::exceptions::PyRuntimeError::new_err(format!("card list render failed: {err}"))
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

#[pyfunction]
fn render_card_box(py: Python<'_>, ir_json: &[u8]) -> PyResult<Py<PyDict>> {
    let ir: CardBoxIr = serde_json::from_slice(ir_json).map_err(|err| {
        pyo3::exceptions::PyValueError::new_err(format!("invalid card box IR: {err}"))
    })?;
    let rendered = py.detach(|| render_card_box_inner(&ir)).map_err(|err| {
        pyo3::exceptions::PyRuntimeError::new_err(format!("card box render failed: {err}"))
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
    m.add_function(wrap_pyfunction!(render_card_list, m)?)?;
    m.add_function(wrap_pyfunction!(render_card_box, m)?)?;
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

#[derive(Default)]
struct RenderProfile {
    enabled: bool,
    total: Duration,
    load_fonts: Duration,
    create_surface: Duration,
    draw_background: Duration,
    draw_title: Duration,
    draw_grid_panel: Duration,
    load_icons: Duration,
    draw_cards: Duration,
    draw_watermark: Duration,
    compose_thumbnails: Duration,
    thumbnail_snapshots: Duration,
    load_images: Duration,
    encode: Duration,
    thumbnail_count: usize,
    image_cache_hits: usize,
    image_cache_misses: usize,
}

impl RenderProfile {
    fn new() -> Self {
        Self {
            enabled: env::var_os("HARUKI_SKIA_PROFILE").is_some(),
            ..Self::default()
        }
    }

    fn sec(duration: Duration) -> f64 {
        duration.as_secs_f64()
    }

    fn emit(&self, endpoint: &str, cards: usize, image_cache_len: usize) {
        if !self.enabled {
            return;
        }
        eprintln!(
            concat!(
                "haruki_skia_profile ",
                "{{\"endpoint\":\"{}\",\"cards\":{},\"image_cache_len\":{},",
                "\"total\":{:.6},\"load_fonts\":{:.6},\"create_surface\":{:.6},",
                "\"draw_background\":{:.6},\"draw_title\":{:.6},\"draw_grid_panel\":{:.6},",
                "\"load_icons\":{:.6},\"draw_cards\":{:.6},\"draw_watermark\":{:.6},",
                "\"compose_thumbnails\":{:.6},\"thumbnail_snapshots\":{:.6},",
                "\"thumbnail_count\":{},\"load_images\":{:.6},",
                "\"image_cache_hits\":{},\"image_cache_misses\":{},\"encode\":{:.6}}}"
            ),
            endpoint,
            cards,
            image_cache_len,
            Self::sec(self.total),
            Self::sec(self.load_fonts),
            Self::sec(self.create_surface),
            Self::sec(self.draw_background),
            Self::sec(self.draw_title),
            Self::sec(self.draw_grid_panel),
            Self::sec(self.load_icons),
            Self::sec(self.draw_cards),
            Self::sec(self.draw_watermark),
            Self::sec(self.compose_thumbnails),
            Self::sec(self.thumbnail_snapshots),
            self.thumbnail_count,
            Self::sec(self.load_images),
            self.image_cache_hits,
            self.image_cache_misses,
            Self::sec(self.encode),
        );
    }
}

fn render_card_list_inner(ir: &CardListIr) -> Result<RenderedImage, String> {
    let total_started = Instant::now();
    let mut profile = RenderProfile::new();
    if ir.version != 1 {
        return Err(format!("unsupported IR version {}", ir.version));
    }
    let assets_base = PathBuf::from(&ir.assets_base_dir);
    let started = Instant::now();
    let fonts = load_fonts(&ir.fonts);
    profile.load_fonts += started.elapsed();
    let rows = ir.cards.len().max(1).div_ceil(GRID_COLS) as f32;
    let title_h = if ir.title.as_ref().is_some_and(|s| !s.is_empty()) {
        TITLE_H + TITLE_SEP
    } else {
        0.0
    };
    let grid_h = GRID_PADDING * 2.0 + rows * CARD_H + (rows - 1.0).max(0.0) * CARD_SEP;
    let width = (PANEL_WIDTH + BG_PADDING * 2.0).ceil() as i32;
    let height = (BG_PADDING * 2.0 + title_h + grid_h).ceil() as i32;
    let started = Instant::now();
    let mut surface = surfaces::raster_n32_premul((width, height))
        .ok_or_else(|| "failed to create raster surface".to_string())?;
    profile.create_surface += started.elapsed();

    let started = Instant::now();
    draw_background(
        surface.canvas(),
        &assets_base,
        ir.background_img_path.as_deref(),
        ir.background_hour.unwrap_or(15.0),
        width as f32,
        height as f32,
    );
    profile.draw_background += started.elapsed();
    let background_snapshot = surface.image_snapshot();

    let mut y = BG_PADDING;
    if let Some(title) = &ir.title
        && !title.is_empty()
    {
        let started = Instant::now();
        draw_title(
            surface.canvas(),
            &background_snapshot,
            title,
            &fonts,
            BG_PADDING,
            y,
        );
        profile.draw_title += started.elapsed();
        y += TITLE_H + TITLE_SEP;
    }
    let title_snapshot = surface.image_snapshot();

    let grid_rect = Rect::from_xywh(BG_PADDING, y, PANEL_WIDTH, grid_h);
    let started = Instant::now();
    draw_blur_glass_rect(
        surface.canvas(),
        &title_snapshot,
        grid_rect,
        12.0,
        Color::from_argb(80, 255, 255, 255),
        0.26,
    );
    profile.draw_grid_panel += started.elapsed();
    let card_backdrop = surface.image_snapshot();

    let mut image_cache = HashMap::new();
    let started = Instant::now();
    let term_icon = load_optional_cached_image(
        &assets_base,
        ir.icons.term_limited.as_deref(),
        &mut image_cache,
        &mut profile,
    );
    let fes_icon = load_optional_cached_image(
        &assets_base,
        ir.icons.fes_limited.as_deref(),
        &mut image_cache,
        &mut profile,
    );
    profile.load_icons += started.elapsed();

    let started = Instant::now();
    for (idx, card) in ir.cards.iter().enumerate() {
        let row = idx / GRID_COLS;
        let col = idx % GRID_COLS;
        let x = BG_PADDING + GRID_PADDING + col as f32 * (CARD_W + CARD_SEP);
        let cy = y + GRID_PADDING + row as f32 * (CARD_H + CARD_SEP);
        draw_card(
            surface.canvas(),
            &card_backdrop,
            card,
            &assets_base,
            &fonts,
            term_icon.as_ref(),
            fes_icon.as_ref(),
            &mut image_cache,
            &mut profile,
            x,
            cy,
            ir.now_ms,
        );
    }
    profile.draw_cards += started.elapsed();

    if ir.watermark.enabled {
        let started = Instant::now();
        let watermark = if ir.watermark.text.is_empty() {
            "Haruki Drawing API"
        } else {
            ir.watermark.text.as_str()
        };
        draw_text(
            surface.canvas(),
            watermark,
            &fonts.regular,
            12.0,
            Point::new(width as f32 - 150.0, height as f32 - 10.0),
            Color::from_argb(120, 0, 0, 0),
        );
        profile.draw_watermark += started.elapsed();
    }

    let started = Instant::now();
    let rendered = encode_surface(surface, &ir.export_format, ir.jpg_quality)?;
    profile.encode += started.elapsed();
    profile.total = total_started.elapsed();
    profile.emit("card_list", ir.cards.len(), image_cache.len());
    Ok(rendered)
}

fn render_card_box_inner(ir: &CardBoxIr) -> Result<RenderedImage, String> {
    let total_started = Instant::now();
    let mut profile = RenderProfile::new();
    if ir.version != 1 {
        return Err(format!("unsupported IR version {}", ir.version));
    }
    let assets_base = PathBuf::from(&ir.assets_base_dir);
    let started = Instant::now();
    let fonts = load_fonts(&ir.fonts);
    profile.load_fonts += started.elapsed();

    let groups = build_box_groups(ir);
    let layout = compute_box_layout(&groups, ir.show_id);
    let title_h = if ir.title.as_ref().is_some_and(|s| !s.is_empty()) {
        TITLE_H + TITLE_SEP
    } else {
        0.0
    };
    let panel_h = layout.panel_height;
    let width = (layout.panel_width + BG_PADDING * 2.0).ceil() as i32;
    let height = (BG_PADDING * 2.0 + title_h + panel_h).ceil() as i32;

    let started = Instant::now();
    let mut surface = surfaces::raster_n32_premul((width, height))
        .ok_or_else(|| "failed to create raster surface".to_string())?;
    profile.create_surface += started.elapsed();

    let started = Instant::now();
    draw_background(
        surface.canvas(),
        &assets_base,
        ir.background_img_path.as_deref(),
        ir.background_hour.unwrap_or(15.0),
        width as f32,
        height as f32,
    );
    profile.draw_background += started.elapsed();
    let background_snapshot = surface.image_snapshot();

    let mut y = BG_PADDING;
    if let Some(title) = &ir.title
        && !title.is_empty()
    {
        let started = Instant::now();
        draw_notice_title(
            surface.canvas(),
            &background_snapshot,
            title,
            &fonts,
            BG_PADDING,
            y,
            layout.panel_width,
        );
        profile.draw_title += started.elapsed();
        y += TITLE_H + TITLE_SEP;
    }
    let title_snapshot = surface.image_snapshot();
    let panel_rect = Rect::from_xywh(BG_PADDING, y, layout.panel_width, panel_h);
    let started = Instant::now();
    draw_blur_glass_rect(
        surface.canvas(),
        &title_snapshot,
        panel_rect,
        12.0,
        Color::from_argb(80, 255, 255, 255),
        0.26,
    );
    profile.draw_grid_panel += started.elapsed();

    let mut image_cache = HashMap::new();
    let started = Instant::now();
    let term_icon = load_optional_cached_image(
        &assets_base,
        ir.icons.term_limited.as_deref(),
        &mut image_cache,
        &mut profile,
    );
    let fes_icon = load_optional_cached_image(
        &assets_base,
        ir.icons.fes_limited.as_deref(),
        &mut image_cache,
        &mut profile,
    );
    profile.load_icons += started.elapsed();

    let started = Instant::now();
    draw_box_groups(
        surface.canvas(),
        ir,
        &groups,
        &layout,
        &assets_base,
        &fonts,
        term_icon.as_ref(),
        fes_icon.as_ref(),
        &mut image_cache,
        &mut profile,
        BG_PADDING,
        y,
    );
    profile.draw_cards += started.elapsed();

    if ir.watermark.enabled {
        let started = Instant::now();
        let watermark = if ir.watermark.text.is_empty() {
            "Haruki Drawing API"
        } else {
            ir.watermark.text.as_str()
        };
        draw_text(
            surface.canvas(),
            watermark,
            &fonts.regular,
            12.0,
            Point::new(width as f32 - 150.0, height as f32 - 10.0),
            Color::from_argb(120, 0, 0, 0),
        );
        profile.draw_watermark += started.elapsed();
    }

    let started = Instant::now();
    let rendered = encode_surface(surface, &ir.export_format, ir.jpg_quality)?;
    profile.encode += started.elapsed();
    profile.total = total_started.elapsed();
    profile.emit("card_box", ir.cards.len(), image_cache.len());
    Ok(rendered)
}

struct BoxGroup<'a> {
    chara_id: i64,
    cards: Vec<&'a BoxCardIr>,
}

struct BoxLayout {
    best_height: usize,
    thumb_size: f32,
    sep: f32,
    panel_width: f32,
    panel_height: f32,
    group_widths: Vec<f32>,
}

fn build_box_groups(ir: &CardBoxIr) -> Vec<BoxGroup<'_>> {
    let mut grouped: HashMap<i64, Vec<&BoxCardIr>> = HashMap::new();
    for card in &ir.cards {
        if ir.show_box && !card.has_card {
            continue;
        }
        if selected_box_thumbnail(card).is_none() {
            continue;
        }
        if let Some(chara_id) = card.character_id {
            grouped.entry(chara_id).or_default().push(card);
        }
    }
    let mut groups = grouped
        .into_iter()
        .map(|(chara_id, mut cards)| {
            cards.sort_by(|a, b| {
                a.rare
                    .cmp(&b.rare)
                    .then_with(|| a.release_at.cmp(&b.release_at))
                    .then_with(|| a.card_id.cmp(&b.card_id))
            });
            BoxGroup { chara_id, cards }
        })
        .collect::<Vec<_>>();
    groups.sort_by_key(|group| group.chara_id);
    groups
}

fn selected_box_thumbnail(card: &BoxCardIr) -> Option<&ThumbnailIr> {
    if card.thumbnail_info.is_empty() {
        return None;
    }
    if card.thumbnail_info.len() == 1 {
        return card.thumbnail_info.first();
    }
    if card.is_after_training {
        return card.thumbnail_info.get(1);
    }
    card.thumbnail_info.first()
}

fn compute_box_layout(groups: &[BoxGroup<'_>], show_id: bool) -> BoxLayout {
    let max_card_num = groups
        .iter()
        .map(|group| group.cards.len())
        .max()
        .unwrap_or(0);
    let mut best_height = 1usize;
    let mut best_value = f32::INFINITY;
    for candidate in 1..=max_card_num.max(1) {
        let mut max_height = 0usize;
        let mut total_width = 0usize;
        let mut total = 0usize;
        let mut space = 0usize;
        for group in groups {
            max_height = max_height.max(group.cards.len().min(candidate));
        }
        for group in groups {
            let width = group.cards.len().div_ceil(candidate).max(1);
            total_width += width;
            total += max_height * width;
            space += max_height * width - group.cards.len();
        }
        let value = if total_width > 9 {
            (total_width as f32).max(max_height as f32 * 0.5)
        } else {
            (total_width as f32 * 0.5).max(max_height as f32)
        };
        let density = if total > space {
            total as f32 / (total - space) as f32
        } else {
            1.0
        };
        let value = value * density;
        if value < best_value {
            best_height = candidate;
            best_value = value;
        }
    }

    let total_width_cols = groups
        .iter()
        .map(|group| group.cards.len().div_ceil(best_height).max(1))
        .sum::<usize>();
    let area = total_width_cols * (best_height + 4);
    let start_area = 9.0 * 5.0;
    let end_area = 26.0 * 50.0;
    let interp = ((area as f32 - start_area) / (end_area - start_area)).clamp(0.0, 1.0);
    let sep = (8.0 + (4.0 - 8.0) * interp).round();
    let thumb_size = (100.0 + (48.0 - 100.0) * interp).round();
    let item_height = thumb_size + if show_id { 16.0 } else { 0.0 };

    let group_widths = groups
        .iter()
        .map(|group| {
            let cols = group.cards.len().div_ceil(best_height).max(1) as f32;
            thumb_size * cols + sep * (cols - 1.0).max(0.0)
        })
        .collect::<Vec<_>>();
    let content_width = if group_widths.is_empty() {
        GRID_PADDING * 2.0
    } else {
        GRID_PADDING * 2.0
            + group_widths.iter().sum::<f32>()
            + BOX_GROUP_SEP * (group_widths.len().saturating_sub(1)) as f32
    };
    let max_group_height = groups
        .iter()
        .map(|group| {
            let rows = group.cards.len().min(best_height).max(1) as f32;
            thumb_size
                + BOX_GROUP_SEP
                + sep
                + BOX_GROUP_SEP
                + rows * item_height
                + sep * (rows - 1.0).max(0.0)
        })
        .fold(thumb_size, f32::max);

    BoxLayout {
        best_height,
        thumb_size,
        sep,
        panel_width: content_width.max(520.0),
        panel_height: GRID_PADDING * 2.0 + max_group_height,
        group_widths,
    }
}

#[allow(clippy::too_many_arguments)]
fn draw_box_groups(
    canvas: &Canvas,
    ir: &CardBoxIr,
    groups: &[BoxGroup<'_>],
    layout: &BoxLayout,
    base: &Path,
    fonts: &FontSet,
    term_icon: Option<&Image>,
    fes_icon: Option<&Image>,
    image_cache: &mut HashMap<String, Image>,
    profile: &mut RenderProfile,
    panel_x: f32,
    panel_y: f32,
) {
    let mut x = panel_x + GRID_PADDING;
    let y = panel_y + GRID_PADDING;
    for (idx, group) in groups.iter().enumerate() {
        let group_w = layout
            .group_widths
            .get(idx)
            .copied()
            .unwrap_or(layout.thumb_size);
        draw_character_header(
            canvas,
            ir,
            group.chara_id,
            base,
            image_cache,
            profile,
            x,
            y,
            layout.thumb_size,
            group_w,
            layout.sep,
        );
        let item_h = layout.thumb_size + if ir.show_id { 16.0 } else { 0.0 };
        let grid_y = y + layout.thumb_size + BOX_GROUP_SEP + layout.sep + BOX_GROUP_SEP;
        for (card_idx, card) in group.cards.iter().enumerate() {
            let row = card_idx % layout.best_height;
            let col = card_idx / layout.best_height;
            let cx = x + col as f32 * (layout.thumb_size + layout.sep);
            let cy = grid_y + row as f32 * (item_h + layout.sep);
            draw_box_card(
                canvas,
                card,
                base,
                fonts,
                term_icon,
                fes_icon,
                image_cache,
                profile,
                cx,
                cy,
                layout.thumb_size,
                ir.show_id,
            );
        }
        x += group_w + BOX_GROUP_SEP;
    }
}

#[allow(clippy::too_many_arguments)]
fn draw_character_header(
    canvas: &Canvas,
    ir: &CardBoxIr,
    chara_id: i64,
    base: &Path,
    image_cache: &mut HashMap<String, Image>,
    profile: &mut RenderProfile,
    x: f32,
    y: f32,
    size: f32,
    width: f32,
    sep: f32,
) {
    let key = chara_id.to_string();
    if let Some(path) = ir.character_icon_paths.get(&key)
        && let Ok(icon) = load_cached_image(base, path, image_cache, profile)
    {
        draw_fit_image(canvas, &icon, Rect::from_xywh(x, y, size, size), 1.0);
    } else {
        let color = parse_color(
            ir.character_color_codes
                .get(&key)
                .map(String::as_str)
                .unwrap_or("#cccccc"),
        );
        let mut paint = Paint::default();
        paint.set_anti_alias(true);
        paint.set_color(Color::from_argb(210, color.0, color.1, color.2));
        canvas.draw_rrect(
            RRect::new_rect_xy(Rect::from_xywh(x, y, size, size), 8.0, 8.0),
            &paint,
        );
    }

    let color = parse_color(
        ir.character_color_codes
            .get(&key)
            .map(String::as_str)
            .unwrap_or("#cccccc"),
    );
    let mut paint = Paint::default();
    paint.set_anti_alias(true);
    paint.set_color(Color::from_argb(255, color.0, color.1, color.2));
    canvas.draw_rect(
        Rect::from_xywh(x, y + size + BOX_GROUP_SEP, width, sep),
        &paint,
    );
}

#[allow(clippy::too_many_arguments)]
fn draw_box_card(
    canvas: &Canvas,
    card: &BoxCardIr,
    base: &Path,
    fonts: &FontSet,
    term_icon: Option<&Image>,
    fes_icon: Option<&Image>,
    image_cache: &mut HashMap<String, Image>,
    profile: &mut RenderProfile,
    x: f32,
    y: f32,
    size: f32,
    show_id: bool,
) {
    if let Some(thumb) = selected_box_thumbnail(card)
        && let Ok(image) = compose_thumbnail(base, fonts, thumb, image_cache, profile)
    {
        draw_fit_image(canvas, &image, Rect::from_xywh(x, y, size, size), 1.0);
    }
    let icon = match card.supply_type.as_str() {
        "期间限定" | "WL限定" | "联动限定" => term_icon,
        "Fes限定" | "CFes限定" | "BFes限定" => fes_icon,
        _ => None,
    };
    if let Some(icon) = icon {
        let width = size * 0.75;
        draw_width_image(canvas, icon, Point::new(x + size - width, y), width, 1.0);
    }
    if show_id {
        draw_center_text(
            canvas,
            &card.card_id.to_string(),
            &fonts.regular,
            12.0,
            Rect::from_xywh(x, y + size, size, 16.0),
            Color::BLACK,
        );
    }
}

fn draw_background(
    canvas: &Canvas,
    base: &Path,
    background_img_path: Option<&str>,
    background_hour: f32,
    width: f32,
    height: f32,
) {
    if let Some(path) = background_img_path
        && let Ok(image) = load_image(base, path)
    {
        draw_cover_image(
            canvas,
            &image,
            Rect::from_xywh(0.0, 0.0, width, height),
            1.0,
        );
        return;
    }
    draw_sekai_triangle_background(canvas, width, height, background_hour);
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

fn draw_title(canvas: &Canvas, backdrop: &Image, title: &str, fonts: &FontSet, x: f32, y: f32) {
    draw_notice_title(canvas, backdrop, title, fonts, x, y, PANEL_WIDTH);
}

fn draw_notice_title(
    canvas: &Canvas,
    backdrop: &Image,
    title: &str,
    fonts: &FontSet,
    x: f32,
    y: f32,
    width: f32,
) {
    let rect = Rect::from_xywh(x, y, width, TITLE_H);
    draw_blur_glass_rect(
        canvas,
        backdrop,
        rect,
        10.0,
        Color::from_argb(220, 255, 246, 219),
        0.24,
    );
    draw_text(
        canvas,
        "提示",
        &fonts.bold,
        22.0,
        Point::new(x + 14.0, y + 34.0),
        Color::from_argb(255, 166, 90, 0),
    );
    draw_text(
        canvas,
        title,
        &fonts.regular,
        22.0,
        Point::new(x + 80.0, y + 34.0),
        Color::from_argb(255, 98, 68, 0),
    );
}

#[allow(clippy::too_many_arguments)]
fn draw_card(
    canvas: &Canvas,
    backdrop: &Image,
    card: &CardIr,
    base: &Path,
    fonts: &FontSet,
    term_icon: Option<&Image>,
    fes_icon: Option<&Image>,
    image_cache: &mut HashMap<String, Image>,
    profile: &mut RenderProfile,
    x: f32,
    y: f32,
    now_ms: i64,
) {
    let limited = !is_non_limited(&card.supply_type);
    let fill = if limited {
        Color::from_argb(200, 255, 250, 220)
    } else {
        Color::from_argb(80, 255, 255, 255)
    };
    draw_blur_glass_rect(
        canvas,
        backdrop,
        Rect::from_xywh(x, y, CARD_W, CARD_H),
        10.0,
        fill,
        0.30,
    );

    if card.release_at > now_ms {
        draw_text(
            canvas,
            "未上线",
            &fonts.bold,
            20.0,
            Point::new(x + 4.0, y + CARD_H - 8.0),
            Color::from_argb(255, 200, 0, 0),
        );
    }

    if card.skill_type.is_some()
        && let Some(path) = card.skill_icon_path.as_deref()
        && let Ok(icon) = load_cached_image(base, path, image_cache, profile)
    {
        draw_fit_image(
            canvas,
            &icon,
            Rect::from_xywh(x + CARD_W - 40.0, y + CARD_H - 40.0, 32.0, 32.0),
            1.0,
        );
    }

    let thumbs = card.thumbnail_info.iter().take(2).collect::<Vec<_>>();
    let total_w = thumbs.len() as f32 * THUMB + (thumbs.len().saturating_sub(1)) as f32 * 16.0;
    let mut tx = x + (CARD_W - total_w) * 0.5;
    for thumb in thumbs {
        if let Ok(image) = compose_thumbnail(base, fonts, thumb, image_cache, profile) {
            draw_shadow(
                canvas,
                Rect::from_xywh(tx, y + 16.0, THUMB, THUMB),
                8.0,
                0.35,
            );
            draw_fit_image(
                canvas,
                &image,
                Rect::from_xywh(tx, y + 16.0, THUMB, THUMB),
                1.0,
            );
            let icon = match card.supply_type.as_str() {
                "期间限定" | "WL限定" | "联动限定" => term_icon,
                "Fes限定" | "CFes限定" | "BFes限定" => fes_icon,
                _ => None,
            };
            if let Some(icon) = icon {
                draw_width_image(
                    canvas,
                    icon,
                    Point::new(tx + THUMB - 75.0, y + 16.0),
                    75.0,
                    1.0,
                );
            }
        }
        tx += THUMB + 16.0;
    }

    draw_center_text(
        canvas,
        &card.prefix,
        &fonts.bold,
        20.0,
        Rect::from_xywh(x, y + 129.0, CARD_W, 24.0),
        Color::BLACK,
    );
    let mut id_text = format!("ID:{}", card.card_id);
    if limited {
        id_text.push_str(&format!("【{}】", card.supply_type));
    }
    draw_center_text(
        canvas,
        &id_text,
        &fonts.regular,
        20.0,
        Rect::from_xywh(x, y + 158.0, CARD_W, 24.0),
        Color::BLACK,
    );
}

fn compose_thumbnail(
    base: &Path,
    fonts: &FontSet,
    thumb: &ThumbnailIr,
    image_cache: &mut HashMap<String, Image>,
    profile: &mut RenderProfile,
) -> Result<Image, String> {
    let thumbnail_started = Instant::now();
    profile.thumbnail_count += 1;
    let width = 100;
    let height = 100;
    let mut surface = surfaces::raster_n32_premul((width, height))
        .ok_or_else(|| "failed to create thumbnail surface".to_string())?;
    let canvas = surface.canvas();
    let card = load_cached_image(base, &thumb.card_thumbnail_path, image_cache, profile)?;
    draw_cover_image(
        canvas,
        &card,
        Rect::from_xywh(0.0, 0.0, width as f32, height as f32),
        1.0,
    );

    if thumb.is_pcard {
        let mut paint = Paint::default();
        paint.set_color(Color::from_argb(255, 70, 70, 100));
        canvas.draw_rect(Rect::from_xywh(0.0, 76.0, 100.0, 24.0), &paint);
        let text = thumb
            .custom_text
            .clone()
            .unwrap_or_else(|| format!("Lv.{}", thumb.level.unwrap_or_default()));
        draw_text(
            canvas,
            &text,
            &fonts.bold,
            20.0,
            Point::new(6.0, 92.0),
            Color::WHITE,
        );
    }

    if let Some(path) = thumb.frame_img_path.as_deref()
        && let Ok(frame) = load_cached_image(base, path, image_cache, profile)
    {
        draw_fit_image(canvas, &frame, Rect::from_xywh(0.0, 0.0, 100.0, 100.0), 1.0);
    }
    if let Some(path) = thumb.attr_img_path.as_deref()
        && let Ok(attr) = load_cached_image(base, path, image_cache, profile)
    {
        draw_fit_image(canvas, &attr, Rect::from_xywh(1.0, 0.0, 22.0, 25.0), 1.0);
    }
    if thumb.is_pcard
        && thumb.train_rank.unwrap_or_default() > 0
        && let Some(path) = thumb.train_rank_img_path.as_deref()
        && let Ok(rank) = load_cached_image(base, path, image_cache, profile)
    {
        draw_fit_image(canvas, &rank, Rect::from_xywh(65.0, 65.0, 35.0, 35.0), 1.0);
    }

    let rare = load_cached_image(base, &thumb.rare_img_path, image_cache, profile)?;
    let rare_num = rare_count(&thumb.rare);
    let rare_w = 17.0;
    let rare_h = 17.0;
    let voffset = if thumb.is_pcard { 24.0 } else { 6.0 };
    for i in 0..rare_num {
        draw_fit_image(
            canvas,
            &rare,
            Rect::from_xywh(
                6.0 + rare_w * i as f32,
                100.0 - rare_h - voffset,
                rare_w,
                rare_h,
            ),
            1.0,
        );
    }

    let snapshot_started = Instant::now();
    let image = surface.image_snapshot();
    profile.thumbnail_snapshots += snapshot_started.elapsed();
    profile.compose_thumbnails += thumbnail_started.elapsed();
    Ok(image)
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
                SamplingOptions::default(),
                &copy_paint,
            );

            let blurred =
                if let Some(mut blur_surface) = surfaces::raster_n32_premul((temp_w, temp_h)) {
                    let temp_image = temp_surface.image_snapshot();
                    let mut blur_paint = Paint::default();
                    blur_paint.set_anti_alias(true);
                    blur_paint.set_image_filter(image_filters::blur(
                        (5.0 / downsample, 5.0 / downsample),
                        TileMode::Clamp,
                        None,
                        None,
                    ));
                    blur_surface.canvas().draw_image_rect_with_sampling_options(
                        &temp_image,
                        None,
                        temp_dst,
                        SamplingOptions::default(),
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
                SamplingOptions::default(),
                &paste_paint,
            );
            canvas.restore();
        }
    }

    draw_glass_overlay(canvas, rect, radius, fill);
}

fn draw_glass_shadow(canvas: &Canvas, rect: Rect, radius: f32, shadow_alpha: f32) {
    let mut paint = Paint::default();
    paint.set_anti_alias(true);
    paint.set_style(PaintStyle::Fill);

    for (dx, dy, sigma, alpha) in [
        (0.0, 1.5, 1.4, shadow_alpha * 0.26),
        (0.0, 4.5, 3.8, shadow_alpha * 0.23),
        (0.0, 9.0, 7.5, shadow_alpha * 0.13),
    ] {
        paint.set_color(Color::from_argb(
            (alpha * 255.0).clamp(0.0, 255.0) as u8,
            96,
            78,
            122,
        ));
        paint.set_mask_filter(MaskFilter::blur(BlurStyle::Normal, sigma, true));
        canvas.draw_rrect(
            RRect::new_rect_xy(rect.with_offset((dx, dy)), radius, radius),
            &paint,
        );
    }
    paint.set_mask_filter(None);
}

fn draw_glass_overlay(canvas: &Canvas, rect: Rect, radius: f32, fill: Color) {
    let mut paint = Paint::default();
    paint.set_anti_alias(true);
    paint.set_color(fill);
    paint.set_style(PaintStyle::Fill);
    canvas.draw_rrect(RRect::new_rect_xy(rect, radius, radius), &paint);

    // Pillow's blurglass adds soft edge highlights on top of the blurred layer.
    paint.set_style(PaintStyle::Stroke);
    paint.set_stroke_width(1.0);
    paint.set_color(Color::from_argb(34, 104, 86, 132));
    canvas.draw_rrect(RRect::new_rect_xy(rect, radius, radius), &paint);

    paint.set_stroke_width(1.4);
    paint.set_color(Color::from_argb(96, 255, 255, 255));
    canvas.draw_rrect(
        RRect::new_rect_xy(rect.with_inset((0.8, 0.8)), radius - 0.8, radius - 0.8),
        &paint,
    );

    paint.set_stroke_width(2.0);
    paint.set_color(Color::from_argb(24, 255, 255, 255));
    canvas.draw_rrect(
        RRect::new_rect_xy(rect.with_inset((2.0, 2.0)), radius - 2.0, radius - 2.0),
        &paint,
    );
}

fn draw_shadow(canvas: &Canvas, rect: Rect, radius: f32, alpha: f32) {
    let mut paint = Paint::default();
    paint.set_anti_alias(true);
    paint.set_color(Color::from_argb((alpha * 255.0) as u8, 0, 0, 0));
    paint.set_mask_filter(MaskFilter::blur(BlurStyle::Normal, 2.5, true));
    canvas.draw_rrect(
        RRect::new_rect_xy(rect.with_offset((2.0, 4.0)), radius, radius),
        &paint,
    );
}

fn draw_text(
    canvas: &Canvas,
    text: &str,
    typeface: &Typeface,
    size: f32,
    point: Point,
    color: Color,
) {
    let font = Font::from_typeface(typeface.clone(), size);
    if let Some(blob) = TextBlob::new(text, &font) {
        let mut paint = Paint::default();
        paint.set_anti_alias(true);
        paint.set_color(color);
        canvas.draw_text_blob(&blob, point, &paint);
    }
}

fn draw_center_text(
    canvas: &Canvas,
    text: &str,
    typeface: &Typeface,
    size: f32,
    rect: Rect,
    color: Color,
) {
    let font = Font::from_typeface(typeface.clone(), size);
    if let Some(blob) = TextBlob::new(text, &font) {
        let bounds = blob.bounds();
        let x = rect.left + (rect.width() - bounds.width()) * 0.5;
        let y = rect.top + rect.height() - 5.0;
        let mut paint = Paint::default();
        paint.set_anti_alias(true);
        paint.set_color(color);
        canvas.draw_text_blob(&blob, Point::new(x, y), &paint);
    }
}

fn draw_fit_image(canvas: &Canvas, image: &Image, dst: Rect, alpha: f32) {
    let mut paint = Paint::default();
    paint.set_anti_alias(true);
    paint.set_alpha_f(alpha);
    canvas.draw_image_rect_with_sampling_options(
        image,
        None,
        dst,
        SamplingOptions::default(),
        &paint,
    );
}

fn draw_width_image(canvas: &Canvas, image: &Image, top_left: Point, width: f32, alpha: f32) {
    let iw = image.width() as f32;
    let ih = image.height() as f32;
    if iw <= 0.0 || ih <= 0.0 {
        return;
    }
    let height = width * ih / iw;
    draw_fit_image(
        canvas,
        image,
        Rect::from_xywh(top_left.x, top_left.y, width, height),
        alpha,
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

fn load_optional_cached_image(
    base: &Path,
    path: Option<&str>,
    cache: &mut HashMap<String, Image>,
    profile: &mut RenderProfile,
) -> Option<Image> {
    path.and_then(|p| load_cached_image(base, p, cache, profile).ok())
}

fn load_cached_image(
    base: &Path,
    path: &str,
    cache: &mut HashMap<String, Image>,
    profile: &mut RenderProfile,
) -> Result<Image, String> {
    if let Some(image) = cache.get(path) {
        profile.image_cache_hits += 1;
        return Ok(image.clone());
    }
    let started = Instant::now();
    let image = load_image(base, path)?;
    profile.load_images += started.elapsed();
    profile.image_cache_misses += 1;
    cache.insert(path.to_string(), image.clone());
    Ok(image)
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

fn load_fonts(fonts: &FontsIr) -> FontSet {
    let key = format!("{}\0{}\0{}", fonts.dir, fonts.default, fonts.bold);
    let cache = FONT_CACHE.get_or_init(|| Mutex::new(HashMap::new()));
    if let Ok(mut cache) = cache.lock() {
        if let Some(fonts) = cache.get(&key) {
            return fonts.clone();
        }
        let loaded = FontSet {
            regular: load_typeface(&fonts.dir, &fonts.default),
            bold: load_typeface(&fonts.dir, &fonts.bold),
        };
        cache.insert(key, loaded.clone());
        return loaded;
    }

    FontSet {
        regular: load_typeface(&fonts.dir, &fonts.default),
        bold: load_typeface(&fonts.dir, &fonts.bold),
    }
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

fn is_non_limited(value: &str) -> bool {
    matches!(value.trim(), "" | "normal" | "非限定")
}

fn parse_color(value: &str) -> (u8, u8, u8) {
    let hex = value.trim().trim_start_matches('#');
    if hex.len() == 6
        && let (Ok(r), Ok(g), Ok(b)) = (
            u8::from_str_radix(&hex[0..2], 16),
            u8::from_str_radix(&hex[2..4], 16),
            u8::from_str_radix(&hex[4..6], 16),
        )
    {
        return (r, g, b);
    }
    (204, 204, 204)
}

fn rare_count(value: &str) -> usize {
    if value == "rarity_birthday" {
        return 1;
    }
    value
        .chars()
        .find(|ch| ch.is_ascii_digit())
        .and_then(|ch| ch.to_digit(10))
        .unwrap_or(0) as usize
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
    fn counts_rare_stars() {
        assert_eq!(rare_count("rarity_4"), 4);
        assert_eq!(rare_count("rarity_birthday"), 1);
        assert_eq!(rare_count("unknown"), 0);
    }
}
