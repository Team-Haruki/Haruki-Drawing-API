//! Pixel-compatible RGBA8 Lanczos resize for Pillow retirement.
//!
//! This is intentionally a raster operation rather than a Skia sampling mode. Skia's cubic
//! samplers are not Pillow's three-lobed Lanczos filter, and substituting one changes both the
//! reconstruction kernel and Pillow's two-pass integer rounding.
//!
//! The implementation follows Pillow 12.3's public `RGBA.resize(..., LANCZOS)` path:
//!
//! 1. convert straight RGBA8 to Pillow's integer-premultiplied `RGBa` representation;
//! 2. build normalized Lanczos-3 coefficients, widening the support while shrinking;
//! 3. quantize coefficients to 22-bit fixed point and round/clip after *each* axis;
//! 4. convert the resized `RGBa` pixels back to straight RGBA8.
//!
//! Only a full-image resize is exposed because that is the custom-profile prefab use case. A
//! future IR crop/resize node must add Pillow's float32 `box` semantics explicitly rather than
//! pretending a source rectangle is equivalent.

use std::error::Error;
use std::fmt;
use std::mem;

const CHANNELS: usize = 4;
const LANCZOS_SUPPORT: f64 = 3.0;
const PRECISION_BITS: u32 = 22;
const PRECISION_SCALE: f64 = (1_u64 << PRECISION_BITS) as f64;
const ROUNDING_BIAS: i64 = 1_i64 << (PRECISION_BITS - 1);

/// Allocation and work limits supplied by the renderer.
///
/// `max_output_bytes` applies to the returned RGBA buffer. `max_working_bytes` covers the
/// owned premultiplied source, a pass destination, and coefficient tables at their peak; the
/// caller-owned input slice is not counted. `max_dimension` is also a CPU bound because the
/// algorithm is proportional to the destination pixels times the widened filter support.
#[derive(Clone, Copy, Debug, Eq, PartialEq)]
pub(crate) struct PillowResizeLimits {
    pub(crate) max_output_bytes: usize,
    pub(crate) max_working_bytes: usize,
    pub(crate) max_dimension: usize,
}

impl PillowResizeLimits {
    pub(crate) const fn new(
        max_output_bytes: usize,
        max_working_bytes: usize,
        max_dimension: usize,
    ) -> Self {
        Self {
            max_output_bytes,
            max_working_bytes,
            max_dimension,
        }
    }
}

#[derive(Clone, Debug, Eq, PartialEq)]
pub(crate) enum PillowResizeError {
    ZeroDimension,
    DimensionLimit {
        width: usize,
        height: usize,
        limit: usize,
    },
    SourceLength {
        expected: usize,
        actual: usize,
    },
    OutputLimit {
        required: usize,
        limit: usize,
    },
    WorkingLimit {
        required: usize,
        limit: usize,
    },
    SizeOverflow,
}

impl fmt::Display for PillowResizeError {
    fn fmt(&self, f: &mut fmt::Formatter<'_>) -> fmt::Result {
        match self {
            Self::ZeroDimension => write!(f, "Pillow Lanczos resize dimensions must be positive"),
            Self::DimensionLimit {
                width,
                height,
                limit,
            } => write!(
                f,
                "Pillow Lanczos resize dimension {width}x{height} exceeds limit {limit}"
            ),
            Self::SourceLength { expected, actual } => write!(
                f,
                "RGBA source length mismatch: expected {expected} bytes, got {actual}"
            ),
            Self::OutputLimit { required, limit } => write!(
                f,
                "Pillow Lanczos output needs {required} bytes, limit is {limit}"
            ),
            Self::WorkingLimit { required, limit } => write!(
                f,
                "Pillow Lanczos scratch needs {required} bytes, limit is {limit}"
            ),
            Self::SizeOverflow => write!(f, "Pillow Lanczos resize size calculation overflow"),
        }
    }
}

impl Error for PillowResizeError {}

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

/// Resize a tightly packed, straight-alpha RGBA8 raster with Pillow-compatible Lanczos-3.
pub(crate) fn resize_rgba8_pillow_lanczos(
    source: &[u8],
    source_width: usize,
    source_height: usize,
    destination_width: usize,
    destination_height: usize,
    limits: PillowResizeLimits,
) -> Result<Vec<u8>, PillowResizeError> {
    validate_dimensions(source_width, source_height, limits)?;
    validate_dimensions(destination_width, destination_height, limits)?;

    let source_bytes = rgba_byte_len(source_width, source_height)?;
    if source.len() != source_bytes {
        return Err(PillowResizeError::SourceLength {
            expected: source_bytes,
            actual: source.len(),
        });
    }
    let destination_bytes = rgba_byte_len(destination_width, destination_height)?;
    if destination_bytes > limits.max_output_bytes {
        return Err(PillowResizeError::OutputLimit {
            required: destination_bytes,
            limit: limits.max_output_bytes,
        });
    }

    // Pillow returns a copy before its RGBA -> RGBa conversion when neither axis changes. Apart
    // from being cheaper, this preserves hidden RGB values under fully transparent source pixels.
    if source_width == destination_width && source_height == destination_height {
        return Ok(source.to_vec());
    }

    let vertical_first = source_width
        .checked_mul(100)
        .is_some_and(|threshold| source_height > threshold)
        && destination_height < source_height;
    enforce_working_limit(
        source_width,
        source_height,
        destination_width,
        destination_height,
        vertical_first,
        limits.max_working_bytes,
    )?;

    let mut current = premultiply_rgba(source);
    let mut width = source_width;
    let mut height = source_height;

    // Pillow's Python wrapper reverses the normal pass order for very tall shrinking images to
    // avoid a huge horizontal intermediate. Keep it: the intermediate 8-bit rounding makes the
    // two orders observably different.
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
    unpremultiply_rgba_in_place(&mut current);
    Ok(current)
}

fn validate_dimensions(
    width: usize,
    height: usize,
    limits: PillowResizeLimits,
) -> Result<(), PillowResizeError> {
    if width == 0 || height == 0 {
        return Err(PillowResizeError::ZeroDimension);
    }
    // Pillow's native resampler receives C `int` dimensions. Keep that contract even if a caller
    // accidentally configures a larger usize limit.
    let effective_limit = limits.max_dimension.min(i32::MAX as usize);
    if width > effective_limit || height > effective_limit {
        return Err(PillowResizeError::DimensionLimit {
            width,
            height,
            limit: effective_limit,
        });
    }
    Ok(())
}

fn rgba_byte_len(width: usize, height: usize) -> Result<usize, PillowResizeError> {
    width
        .checked_mul(height)
        .and_then(|pixels| pixels.checked_mul(CHANNELS))
        .ok_or(PillowResizeError::SizeOverflow)
}

fn axis_table_sizes(input: usize, output: usize) -> Result<(usize, usize), PillowResizeError> {
    let scale = input as f64 / output as f64;
    let filter_scale = scale.max(1.0);
    let support = LANCZOS_SUPPORT * filter_scale;
    if !support.is_finite() || support > usize::MAX as f64 {
        return Err(PillowResizeError::SizeOverflow);
    }
    let kernel_size = (support.ceil() as usize)
        .checked_mul(2)
        .and_then(|size| size.checked_add(1))
        .ok_or(PillowResizeError::SizeOverflow)?;
    let entries = output
        .checked_mul(kernel_size)
        .ok_or(PillowResizeError::SizeOverflow)?;
    Ok((kernel_size, entries))
}

fn coefficient_storage_bytes(input: usize, output: usize) -> Result<usize, PillowResizeError> {
    let (_, entries) = axis_table_sizes(input, output)?;
    // Coefficient construction briefly owns both the normalized f64 table and the quantized i32
    // table. Pillow aliases those allocations in C; keeping them separate is safe Rust and this
    // accounting ensures it cannot be used to bypass the renderer's memory ceiling.
    entries
        .checked_mul(mem::size_of::<f64>() + mem::size_of::<i32>())
        .and_then(|bytes| {
            output
                .checked_mul(mem::size_of::<Bounds>())
                .and_then(|bounds| bytes.checked_add(bounds))
        })
        .ok_or(PillowResizeError::SizeOverflow)
}

fn enforce_working_limit(
    source_width: usize,
    source_height: usize,
    destination_width: usize,
    destination_height: usize,
    vertical_first: bool,
    limit: usize,
) -> Result<(), PillowResizeError> {
    let source_bytes = rgba_byte_len(source_width, source_height)?;
    let mut peak = source_bytes;

    let (first_width, first_height, first_coefficients) = if vertical_first {
        (
            source_width,
            destination_height,
            (source_height, destination_height),
        )
    } else {
        (
            destination_width,
            source_height,
            (source_width, destination_width),
        )
    };
    let first_changes = first_coefficients.0 != first_coefficients.1;
    let first_bytes = rgba_byte_len(first_width, first_height)?;
    if first_changes {
        let coeff_bytes = coefficient_storage_bytes(first_coefficients.0, first_coefficients.1)?;
        peak = peak.max(
            source_bytes
                .checked_add(first_bytes)
                .and_then(|bytes| bytes.checked_add(coeff_bytes))
                .ok_or(PillowResizeError::SizeOverflow)?,
        );
    }

    let second_coefficients = if vertical_first {
        (source_width, destination_width)
    } else {
        (source_height, destination_height)
    };
    if second_coefficients.0 != second_coefficients.1 {
        let prior_bytes = if first_changes {
            first_bytes
        } else {
            source_bytes
        };
        let coeff_bytes = coefficient_storage_bytes(second_coefficients.0, second_coefficients.1)?;
        let destination_bytes = rgba_byte_len(destination_width, destination_height)?;
        peak = peak.max(
            prior_bytes
                .checked_add(destination_bytes)
                .and_then(|bytes| bytes.checked_add(coeff_bytes))
                .ok_or(PillowResizeError::SizeOverflow)?,
        );
    }

    if peak > limit {
        return Err(PillowResizeError::WorkingLimit {
            required: peak,
            limit,
        });
    }
    Ok(())
}

fn precompute_coefficients(
    input_size: usize,
    output_size: usize,
) -> Result<AxisCoefficients, PillowResizeError> {
    let scale = input_size as f64 / output_size as f64;
    let filter_scale = scale.max(1.0);
    let support = LANCZOS_SUPPORT * filter_scale;
    let inverse_filter_scale = 1.0 / filter_scale;
    let (kernel_size, entries) = axis_table_sizes(input_size, output_size)?;

    let mut bounds = Vec::with_capacity(output_size);
    let mut normalized = vec![0.0_f64; entries];

    for destination in 0..output_size {
        let center = (destination as f64 + 0.5) * scale;
        // C's float-to-int cast truncates toward zero. The +0.5 is part of Pillow's bounds
        // convention, not an ordinary floor/ceil pair.
        let first = ((center - support + 0.5) as isize).max(0) as usize;
        let last = ((center + support + 0.5) as usize).min(input_size);
        let count = last.saturating_sub(first);
        bounds.push(Bounds { first, count });

        let row = &mut normalized[destination * kernel_size..(destination + 1) * kernel_size];
        let mut weight_sum = 0.0_f64;
        for (offset, weight) in row.iter_mut().take(count).enumerate() {
            let distance = (offset as f64 + first as f64 - center + 0.5) * inverse_filter_scale;
            *weight = lanczos_filter(distance);
            weight_sum += *weight;
        }
        if weight_sum != 0.0 {
            for weight in row.iter_mut().take(count) {
                *weight /= weight_sum;
            }
        }
    }

    let coefficients = normalized.into_iter().map(quantize_coefficient).collect();
    Ok(AxisCoefficients {
        kernel_size,
        bounds,
        coefficients,
    })
}

#[inline]
fn sinc_filter(value: f64) -> f64 {
    if value == 0.0 {
        1.0
    } else {
        let radians = value * std::f64::consts::PI;
        radians.sin() / radians
    }
}

#[inline]
fn lanczos_filter(value: f64) -> f64 {
    if (-LANCZOS_SUPPORT..LANCZOS_SUPPORT).contains(&value) {
        sinc_filter(value) * sinc_filter(value / LANCZOS_SUPPORT)
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
) -> Result<Vec<u8>, PillowResizeError> {
    let axis = precompute_coefficients(source_width, destination_width)?;
    let output_len = rgba_byte_len(destination_width, source_height)?;
    let mut output = vec![0_u8; output_len];

    for y in 0..source_height {
        for destination_x in 0..destination_width {
            let bounds = axis.bounds[destination_x];
            let coefficients = &axis.coefficients
                [destination_x * axis.kernel_size..(destination_x + 1) * axis.kernel_size];
            let mut sums = [ROUNDING_BIAS; CHANNELS];
            for (offset, coefficient) in coefficients.iter().take(bounds.count).enumerate() {
                let source_offset = (y * source_width + bounds.first + offset) * CHANNELS;
                for channel in 0..CHANNELS {
                    sums[channel] +=
                        i64::from(source[source_offset + channel]) * i64::from(*coefficient);
                }
            }
            let destination_offset = (y * destination_width + destination_x) * CHANNELS;
            for channel in 0..CHANNELS {
                output[destination_offset + channel] = clip_fixed_8(sums[channel]);
            }
        }
    }
    Ok(output)
}

fn resize_vertical(
    source: &[u8],
    width: usize,
    source_height: usize,
    destination_height: usize,
) -> Result<Vec<u8>, PillowResizeError> {
    let axis = precompute_coefficients(source_height, destination_height)?;
    let output_len = rgba_byte_len(width, destination_height)?;
    let mut output = vec![0_u8; output_len];

    for destination_y in 0..destination_height {
        let bounds = axis.bounds[destination_y];
        let coefficients = &axis.coefficients
            [destination_y * axis.kernel_size..(destination_y + 1) * axis.kernel_size];
        for x in 0..width {
            let mut sums = [ROUNDING_BIAS; CHANNELS];
            for (offset, coefficient) in coefficients.iter().take(bounds.count).enumerate() {
                let source_offset = ((bounds.first + offset) * width + x) * CHANNELS;
                for channel in 0..CHANNELS {
                    sums[channel] +=
                        i64::from(source[source_offset + channel]) * i64::from(*coefficient);
                }
            }
            let destination_offset = (destination_y * width + x) * CHANNELS;
            for channel in 0..CHANNELS {
                output[destination_offset + channel] = clip_fixed_8(sums[channel]);
            }
        }
    }
    Ok(output)
}

#[inline]
fn clip_fixed_8(value: i64) -> u8 {
    (value >> PRECISION_BITS).clamp(0, 255) as u8
}

fn premultiply_rgba(source: &[u8]) -> Vec<u8> {
    let mut output = Vec::with_capacity(source.len());
    for pixel in source.chunks_exact(CHANNELS) {
        let alpha = pixel[3];
        output.push(multiply_divide_255(pixel[0], alpha));
        output.push(multiply_divide_255(pixel[1], alpha));
        output.push(multiply_divide_255(pixel[2], alpha));
        output.push(alpha);
    }
    output
}

#[inline]
fn multiply_divide_255(value: u8, alpha: u8) -> u8 {
    let product = u32::from(value) * u32::from(alpha) + 128;
    (((product >> 8) + product) >> 8) as u8
}

fn unpremultiply_rgba_in_place(pixels: &mut [u8]) {
    for pixel in pixels.chunks_exact_mut(CHANNELS) {
        let alpha = pixel[3];
        if alpha != 0 && alpha != 255 {
            for channel in &mut pixel[..3] {
                *channel = ((255 * u32::from(*channel)) / u32::from(alpha)).min(255) as u8;
            }
        }
    }
}

#[cfg(test)]
mod tests {
    use super::*;

    const TEST_LIMITS: PillowResizeLimits =
        PillowResizeLimits::new(1024 * 1024, 4 * 1024 * 1024, 4096);

    fn patterned_rgba(width: usize, height: usize, seed: usize) -> Vec<u8> {
        (0..height)
            .flat_map(|y| {
                (0..width).flat_map(move |x| {
                    (0..CHANNELS).map(move |channel| {
                        ((x * 37 + y * 71 + channel * 53 + seed + ((x * y + channel * 11) % 29))
                            % 256) as u8
                    })
                })
            })
            .collect()
    }

    #[test]
    fn matches_pillow_12_3_downscale_golden() {
        let source = patterned_rgba(5, 4, 23);
        let actual =
            resize_rgba8_pillow_lanczos(&source, 5, 4, 2, 3, TEST_LIMITS).expect("resize succeeds");
        let expected = [
            34, 119, 170, 120, 245, 163, 33, 53, 245, 113, 18, 54, 137, 79, 126, 173, 115, 78, 136,
            170, 52, 126, 186, 111,
        ];
        assert_eq!(actual, expected);
    }

    #[test]
    fn matches_pillow_12_3_transparent_edge_golden() {
        let source = [
            255, 10, 200, 0, 240, 20, 180, 64, 30, 220, 40, 128, 5, 250, 80, 255,
        ];
        let actual =
            resize_rgba8_pillow_lanczos(&source, 4, 1, 7, 1, TEST_LIMITS).expect("resize succeeds");
        let expected = [
            0, 3, 0, 0, 255, 0, 255, 16, 250, 8, 190, 59, 137, 111, 87, 87, 22, 227, 40, 138, 4,
            249, 68, 222, 5, 255, 88, 255,
        ];
        assert_eq!(actual, expected);
    }

    #[test]
    fn identity_preserves_hidden_transparent_rgb() {
        let source = [231, 119, 47, 0, 1, 2, 3, 255];
        let actual =
            resize_rgba8_pillow_lanczos(&source, 2, 1, 2, 1, TEST_LIMITS).expect("copy succeeds");
        assert_eq!(actual, source);
    }

    #[test]
    fn matches_pillow_12_3_very_tall_vertical_first_golden() {
        let source = patterned_rgba(2, 201, 19);
        let actual = resize_rgba8_pillow_lanczos(&source, 2, 201, 3, 17, TEST_LIMITS)
            .expect("resize succeeds");
        let expected = [
            100, 121, 146, 134, 97, 110, 142, 138, 95, 101, 139, 141, 109, 110, 142, 131, 103, 113,
            131, 128, 96, 117, 121, 124, 104, 112, 140, 129, 108, 112, 133, 122, 113, 110, 126,
            115, 98, 112, 140, 127, 105, 105, 134, 133, 110, 99, 126, 139, 114, 106, 142, 127, 102,
            106, 140, 129, 89, 107, 136, 131, 121, 106, 141, 124, 123, 111, 135, 124, 126, 116,
            128, 123, 116, 108, 142, 125, 115, 101, 124, 121, 116, 94, 105, 116, 115, 103, 134,
            121, 108, 104, 142, 132, 99, 105, 148, 143, 115, 100, 135, 117, 107, 107, 130, 123, 98,
            112, 126, 129, 108, 102, 142, 129, 117, 107, 135, 128, 126, 110, 126, 127, 106, 121,
            146, 134, 108, 112, 138, 136, 109, 104, 130, 137, 109, 110, 142, 131, 104, 115, 139,
            137, 98, 120, 136, 142, 102, 112, 140, 129, 121, 115, 137, 128, 139, 117, 135, 126, 98,
            113, 143, 130, 110, 110, 126, 125, 122, 105, 109, 119, 114, 110, 136, 125, 104, 108,
            141, 139, 95, 106, 145, 153, 124, 104, 138, 125, 110, 110, 136, 127, 95, 117, 133, 128,
            112, 109, 145, 131, 122, 113, 126, 121, 132, 120, 104, 110,
        ];
        assert_eq!(actual, expected);
    }

    #[test]
    fn validates_lengths_dimensions_and_limits() {
        assert_eq!(
            resize_rgba8_pillow_lanczos(&[], 0, 1, 1, 1, TEST_LIMITS),
            Err(PillowResizeError::ZeroDimension)
        );
        assert_eq!(
            resize_rgba8_pillow_lanczos(&[0; 7], 2, 1, 1, 1, TEST_LIMITS),
            Err(PillowResizeError::SourceLength {
                expected: 8,
                actual: 7,
            })
        );
        let output_limited = PillowResizeLimits::new(7, 1024, 32);
        assert_eq!(
            resize_rgba8_pillow_lanczos(&[0; 4], 1, 1, 2, 1, output_limited),
            Err(PillowResizeError::OutputLimit {
                required: 8,
                limit: 7,
            })
        );
        let dimension_limited = PillowResizeLimits::new(1024, 4096, 1);
        assert_eq!(
            resize_rgba8_pillow_lanczos(&[0; 4], 1, 1, 2, 1, dimension_limited),
            Err(PillowResizeError::DimensionLimit {
                width: 2,
                height: 1,
                limit: 1,
            })
        );
        let working_limited = PillowResizeLimits::new(1024, 1, 32);
        assert!(matches!(
            resize_rgba8_pillow_lanczos(&[0; 4], 1, 1, 2, 1, working_limited),
            Err(PillowResizeError::WorkingLimit { .. })
        ));
    }
}
