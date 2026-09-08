//! Exact float32 counterpart of the NumPy source-outline distance field.

pub(crate) const MAX_WORK: usize = 500_000_000;

pub(crate) fn field(
    data: &[u8],
    ends: &[usize],
    width: usize,
    height: usize,
    origin: (f32, f32),
    spread: f32,
) -> Result<Vec<u8>, String> {
    let pixels = width.checked_mul(height).ok_or("SDF size overflow")?;
    let count = data.len() / 8;
    if width == 0
        || height == 0
        || pixels > 16_777_216
        || !data.len().is_multiple_of(8)
        || count > 262_144
        || pixels.checked_mul(count).is_none_or(|n| n > MAX_WORK)
        || !origin.0.is_finite()
        || !origin.1.is_finite()
        || origin.0.abs() > 1.0e9
        || origin.1.abs() > 1.0e9
        || !spread.is_finite()
        || spread < 1.0
        || ends.last().copied() != Some(count)
    {
        return Err("Invalid or oversized source SDF input".into());
    }
    let points: Vec<(f32, f32)> = data
        .chunks_exact(8)
        .map(|p| {
            (
                f32::from_le_bytes(p[..4].try_into().unwrap()),
                f32::from_le_bytes(p[4..].try_into().unwrap()),
            )
        })
        .collect();
    if points
        .iter()
        .any(|(x, y)| !x.is_finite() || !y.is_finite() || x.abs() > 1.0e9 || y.abs() > 1.0e9)
    {
        return Err("Non-finite or oversized source outline".into());
    }
    let mut segments = Vec::with_capacity(count);
    let mut start = 0;
    for &end in ends {
        if end <= start + 1 || end > count {
            return Err("Invalid source outline contour".into());
        }
        for index in start..end {
            let (ax, ay) = points[index];
            let (bx, by) = points[if index + 1 == end { start } else { index + 1 }];
            let vx = bx - ax;
            let vy = by - ay;
            segments.push((ax, ay, by, vx, vy, vx * vx + vy * vy));
        }
        start = end;
    }
    let mut output = vec![0; pixels];
    for y in 0..height {
        let py = -(origin.1 + y as f32 + 0.5);
        for x in 0..width {
            let px = origin.0 + x as f32 + 0.5;
            let mut distance = 1.0e9f32;
            let mut winding = 0i16;
            for &(ax, ay, by, vx, vy, length_sq) in &segments {
                let wx = px - ax;
                let wy = py - ay;
                let d = if length_sq <= 1.0e-12 {
                    (wx * wx + wy * wy).sqrt()
                } else {
                    let t = ((wx * vx + wy * vy) / length_sq).clamp(0.0, 1.0);
                    let dx = px - (ax + t * vx);
                    let dy = py - (ay + t * vy);
                    (dx * dx + dy * dy).sqrt()
                };
                distance = distance.min(d);
                let cross = vx * (py - ay) - (px - ax) * vy;
                if ay <= py && by > py && cross > 0.0 {
                    winding = winding.wrapping_add(1);
                }
                if ay > py && by <= py && cross < 0.0 {
                    winding = winding.wrapping_sub(1);
                }
            }
            let signed = if winding != 0 { distance } else { -distance };
            let value = (0.5 + signed / spread).clamp(0.0, 1.0);
            output[y * width + x] = (value * 255.0).round_ties_even() as u8;
        }
    }
    Ok(output)
}
