//! Card List → Render IR v2 scene builder (port step ④, approach 1).
//!
//! Reproduces the bespoke `render_card_list_inner`/`draw_card`/`compose_thumbnail`
//! draw path as a declarative `Node` tree rendered by the general interpreter.
//! The thumbnail composite becomes a sub-`Group` of layered Image/Rect/Text nodes,
//! so nothing relies on in-memory image handles (keeps the door open for a future
//! pure-Python IRBuilder). Card layout math is kept identical to the legacy path.

use crate::ir::*;
use crate::{
    BG_PADDING, CARD_H, CARD_SEP, CARD_W, CardIr, CardListIr, GRID_COLS, GRID_PADDING, IconsIr,
    PANEL_WIDTH, RenderedImage, THUMB, TITLE_H, TITLE_SEP, ThumbnailIr, is_non_limited, rare_count,
};

const WATERMARK_FALLBACK: &str = "Haruki Drawing API";

pub(crate) fn render_card_list_via_scene(ir: &CardListIr) -> Result<RenderedImage, String> {
    if ir.version != 1 {
        return Err(format!("unsupported IR version {}", ir.version));
    }
    let scene = build_card_list_scene(ir);
    crate::interp::render_scene_inner(&scene)
}

fn solid(c: Color4) -> Option<Fill> {
    Some(Fill::Solid(c))
}

fn build_card_list_scene(ir: &CardListIr) -> Scene {
    let has_title = ir.title.as_ref().is_some_and(|s| !s.is_empty());
    let rows = ir.cards.len().max(1).div_ceil(GRID_COLS) as f32;
    let title_h = if has_title { TITLE_H + TITLE_SEP } else { 0.0 };
    let grid_h = GRID_PADDING * 2.0 + rows * CARD_H + (rows - 1.0).max(0.0) * CARD_SEP;
    let width = (PANEL_WIDTH + BG_PADDING * 2.0).ceil() as i32;
    let height = (BG_PADDING * 2.0 + title_h + grid_h).ceil() as i32;

    let mut children: Vec<Node> = Vec::new();
    let mut y = BG_PADDING;

    if has_title {
        let title = ir.title.as_deref().unwrap_or_default();
        push_notice_title(&mut children, BG_PADDING, y, PANEL_WIDTH, title);
        y += TITLE_H + TITLE_SEP;
    }

    // Frosted grid panel behind the cards.
    children.push(Node::BlurGlass(BlurGlassNode {
        pos: [BG_PADDING, y],
        size: [PANEL_WIDTH, grid_h],
        radius: 12.0,
        fill: [255, 255, 255, 80],
        shadow_alpha: 0.26,
    }));

    for (idx, card) in ir.cards.iter().enumerate() {
        let row = idx / GRID_COLS;
        let col = idx % GRID_COLS;
        let x = BG_PADDING + GRID_PADDING + col as f32 * (CARD_W + CARD_SEP);
        let cy = y + GRID_PADDING + row as f32 * (CARD_H + CARD_SEP);
        children.push(Node::Group(GroupNode {
            offset: [x, cy],
            size: [CARD_W, CARD_H],
            clip: None,
            children: build_card_cell(card, &ir.icons, ir.now_ms),
        }));
    }

    if ir.watermark.enabled {
        let text = if ir.watermark.text.is_empty() {
            WATERMARK_FALLBACK
        } else {
            ir.watermark.text.as_str()
        };
        children.push(Node::Text(TextNode {
            text: text.to_string(),
            pos: [width as f32 - 150.0, height as f32 - 10.0],
            font: FontRef {
                role: FontRole::Default,
                size: 12.0,
            },
            align: HAlign::Left,
            baseline: Baseline::Alphabetic,
            fill: [0, 0, 0, 120],
        }));
    }

    let background = match ir.background_img_path.as_deref() {
        Some(path) => Some(Node::ImageBg(ImageBgNode {
            path: path.to_string(),
        })),
        None => Some(Node::TriangleBg(TriangleBgNode {
            hour: ir.background_hour.unwrap_or(15.0),
        })),
    };

    Scene {
        version: 2,
        assets_base_dir: ir.assets_base_dir.clone(),
        export_format: ir.export_format.clone(),
        jpg_quality: ir.jpg_quality,
        fonts: FontsIr {
            dir: ir.fonts.dir.clone(),
            default: ir.fonts.default.clone(),
            bold: ir.fonts.bold.clone(),
            heavy: None,
            emoji: None,
        },
        canvas: CanvasIr { width, height },
        background,
        root: Node::Group(GroupNode {
            offset: [0.0, 0.0],
            size: [width as f32, height as f32],
            clip: None,
            children,
        }),
    }
}

/// Notice-style title: frosted bar + "提示" label + the title text.
fn push_notice_title(children: &mut Vec<Node>, x: f32, y: f32, width: f32, title: &str) {
    children.push(Node::BlurGlass(BlurGlassNode {
        pos: [x, y],
        size: [width, TITLE_H],
        radius: 10.0,
        fill: [255, 246, 219, 220],
        shadow_alpha: 0.24,
    }));
    children.push(Node::Text(TextNode {
        text: "提示".to_string(),
        pos: [x + 14.0, y + 34.0],
        font: FontRef {
            role: FontRole::Bold,
            size: 22.0,
        },
        align: HAlign::Left,
        baseline: Baseline::Alphabetic,
        fill: [166, 90, 0, 255],
    }));
    children.push(Node::Text(TextNode {
        text: title.to_string(),
        pos: [x + 80.0, y + 34.0],
        font: FontRef {
            role: FontRole::Default,
            size: 22.0,
        },
        align: HAlign::Left,
        baseline: Baseline::Alphabetic,
        fill: [98, 68, 0, 255],
    }));
}

/// Card cell contents in the cell's local frame (origin at the card top-left).
fn build_card_cell(card: &CardIr, icons: &IconsIr, now_ms: i64) -> Vec<Node> {
    let limited = !is_non_limited(&card.supply_type);
    let fill: Color4 = if limited {
        [255, 250, 220, 200]
    } else {
        [255, 255, 255, 80]
    };

    let mut nodes: Vec<Node> = Vec::new();
    nodes.push(Node::BlurGlass(BlurGlassNode {
        pos: [0.0, 0.0],
        size: [CARD_W, CARD_H],
        radius: 10.0,
        fill,
        shadow_alpha: 0.30,
    }));

    if card.release_at > now_ms {
        nodes.push(Node::Text(TextNode {
            text: "未上线".to_string(),
            pos: [4.0, CARD_H - 8.0],
            font: FontRef {
                role: FontRole::Bold,
                size: 20.0,
            },
            align: HAlign::Left,
            baseline: Baseline::Alphabetic,
            fill: [200, 0, 0, 255],
        }));
    }

    if card.skill_type.is_some()
        && let Some(path) = card.skill_icon_path.as_deref()
    {
        nodes.push(Node::Image(ImageNode {
            pos: [CARD_W - 40.0, CARD_H - 40.0],
            size: [32.0, 32.0],
            path: path.to_string(),
            fit: Fit::Stretch,
            alpha: 1.0,
        }));
    }

    let thumbs: Vec<&ThumbnailIr> = card.thumbnail_info.iter().take(2).collect();
    let total_w = thumbs.len() as f32 * THUMB + (thumbs.len().saturating_sub(1)) as f32 * 16.0;
    let mut tx = (CARD_W - total_w) * 0.5;
    let icon_path = limited_icon_path(&card.supply_type, icons);
    for thumb in thumbs {
        nodes.push(Node::Shadow(ShadowNode {
            pos: [tx, 16.0],
            size: [THUMB, THUMB],
            radius: 8.0,
            alpha: 0.35,
            offset: [2.0, 4.0],
            sigma: 2.5,
            color: [0, 0, 0, 255],
        }));
        nodes.push(Node::Group(GroupNode {
            offset: [tx, 16.0],
            size: [THUMB, THUMB],
            clip: None,
            children: build_thumbnail(thumb),
        }));
        if let Some(path) = icon_path {
            nodes.push(Node::Image(ImageNode {
                pos: [tx + THUMB - 75.0, 16.0],
                size: [75.0, 0.0],
                path: path.to_string(),
                fit: Fit::Width,
                alpha: 1.0,
            }));
        }
        tx += THUMB + 16.0;
    }

    nodes.push(center_text(
        &card.prefix,
        FontRole::Bold,
        20.0,
        0.0,
        129.0,
        CARD_W,
        24.0,
        [0, 0, 0, 255],
    ));
    let mut id_text = format!("ID:{}", card.card_id);
    if limited {
        id_text.push_str(&format!("【{}】", card.supply_type));
    }
    nodes.push(center_text(
        &id_text,
        FontRole::Default,
        20.0,
        0.0,
        158.0,
        CARD_W,
        24.0,
        [0, 0, 0, 255],
    ));
    nodes
}

/// Layered thumbnail composite in its own 100x100 local frame.
fn build_thumbnail(thumb: &ThumbnailIr) -> Vec<Node> {
    let mut nodes: Vec<Node> = Vec::new();
    nodes.push(Node::Image(ImageNode {
        pos: [0.0, 0.0],
        size: [100.0, 100.0],
        path: thumb.card_thumbnail_path.clone(),
        fit: Fit::Cover,
        alpha: 1.0,
    }));

    if thumb.is_pcard {
        nodes.push(Node::Rect(RectNode {
            pos: [0.0, 76.0],
            size: [100.0, 24.0],
            fill: solid([70, 70, 100, 255]),
            stroke: None,
            stroke_width: 1.0,
        }));
        let text = thumb
            .custom_text
            .clone()
            .unwrap_or_else(|| format!("Lv.{}", thumb.level.unwrap_or_default()));
        nodes.push(Node::Text(TextNode {
            text,
            pos: [6.0, 92.0],
            font: FontRef {
                role: FontRole::Bold,
                size: 20.0,
            },
            align: HAlign::Left,
            baseline: Baseline::Alphabetic,
            fill: [255, 255, 255, 255],
        }));
    }

    if let Some(path) = thumb.frame_img_path.as_deref() {
        nodes.push(fit_image(path, 0.0, 0.0, 100.0, 100.0));
    }
    if let Some(path) = thumb.attr_img_path.as_deref() {
        nodes.push(fit_image(path, 1.0, 0.0, 22.0, 25.0));
    }
    if thumb.is_pcard
        && thumb.train_rank.unwrap_or_default() > 0
        && let Some(path) = thumb.train_rank_img_path.as_deref()
    {
        nodes.push(fit_image(path, 65.0, 65.0, 35.0, 35.0));
    }

    let rare_num = rare_count(&thumb.rare);
    let rare_w = 17.0;
    let rare_h = 17.0;
    let voffset = if thumb.is_pcard { 24.0 } else { 6.0 };
    for i in 0..rare_num {
        nodes.push(fit_image(
            &thumb.rare_img_path,
            6.0 + rare_w * i as f32,
            100.0 - rare_h - voffset,
            rare_w,
            rare_h,
        ));
    }
    nodes
}

fn limited_icon_path<'a>(supply: &str, icons: &'a IconsIr) -> Option<&'a str> {
    match supply {
        "期间限定" | "WL限定" | "联动限定" => icons.term_limited.as_deref(),
        "Fes限定" | "CFes限定" | "BFes限定" => icons.fes_limited.as_deref(),
        _ => None,
    }
}

fn fit_image(path: &str, x: f32, y: f32, w: f32, h: f32) -> Node {
    Node::Image(ImageNode {
        pos: [x, y],
        size: [w, h],
        path: path.to_string(),
        fit: Fit::Stretch,
        alpha: 1.0,
    })
}

#[allow(clippy::too_many_arguments)]
fn center_text(
    text: &str,
    role: FontRole,
    size: f32,
    rx: f32,
    ry: f32,
    rw: f32,
    rh: f32,
    fill: Color4,
) -> Node {
    // Mirror draw_center_text: horizontally centered, baseline at rect.bottom - 5.
    Node::Text(TextNode {
        text: text.to_string(),
        pos: [rx + rw * 0.5, ry + rh - 5.0],
        font: FontRef { role, size },
        align: HAlign::Center,
        baseline: Baseline::Alphabetic,
        fill,
    })
}
