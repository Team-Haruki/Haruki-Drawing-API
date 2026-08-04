//! Pillow 12.3-compatible 8-bit grayscale resampling for native TMP glyph fields.
//!
//! TMP decorative text historically performs two distinct Pillow operations before SDF
//! shading: a full-raster BICUBIC resize of an atlas crop, then a BICUBIC affine transform into
//! the canvas-clipped glyph bounds.  Skia cubic sampling is not byte-equivalent, so the native
//! renderer carries the small L-mode kernels directly instead of transporting pre-warped A8
//! request memory from Python.

use crate::pillow_resize::PillowResizeLimits;
use std::mem;

const BICUBIC_SUPPORT: f64 = 2.0;
const PRECISION_BITS: u32 = 22;
const PRECISION_SCALE: f64 = (1_u64 << PRECISION_BITS) as f64;
const ROUNDING_BIAS: i64 = 1_i64 << (PRECISION_BITS - 1);

#[derive(Clone, Copy)]
struct Bounds {
    first: usize,
    count: usize,
}

struct AxisCoefficients {
    kernel_size: usize,
    bounds: Vec<Bounds>,
    coefficients: Vec<i32>,
}

pub(crate) fn resize_l_pillow_bicubic(
    source: &[u8],
    source_width: usize,
    source_height: usize,
    destination_width: usize,
    destination_height: usize,
    limits: PillowResizeLimits,
) -> Result<Vec<u8>, String> {
    validate_dimensions(source_width, source_height, limits)?;
    validate_dimensions(destination_width, destination_height, limits)?;
    let source_len = gray_len(source_width, source_height)?;
    if source.len() != source_len {
        return Err(format!(
            "L source length mismatch: expected {source_len} bytes, got {}",
            source.len()
        ));
    }
    let destination_len = gray_len(destination_width, destination_height)?;
    if destination_len > limits.max_output_bytes {
        return Err(format!(
            "Pillow BICUBIC L output needs {destination_len} bytes, limit is {}",
            limits.max_output_bytes
        ));
    }
    if source_width == destination_width && source_height == destination_height {
        validate_working_bytes(source_len, limits)?;
        return Ok(source.to_vec());
    }

    let vertical_first = source_width
        .checked_mul(100)
        .is_some_and(|threshold| source_height > threshold)
        && destination_height < source_height;
    validate_resize_working_bytes(
        source_width,
        source_height,
        destination_width,
        destination_height,
        vertical_first,
        limits,
    )?;
    let mut current = source.to_vec();
    let mut width = source_width;
    let mut height = source_height;
    if vertical_first {
        if height != destination_height {
            current = resize_vertical(&current, width, height, destination_height)?;
            height = destination_height;
        }
        if width != destination_width {
            current = resize_horizontal(&current, width, height, destination_width)?;
            width = destination_width;
        }
    } else {
        if width != destination_width {
            current = resize_horizontal(&current, width, height, destination_width)?;
            width = destination_width;
        }
        if height != destination_height {
            current = resize_vertical(&current, width, height, destination_height)?;
            height = destination_height;
        }
    }
    debug_assert_eq!((width, height), (destination_width, destination_height));
    Ok(current)
}

pub(crate) fn transform_l_pillow_bicubic(
    source: &[u8],
    source_width: usize,
    source_height: usize,
    destination_width: usize,
    destination_height: usize,
    affine: [f64; 6],
    limits: PillowResizeLimits,
) -> Result<Vec<u8>, String> {
    validate_dimensions(source_width, source_height, limits)?;
    validate_dimensions(destination_width, destination_height, limits)?;
    let source_len = gray_len(source_width, source_height)?;
    if source.len() != source_len {
        return Err(format!(
            "L source length mismatch: expected {source_len} bytes, got {}",
            source.len()
        ));
    }
    if affine.iter().any(|value| !value.is_finite()) {
        return Err("Pillow BICUBIC L affine contains a non-finite scalar".to_string());
    }
    let destination_len = gray_len(destination_width, destination_height)?;
    if destination_len > limits.max_output_bytes {
        return Err(format!(
            "Pillow BICUBIC L affine output needs {destination_len} bytes, limit is {}",
            limits.max_output_bytes
        ));
    }
    validate_working_bytes(destination_len, limits)?;

    let mut output = vec![0_u8; destination_len];
    for y in 0..destination_height {
        for x in 0..destination_width {
            // Geometry.c::affine_transform maps destination pixel centers.
            let center_x = x as f64 + 0.5;
            let center_y = y as f64 + 0.5;
            let source_x = affine[0] * center_x + affine[1] * center_y + affine[2];
            let source_y = affine[3] * center_x + affine[4] * center_y + affine[5];
            output[y * destination_width + x] =
                sample_l_pillow_bicubic(source, source_width, source_height, source_x, source_y);
        }
    }
    Ok(output)
}

fn validate_dimensions(
    width: usize,
    height: usize,
    limits: PillowResizeLimits,
) -> Result<(), String> {
    if width == 0 || height == 0 {
        return Err("Pillow BICUBIC L dimensions must be positive".to_string());
    }
    let effective_limit = limits.max_dimension.min(i32::MAX as usize);
    if width > effective_limit || height > effective_limit {
        return Err(format!(
            "Pillow BICUBIC L dimension {width}x{height} exceeds limit {effective_limit}"
        ));
    }
    Ok(())
}

fn gray_len(width: usize, height: usize) -> Result<usize, String> {
    width
        .checked_mul(height)
        .ok_or_else(|| "Pillow BICUBIC L size calculation overflow".to_string())
}

fn validate_working_bytes(required: usize, limits: PillowResizeLimits) -> Result<(), String> {
    if required > limits.max_working_bytes {
        return Err(format!(
            "Pillow BICUBIC L scratch needs {required} bytes, limit is {}",
            limits.max_working_bytes
        ));
    }
    Ok(())
}

fn axis_coefficient_bytes(input_size: usize, output_size: usize) -> Result<usize, String> {
    let scale = input_size as f64 / output_size as f64;
    let support = BICUBIC_SUPPORT * scale.max(1.0);
    let kernel_size = (support.ceil() as usize)
        .checked_mul(2)
        .and_then(|size| size.checked_add(1))
        .ok_or_else(|| "Pillow BICUBIC coefficient size overflow".to_string())?;
    let entries = output_size
        .checked_mul(kernel_size)
        .ok_or_else(|| "Pillow BICUBIC coefficient count overflow".to_string())?;
    let bounds = output_size
        .checked_mul(mem::size_of::<Bounds>())
        .ok_or_else(|| "Pillow BICUBIC bounds byte count overflow".to_string())?;
    let normalized = entries
        .checked_mul(mem::size_of::<f64>())
        .ok_or_else(|| "Pillow BICUBIC coefficient byte count overflow".to_string())?;
    let fixed = entries
        .checked_mul(mem::size_of::<i32>())
        .ok_or_else(|| "Pillow BICUBIC fixed coefficient byte count overflow".to_string())?;
    bounds
        .checked_add(normalized)
        .and_then(|total| total.checked_add(fixed))
        .ok_or_else(|| "Pillow BICUBIC coefficient working set overflow".to_string())
}

fn validate_resize_working_bytes(
    source_width: usize,
    source_height: usize,
    destination_width: usize,
    destination_height: usize,
    vertical_first: bool,
    limits: PillowResizeLimits,
) -> Result<(), String> {
    let mut width = source_width;
    let mut height = source_height;
    let mut current_bytes = gray_len(width, height)?;
    let mut peak = current_bytes;
    if vertical_first {
        if height != destination_height {
            let (output_bytes, pass_peak) = resize_pass_working_bytes(
                current_bytes,
                width,
                destination_height,
                height,
                destination_height,
            )?;
            current_bytes = output_bytes;
            peak = peak.max(pass_peak);
            height = destination_height;
        }
        if width != destination_width {
            let (_, pass_peak) = resize_pass_working_bytes(
                current_bytes,
                destination_width,
                height,
                width,
                destination_width,
            )?;
            peak = peak.max(pass_peak);
        }
    } else {
        if width != destination_width {
            let (output_bytes, pass_peak) = resize_pass_working_bytes(
                current_bytes,
                destination_width,
                height,
                width,
                destination_width,
            )?;
            current_bytes = output_bytes;
            peak = peak.max(pass_peak);
            width = destination_width;
        }
        if height != destination_height {
            let (_, pass_peak) = resize_pass_working_bytes(
                current_bytes,
                width,
                destination_height,
                height,
                destination_height,
            )?;
            peak = peak.max(pass_peak);
        }
    }
    validate_working_bytes(peak, limits)
}

fn resize_pass_working_bytes(
    current_bytes: usize,
    next_width: usize,
    next_height: usize,
    axis_input: usize,
    axis_output: usize,
) -> Result<(usize, usize), String> {
    let output_bytes = gray_len(next_width, next_height)?;
    let coefficient_bytes = axis_coefficient_bytes(axis_input, axis_output)?;
    let peak = current_bytes
        .checked_add(output_bytes)
        .and_then(|total| total.checked_add(coefficient_bytes))
        .ok_or_else(|| "Pillow BICUBIC L working set overflow".to_string())?;
    Ok((output_bytes, peak))
}

fn precompute_coefficients(
    input_size: usize,
    output_size: usize,
) -> Result<AxisCoefficients, String> {
    let scale = input_size as f64 / output_size as f64;
    let filter_scale = scale.max(1.0);
    let support = BICUBIC_SUPPORT * filter_scale;
    let inverse_filter_scale = 1.0 / filter_scale;
    let kernel_size = (support.ceil() as usize)
        .checked_mul(2)
        .and_then(|size| size.checked_add(1))
        .ok_or_else(|| "Pillow BICUBIC coefficient size overflow".to_string())?;
    let entries = output_size
        .checked_mul(kernel_size)
        .ok_or_else(|| "Pillow BICUBIC coefficient count overflow".to_string())?;
    let mut bounds = Vec::with_capacity(output_size);
    let mut normalized = vec![0.0_f64; entries];

    for destination in 0..output_size {
        let center = (destination as f64 + 0.5) * scale;
        let first = ((center - support + 0.5) as isize).max(0) as usize;
        let last = ((center + support + 0.5) as usize).min(input_size);
        let count = last.saturating_sub(first);
        bounds.push(Bounds { first, count });
        let row = &mut normalized[destination * kernel_size..(destination + 1) * kernel_size];
        let mut weight_sum = 0.0;
        for (offset, weight) in row.iter_mut().take(count).enumerate() {
            let distance = (offset as f64 + first as f64 - center + 0.5) * inverse_filter_scale;
            *weight = bicubic_filter(distance);
            weight_sum += *weight;
        }
        if weight_sum != 0.0 {
            for weight in row.iter_mut().take(count) {
                *weight /= weight_sum;
            }
        }
    }
    Ok(AxisCoefficients {
        kernel_size,
        bounds,
        coefficients: normalized.into_iter().map(quantize_coefficient).collect(),
    })
}

#[inline]
fn bicubic_filter(mut value: f64) -> f64 {
    value = value.abs();
    if value < 1.0 {
        ((1.5 * value - 2.5) * value * value) + 1.0
    } else if value < 2.0 {
        -0.5 * (((value - 5.0) * value + 8.0) * value - 4.0)
    } else {
        0.0
    }
}

#[inline]
fn quantize_coefficient(value: f64) -> i32 {
    if value < 0.0 {
        (-0.5 + value * PRECISION_SCALE) as i32
    } else {
        (0.5 + value * PRECISION_SCALE) as i32
    }
}

fn resize_horizontal(
    source: &[u8],
    source_width: usize,
    source_height: usize,
    destination_width: usize,
) -> Result<Vec<u8>, String> {
    let axis = precompute_coefficients(source_width, destination_width)?;
    let mut output = vec![0_u8; gray_len(destination_width, source_height)?];
    for y in 0..source_height {
        for destination_x in 0..destination_width {
            let bounds = axis.bounds[destination_x];
            let coefficients = &axis.coefficients
                [destination_x * axis.kernel_size..(destination_x + 1) * axis.kernel_size];
            let mut sum = ROUNDING_BIAS;
            for (offset, coefficient) in coefficients.iter().take(bounds.count).enumerate() {
                sum += i64::from(source[y * source_width + bounds.first + offset])
                    * i64::from(*coefficient);
            }
            output[y * destination_width + destination_x] = clip_fixed_8(sum);
        }
    }
    Ok(output)
}

fn resize_vertical(
    source: &[u8],
    width: usize,
    source_height: usize,
    destination_height: usize,
) -> Result<Vec<u8>, String> {
    let axis = precompute_coefficients(source_height, destination_height)?;
    let mut output = vec![0_u8; gray_len(width, destination_height)?];
    for destination_y in 0..destination_height {
        let bounds = axis.bounds[destination_y];
        let coefficients = &axis.coefficients
            [destination_y * axis.kernel_size..(destination_y + 1) * axis.kernel_size];
        for x in 0..width {
            let mut sum = ROUNDING_BIAS;
            for (offset, coefficient) in coefficients.iter().take(bounds.count).enumerate() {
                sum += i64::from(source[(bounds.first + offset) * width + x])
                    * i64::from(*coefficient);
            }
            output[destination_y * width + x] = clip_fixed_8(sum);
        }
    }
    Ok(output)
}

#[inline]
fn clip_fixed_8(value: i64) -> u8 {
    (value >> PRECISION_BITS).clamp(0, 255) as u8
}

fn sample_l_pillow_bicubic(source: &[u8], width: usize, height: usize, xin: f64, yin: f64) -> u8 {
    if xin < 0.0 || xin >= width as f64 || yin < 0.0 || yin >= height as f64 {
        return 0;
    }
    let shifted_x = xin - 0.5;
    let shifted_y = yin - 0.5;
    let floor_x = shifted_x.floor() as i64;
    let floor_y = shifted_y.floor() as i64;
    let dx = shifted_x - floor_x as f64;
    let dy = shifted_y - floor_y as f64;
    let base_x = floor_x - 1;
    let base_y = floor_y - 1;

    let row = |raw_y: i64| -> f64 {
        let y = raw_y.clamp(0, height as i64 - 1) as usize;
        let value = |offset: i64| -> f64 {
            let x = (base_x + offset).clamp(0, width as i64 - 1) as usize;
            f64::from(source[y * width + x])
        };
        cubic(value(0), value(1), value(2), value(3), dx)
    };
    let v1 = row(base_y);
    let v2 = if (0..height as i64).contains(&(base_y + 1)) {
        row(base_y + 1)
    } else {
        v1
    };
    let v3 = if (0..height as i64).contains(&(base_y + 2)) {
        row(base_y + 2)
    } else {
        v2
    };
    let v4 = if (0..height as i64).contains(&(base_y + 3)) {
        row(base_y + 3)
    } else {
        v3
    };
    cubic(v1, v2, v3, v4, dy).clamp(0.0, 255.0) as u8
}

#[inline]
fn cubic(v1: f64, v2: f64, v3: f64, v4: f64, distance: f64) -> f64 {
    let p1 = v2;
    let p2 = -v1 + v3;
    let p3 = 2.0 * (v1 - v2) + v3 - v4;
    let p4 = -v1 + v2 - v3 + v4;
    p1 + distance * (p2 + distance * (p3 + distance * p4))
}

#[cfg(test)]
mod tests {
    use super::*;

    const LIMITS: PillowResizeLimits = PillowResizeLimits::new(1024 * 1024, 4 * 1024 * 1024, 4096);

    fn patterned_l(width: usize, height: usize) -> Vec<u8> {
        (0..height)
            .flat_map(|y| {
                (0..width).map(move |x| ((x * 37 + y * 71 + 23 + (x * y) % 29) % 256) as u8)
            })
            .collect()
    }

    #[test]
    fn resize_matches_pillow_12_3_bicubic_l_golden() {
        let actual = resize_l_pillow_bicubic(&patterned_l(5, 4), 5, 4, 7, 3, LIMITS)
            .expect("resize succeeds");
        let expected = [
            34, 55, 84, 111, 148, 183, 202, 127, 154, 193, 216, 139, 127, 158, 232, 134, 69, 109,
            79, 86, 113,
        ];
        assert_eq!(actual, expected);
    }

    #[test]
    fn affine_matches_pillow_12_3_bicubic_l_golden() {
        let actual = transform_l_pillow_bicubic(
            &patterned_l(5, 4),
            5,
            4,
            8,
            6,
            [0.82, -0.17, 0.65, 0.11, 1.07, -0.4],
            LIMITS,
        )
        .expect("transform succeeds");
        let expected = [
            25, 64, 98, 131, 176, 0, 0, 0, 76, 123, 163, 189, 198, 201, 0, 0, 154, 205, 255, 98, 9,
            72, 0, 0, 235, 59, 0, 68, 131, 152, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0, 0,
            0,
        ];
        assert_eq!(actual, expected);
    }

    #[test]
    fn resize_and_affine_reject_working_sets_before_allocation() {
        let source = patterned_l(5, 4);
        let tiny = PillowResizeLimits::new(1024, 19, 4096);
        let identity = resize_l_pillow_bicubic(&source, 5, 4, 5, 4, tiny).expect_err("must reject");
        assert!(identity.contains("scratch"), "unexpected error: {identity}");

        let resized = resize_l_pillow_bicubic(&source, 5, 4, 7, 3, tiny).expect_err("must reject");
        assert!(resized.contains("scratch"), "unexpected error: {resized}");

        let affine = transform_l_pillow_bicubic(
            &source,
            5,
            4,
            8,
            6,
            [0.82, -0.17, 0.65, 0.11, 1.07, -0.4],
            PillowResizeLimits::new(1024, 47, 4096),
        )
        .expect_err("must reject");
        assert!(affine.contains("scratch"), "unexpected error: {affine}");
    }
}
