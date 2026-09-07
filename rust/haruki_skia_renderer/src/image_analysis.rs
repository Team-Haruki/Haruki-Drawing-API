//! Bounded native image analysis and alpha-only fields.
use crate::pillow_resize::{PillowResizeLimits, resize_rgba8_pillow_bilinear};
use skia_safe::{AlphaType, ColorType, Data, ImageInfo};

#[derive(Debug)]
pub(crate) enum AnalysisError {
    Invalid(String),
    Unsupported(String),
    Limit(String),
}

impl From<String> for AnalysisError {
    fn from(message: String) -> Self {
        Self::Invalid(message)
    }
}

fn decode_rgba(data: Data) -> Result<(Vec<u8>, i32, i32), AnalysisError> {
    decode_rgba_limited(data, 16 * 1024 * 1024, i32::MAX)
}

fn decode_rgba_limited(
    data: Data,
    max_pixels: u64,
    max_dimension: i32,
) -> Result<(Vec<u8>, i32, i32), AnalysisError> {
    let mut codec = skia_safe::Codec::from_data(data)
        .ok_or_else(|| "invalid encoded image for alpha bounds".to_string())?;
    let size = codec.dimensions();
    if size.width <= 0 || size.height <= 0 {
        return Err(AnalysisError::Invalid(
            "invalid image analysis dimensions".to_string(),
        ));
    }
    let bytes = size.width as u64 * size.height as u64 * 4;
    if bytes / 4 > max_pixels || size.width.max(size.height) > max_dimension {
        return Err(AnalysisError::Limit(
            "image analysis exceeds the configured pixel/dimension limit (at most 64 MiB RGBA)"
                .to_string(),
        ));
    }
    if bytes > 64 * 1024 * 1024 {
        return Err(AnalysisError::Unsupported(
            "image analysis exceeds the 64 MiB decoded-pixel limit".to_string(),
        ));
    }
    let mut pixels = Vec::new();
    pixels.try_reserve_exact(bytes as usize).map_err(|_| {
        AnalysisError::Unsupported("image analysis allocation rejected".to_string())
    })?;
    pixels.resize(bytes as usize, 0);
    let info = ImageInfo::new(
        (size.width, size.height),
        ColorType::RGBA8888,
        AlphaType::Unpremul,
        None,
    );
    let result = codec.get_pixels_with_options(&info, &mut pixels, size.width as usize * 4, None);
    if result != skia_safe::codec::Result::Success {
        return Err(AnalysisError::Invalid(format!(
            "image analysis decode failed: {result:?}"
        )));
    }
    Ok((pixels, size.width, size.height))
}

/// Keep RGBA within Rust; Python receives immutable A8 data only. Both the caller's
/// layer limit and the gray-field hard limit are checked before pixel allocation.
pub(crate) fn alpha_field(
    data: Data,
    max_pixels: u64,
) -> Result<(Vec<u8>, i32, i32), AnalysisError> {
    if max_pixels == 0 || max_pixels > 16 * 1024 * 1024 {
        return Err(AnalysisError::Invalid(
            "alpha field max_pixels must be in 1..=16777216".to_string(),
        ));
    }
    let (pixels, width, height) = decode_rgba_limited(data, max_pixels, 32767)?;
    let mut alpha = Vec::new();
    alpha
        .try_reserve_exact(pixels.len() / 4)
        .map_err(|_| AnalysisError::Unsupported("alpha field allocation rejected".to_string()))?;
    alpha.extend(pixels.chunks_exact(4).map(|pixel| pixel[3]));
    Ok((alpha, width, height))
}

fn pixel_alpha_bounds(pixels: &[u8], width: i32, height: i32) -> Option<[i32; 4]> {
    let mut bounds = [width, height, 0, 0];
    for (i, pixel) in pixels.chunks_exact(4).enumerate() {
        if pixel[3] != 0 {
            let x = (i % width as usize) as i32;
            let y = (i / width as usize) as i32;
            bounds[0] = bounds[0].min(x);
            bounds[1] = bounds[1].min(y);
            bounds[2] = bounds[2].max(x + 1);
            bounds[3] = bounds[3].max(y + 1);
        }
    }
    (bounds[2] > 0).then_some(bounds)
}

pub(crate) fn alpha_bounds(data: Data) -> Result<Option<[i32; 4]>, AnalysisError> {
    let (pixels, width, height) = decode_rgba(data)?;
    Ok(pixel_alpha_bounds(&pixels, width, height))
}

/// Preserve the legacy costume detector: alpha bounds first, then a bilinear thumbnail,
/// per-row mean edge color, and minimum hits in both axes. All rounding is ties-to-even.
pub(crate) fn foreground_bounds(
    data: Data,
    detect_width: u32,
) -> Result<Option<[i32; 4]>, AnalysisError> {
    if !(1..=4096).contains(&detect_width) {
        return Err(AnalysisError::Invalid(
            "detect_width must be between 1 and 4096".to_string(),
        ));
    }
    let (pixels, width, height) = decode_rgba(data)?;
    if let Some(bounds) = pixel_alpha_bounds(&pixels, width, height) {
        if bounds != [0, 0, width, height] {
            return Ok(Some(bounds));
        }
    }
    let scale = (f64::from(detect_width) / f64::from(width)).min(1.0);
    let sw = (f64::from(width) * scale).round_ties_even().max(1.0) as usize;
    let sh = (f64::from(height) * scale).round_ties_even().max(1.0) as usize;
    if sw > 32768 || sh > 32768 {
        return Err(AnalysisError::Unsupported(
            "foreground sample exceeds dimension limit".to_string(),
        ));
    }
    let sample = if (sw, sh) == (width as usize, height as usize) {
        pixels
    } else {
        resize_rgba8_pillow_bilinear(
            &pixels,
            width as usize,
            height as usize,
            sw,
            sh,
            PillowResizeLimits::new(64 * 1024 * 1024, 128 * 1024 * 1024, 32768),
        )
        .map_err(|e| AnalysisError::Unsupported(e.to_string()))?
    };
    // A one-pixel-wide source used to index past the image in the Python detector.
    let edge = ((sw as f64 * 0.02).round_ties_even() as usize)
        .max(2)
        .min(sw);
    let min_col = ((sh as f64 * 0.015).round_ties_even() as usize).max(2);
    let min_row = ((sw as f64 * 0.015).round_ties_even() as usize).max(2);
    let mut cols = vec![0_usize; sw];
    let mut rows = vec![0_usize; sh];
    for (y, row) in sample.chunks_exact(sw * 4).enumerate() {
        let mut bg = [0_i32; 3];
        for x in 0..edge {
            for channel in 0..3 {
                bg[channel] +=
                    i32::from(row[x * 4 + channel]) + i32::from(row[(sw - 1 - x) * 4 + channel]);
            }
        }
        for value in &mut bg {
            *value /= (edge * 2) as i32;
        }
        for (x, pixel) in row.chunks_exact(4).enumerate() {
            if pixel[3] > 16 && (0..3).any(|c| (i32::from(pixel[c]) - bg[c]).abs() > 36) {
                cols[x] += 1;
                rows[y] += 1;
            }
        }
    }
    let xs: Vec<usize> = cols
        .iter()
        .enumerate()
        .filter_map(|(i, &hits)| (hits >= min_col).then_some(i))
        .collect();
    let ys: Vec<usize> = rows
        .iter()
        .enumerate()
        .filter_map(|(i, &hits)| (hits >= min_row).then_some(i))
        .collect();
    match (xs.first(), xs.last(), ys.first(), ys.last()) {
        (Some(&x0), Some(&x1), Some(&y0), Some(&y1)) => {
            let inv_scale = 1.0 / scale;
            Ok(Some([
                (x0 as f64 * inv_scale).round_ties_even().max(0.0) as i32,
                (y0 as f64 * inv_scale).round_ties_even().max(0.0) as i32,
                (((x1 + 1) as f64 * inv_scale).round_ties_even() as i32).min(width),
                (((y1 + 1) as f64 * inv_scale).round_ties_even() as i32).min(height),
            ]))
        }
        _ => Ok(None),
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn alpha_field_keeps_only_alpha_and_checks_limits() {
        let source = include_bytes!("../tests/fixtures/webp_smoke.webp");
        let (alpha, w, h) = alpha_field(Data::new_copy(source), 221).unwrap();
        assert_eq!((w, h), (17, 13));
        assert_eq!(alpha, vec![255; 221]);
        assert!(alpha_field(Data::new_copy(source), 220).is_err());
        assert!(alpha_field(Data::new_copy(source), 0).is_err());
        assert!(alpha_field(Data::new_copy(source), 16 * 1024 * 1024 + 1).is_err());
        assert!(alpha_field(Data::new_copy(b"invalid"), 221).is_err());
    }
}
