//! Bounded shared vector paths. The layout and styling arrive from the widget tree.

use skia_safe::{
    AlphaType, Canvas, ColorType, Data, ImageInfo, Paint, PaintStyle, Path, PathBuilder,
    PathEffect, paint, surfaces,
};

use crate::ir::{Node, VectorCap, VectorCommand, VectorJoin, VectorPathNode};

pub(crate) fn validate(node: &Node, total: &mut usize) -> Result<(), String> {
    let children = match node {
        Node::Group(n) => Some(&n.children),
        Node::Transform(n) => Some(&n.children),
        Node::RasterSubscene(n) => Some(&n.children),
        Node::UnitySubscene(n) => Some(&n.children),
        _ => None,
    };
    if let Some(children) = children {
        for child in children {
            validate(child, total)?;
        }
    }
    let Node::VectorPath(path) = node else {
        return Ok(());
    };
    *total = total.saturating_add(path.commands.len());
    if path.commands.len() > 16384 || *total > 131072 {
        return Err("vector command limit exceeded".into());
    }
    if !path.width.is_finite()
        || !(0.0..=1000.0).contains(&path.width)
        || !path.phase.is_finite()
        || path.pos.iter().any(|v| !v.is_finite() || v.abs() > 1e7)
    {
        return Err("invalid vector stroke width/phase/position".into());
    }
    if path.dashes.len() > 32
        || path.dashes.len() % 2 != 0
        || path.dashes.iter().any(|v| !v.is_finite() || *v < 0.01)
    {
        return Err("invalid vector dash pattern".into());
    }
    let mut current: Option<[f32; 2]> = None;
    let mut start = [0.0, 0.0];
    let mut length = 0.0_f64;
    for command in &path.commands {
        let points: &[f32] = match command {
            VectorCommand::Move(p) | VectorCommand::Line(p) => p,
            VectorCommand::Quad(p) | VectorCommand::Ellipse(p) => p,
            VectorCommand::Cubic(p) => p,
            VectorCommand::Close => &[],
        };
        if points.iter().any(|v| !v.is_finite() || v.abs() > 1e7) {
            return Err("invalid vector coordinate".into());
        }
        if let VectorCommand::Ellipse(p) = command {
            if p[2] <= p[0] || p[3] <= p[1] {
                return Err("vector ellipse must have positive dimensions".into());
            }
            length += 2.0 * (p[2] as f64 - p[0] as f64 + p[3] as f64 - p[1] as f64);
            start = [p[2], (p[1] + p[3]) * 0.5];
            current = Some(start);
            continue;
        }
        if let VectorCommand::Move(p) = command {
            current = Some(*p);
            start = *p;
            continue;
        }
        let Some(mut previous) = current else {
            return Err("vector subpath must start with move".into());
        };
        if matches!(command, VectorCommand::Close) {
            length +=
                (previous[0] as f64 - start[0] as f64).hypot(previous[1] as f64 - start[1] as f64);
            current = Some(start);
        } else {
            for p in points.chunks_exact(2) {
                length +=
                    (previous[0] as f64 - p[0] as f64).hypot(previous[1] as f64 - p[1] as f64);
                previous = [p[0], p[1]];
            }
            current = Some(previous);
        }
    }
    let period: f64 = path.dashes.iter().map(|v| *v as f64).sum();
    if period > 0.0 && length / period * path.dashes.len() as f64 > 100000.0 {
        return Err("vector dash subdivision exceeds 100000 segments".into());
    }
    Ok(())
}

pub(crate) fn draw(
    canvas: &Canvas,
    path: &VectorPathNode,
    off: (f32, f32),
    max_node_pixels: usize,
    available_bytes: usize,
) -> Result<(), String> {
    let mut builder = PathBuilder::new();
    for command in &path.commands {
        match command {
            VectorCommand::Ellipse(p) => {
                builder.add_oval(skia_safe::Rect::new(p[0], p[1], p[2], p[3]), None, Some(1));
            }
            VectorCommand::Move(p) => {
                builder.move_to((p[0], p[1]));
            }
            VectorCommand::Line(p) => {
                builder.line_to((p[0], p[1]));
            }
            VectorCommand::Quad(p) => {
                builder.quad_to((p[0], p[1]), (p[2], p[3]));
            }
            VectorCommand::Cubic(p) => {
                builder.cubic_to((p[0], p[1]), (p[2], p[3]), (p[4], p[5]));
            }
            VectorCommand::Close => {
                builder.close();
            }
        }
    }
    let curve = builder.detach();
    let count = canvas.save();
    canvas.translate((off.0 + path.pos[0], off.1 + path.pos[1]));
    let result = draw_supersampled(canvas, &curve, path, max_node_pixels, available_bytes);
    canvas.restore_to_count(count);
    result
}

/// Integrate coverage on a device-aligned subpixel grid. Keeping premultiplied
/// channels throughout avoids color fringes on translucent strokes and glyphs.
/// The scratch raster covers only the visible control hull, never a whole page
/// for a small marker; allocation is bounded before any raster is created.
fn draw_supersampled(
    canvas: &Canvas,
    curve: &Path,
    path: &VectorPathNode,
    max_node_pixels: usize,
    available_bytes: usize,
) -> Result<(), String> {
    let Some(clip) = canvas.device_clip_bounds() else {
        return Ok(());
    };
    if curve.is_empty() || (path.fill.is_none() && (path.stroke.is_none() || path.width == 0.0)) {
        return Ok(());
    }
    let matrix = canvas.local_to_device_as_3x3();
    // Miter limit 10 reaches five stroke widths beyond the centered path.
    let pad = if path.stroke.is_some() {
        path.width * 5.0
    } else {
        0.0
    };
    let bounds = matrix.map_rect(curve.bounds().with_outset((pad, pad))).0;
    let left = (bounds.left.floor() - 2.0).max(clip.left as f32) as i32;
    let top = (bounds.top.floor() - 2.0).max(clip.top as f32) as i32;
    let right = (bounds.right.ceil() + 2.0).min(clip.right as f32) as i32;
    let bottom = (bounds.bottom.ceil() + 2.0).min(clip.bottom as f32) as i32;
    if left >= right || top >= bottom {
        return Ok(());
    }
    let (width, height) = (right - left, bottom - top);
    let pixels = (width as usize)
        .checked_mul(height as usize)
        .ok_or("vector coverage pixel count overflow")?;
    let high_pixels = pixels
        .checked_mul(16)
        .ok_or("vector coverage pixel count overflow")?;
    // High-resolution surface + readback, low-resolution Vec + SkData copy.
    let peak = pixels
        .checked_mul(136)
        .ok_or("vector coverage byte count overflow")?;
    if width > 32767 / 4 || height > 32767 / 4 || high_pixels > max_node_pixels {
        return Err("vector coverage exceeds node pixel/dimension limit".into());
    }
    if peak > available_bytes {
        return Err("vector coverage exceeds remaining scene byte limit".into());
    }
    let info = ImageInfo::new(
        (width * 4, height * 4),
        ColorType::RGBA8888,
        AlphaType::Premul,
        None,
    );
    let mut raster =
        surfaces::raster(&info, None, None).ok_or("vector coverage surface allocation failed")?;
    raster.canvas().scale((4.0, 4.0));
    raster.canvas().translate((-left as f32, -top as f32));
    raster.canvas().concat(&matrix);
    draw_curve(raster.canvas(), curve, path)?;
    let mut high = Vec::new();
    high.try_reserve_exact(high_pixels * 4)
        .map_err(|_| "vector coverage readback allocation failed")?;
    high.resize(high_pixels * 4, 0_u8);
    if !raster.read_pixels(&info, &mut high, width as usize * 16, (0, 0)) {
        return Err("vector coverage readback failed".into());
    }
    let mut low = Vec::new();
    low.try_reserve_exact(pixels * 4)
        .map_err(|_| "vector coverage output allocation failed")?;
    low.resize(pixels * 4, 0_u8);
    for y in 0..height as usize {
        for x in 0..width as usize {
            let mut sum = [0_u16; 4];
            for dy in 0..4 {
                for dx in 0..4 {
                    let i = ((y * 4 + dy) * width as usize * 4 + x * 4 + dx) * 4;
                    for c in 0..4 {
                        sum[c] += high[i + c] as u16;
                    }
                }
            }
            for c in 0..4 {
                low[(y * width as usize + x) * 4 + c] = ((sum[c] + 8) / 16) as u8;
            }
        }
    }
    let info = ImageInfo::new(
        (width, height),
        ColorType::RGBA8888,
        AlphaType::Premul,
        None,
    );
    let image =
        skia_safe::images::raster_from_data(&info, Data::new_copy(&low), width as usize * 4)
            .ok_or("vector coverage image allocation failed")?;
    // Geometry already includes all parent transforms. The existing device clip
    // remains in force, and the caller restores the original matrix on return.
    canvas.reset_matrix();
    canvas.draw_image(&image, (left as f32, top as f32), None);
    Ok(())
}

fn draw_curve(canvas: &Canvas, curve: &Path, path: &VectorPathNode) -> Result<(), String> {
    let mut paint = Paint::default();
    paint.set_anti_alias(true);
    if let Some(color) = path.fill {
        paint.set_color(crate::interp::color_of(color));
        canvas.draw_path(curve, &paint);
    }
    if let Some(color) = path.stroke.filter(|_| path.width > 0.0) {
        paint.set_color(crate::interp::color_of(color));
        paint.set_style(PaintStyle::Stroke);
        paint.set_stroke_width(path.width);
        paint.set_stroke_cap(match path.cap {
            VectorCap::Butt => paint::Cap::Butt,
            VectorCap::Round => paint::Cap::Round,
            VectorCap::Square => paint::Cap::Square,
        });
        paint.set_stroke_join(match path.join {
            VectorJoin::Miter => paint::Join::Miter,
            VectorJoin::Round => paint::Join::Round,
            VectorJoin::Bevel => paint::Join::Bevel,
        });
        paint.set_stroke_miter(10.0);
        if !path.dashes.is_empty() {
            let Some(effect) = PathEffect::dash(&path.dashes, path.phase) else {
                return Err("native vector dash construction failed".into());
            };
            paint.set_path_effect(effect);
        }
        canvas.draw_path(curve, &paint);
    }
    Ok(())
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn coverage_budget_failure_restores_parent_transform() {
        let node: Node = serde_json::from_str(
            r#"{"type":"VectorPath", "pos":[1,2], "fill":[20,40,80,128],
            "commands":[{"op":"ellipse","points":[2,2,10,10]}]}"#,
        )
        .unwrap();
        let Node::VectorPath(path) = node else {
            panic!("vector fixture")
        };
        let mut surface = surfaces::raster_n32_premul((20, 20)).unwrap();
        let canvas = surface.canvas();
        canvas.translate((3.0, 2.0));
        let matrix = canvas.local_to_device_as_3x3();
        let error = draw(canvas, &path, (1.0, 1.0), 6400, 1).unwrap_err();
        assert!(error.contains("scene byte"));
        assert_eq!(canvas.local_to_device_as_3x3(), matrix);
    }

    #[test]
    fn coverage_allocation_is_clipped_before_enforcing_limits() {
        let node: Node = serde_json::from_str(
            r#"{"type":"VectorPath", "fill":[20,40,80,128], "commands":[
            {"op":"move","points":[-1000000,-1000000]},
            {"op":"line","points":[1000000,-1000000]},
            {"op":"line","points":[1000000,1000000]},
            {"op":"line","points":[-1000000,1000000]}, {"op":"close"}]}"#,
        )
        .unwrap();
        let Node::VectorPath(path) = node else {
            panic!("vector fixture")
        };
        let mut surface = surfaces::raster_n32_premul((10, 10)).unwrap();
        draw(surface.canvas(), &path, (0.0, 0.0), 1600, 13600).unwrap();
        let info = ImageInfo::new((1, 1), ColorType::RGBA8888, AlphaType::Premul, None);
        let mut pixel = [0_u8; 4];
        assert!(surface.read_pixels(&info, &mut pixel, 4, (5, 5)));
        assert_eq!(pixel, [10, 20, 40, 128]);
    }

    #[test]
    fn rejects_dense_dashes_before_native_allocation() {
        let node: Node = serde_json::from_str(
            r#"{
            "type":"VectorPath", "commands":[
                {"op":"move","points":[0,0]}, {"op":"line","points":[10000000,0]}],
            "stroke":[0,0,0,255], "dashes":[0.01,0.01]
        }"#,
        )
        .unwrap();
        assert!(validate(&node, &mut 0).unwrap_err().contains("subdivision"));
    }

    #[test]
    fn rejects_unstarted_subpath_inside_transform() {
        let node: Node = serde_json::from_str(
            r#"{
            "type":"Transform", "matrix":[1,0,0,0,1,0], "children":[{
                "type":"VectorPath", "commands":[{"op":"close"}]
            }]
        }"#,
        )
        .unwrap();
        assert!(
            validate(&node, &mut 0)
                .unwrap_err()
                .contains("start with move")
        );
    }

    #[test]
    fn validates_closed_cubic_and_quadratic_subpaths() {
        let node: Node = serde_json::from_str(
            r#"{
            "type":"VectorPath", "commands":[{"op":"move","points":[10,10]},
                {"op":"cubic","points":[20,0,30,10,30,20]},
                {"op":"quad","points":[20,30,10,10]}, {"op":"close"}],
            "fill":[100,40,20,128], "stroke":[10,20,30,255], "dashes":[2,3]
        }"#,
        )
        .unwrap();
        let mut total = 0;
        validate(&node, &mut total).unwrap();
        assert_eq!(total, 4);
    }
}
