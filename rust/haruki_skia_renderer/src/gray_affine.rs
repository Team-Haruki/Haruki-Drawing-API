//! Pillow-compatible L-mode affine BICUBIC with a zero exterior.
//!
//! Adapted from Pillow 12.3.0 src/libImaging/Geometry.c (affine_transform and
//! bicubic_filter8); license retained in docs/licenses/pillow.txt.
//! This interpolation polynomial is NOT the scale-adaptive resize kernel.

pub(crate) const MAX_GRAY_PIXELS: usize = 16 * 1024 * 1024;

pub(crate) fn validate_sizes(source: (usize, usize), output: (usize, usize)) -> Result<(), String> {
    for (w, h) in [source, output] {
        if w == 0
            || h == 0
            || w > 32767
            || h > 32767
            || w.checked_mul(h).is_none_or(|n| n > MAX_GRAY_PIXELS)
        {
            return Err("gray8 dimensions exceed raster limits".into());
        }
    }
    Ok(())
}

fn cubic(samples: [f64; 4], fraction: f64) -> f64 {
    let [a, b, c, d] = samples;
    // Keep the operation order: the final uint8 conversion truncates, so even an
    // algebraically equivalent polynomial can cross an integer boundary.
    let linear = -a + c;
    let quadratic = 2.0 * (a - b) + c - d;
    let cubic = -a + b - c + d;
    b + fraction * (linear + fraction * (quadratic + fraction * cubic))
}

fn sample(source: &[u8], size: (usize, usize), x: f64, y: f64) -> u8 {
    let (w, h) = size;
    if !x.is_finite() || !y.is_finite() || x < 0.0 || y < 0.0 || x >= w as f64 || y >= h as f64 {
        return 0;
    }
    let fx = x - 0.5;
    let fy = y - 0.5;
    let ix = fx.floor() as i32;
    let iy = fy.floor() as i32;
    let dx = fx - f64::from(ix);
    let dy = fy - f64::from(iy);
    let mut rows = [0.0; 4];
    for (row, value) in rows.iter_mut().enumerate() {
        let yy = (iy - 1 + row as i32).clamp(0, h as i32 - 1) as usize;
        let mut columns = [0.0; 4];
        for (column, value) in columns.iter_mut().enumerate() {
            let xx = (ix - 1 + column as i32).clamp(0, w as i32 - 1) as usize;
            *value = f64::from(source[yy * w + xx]);
        }
        *value = cubic(columns, dx);
    }
    cubic(rows, dy).clamp(0.0, 255.0) as u8
}

pub(crate) fn transform(
    source: &[u8],
    size: (usize, usize),
    output: (usize, usize),
    inverse: [f64; 6],
) -> Result<Vec<u8>, String> {
    validate_sizes(size, output)?;
    if source.len() != size.0 * size.1 {
        return Err("gray8 source length does not match dimensions".into());
    }
    if !inverse.iter().all(|v| v.is_finite()) {
        return Err("gray8 affine coefficients must be finite".into());
    }
    // Only the output is allocated, at most 16 MiB. The binding reserves an equal
    // Python bytes copy, while borrowing the immutable source across py.detach.
    let mut pixels = vec![0; output.0 * output.1];
    let [a, b, c, d, e, f] = inverse;
    for y in 0..output.1 {
        for x in 0..output.0 {
            let xx = x as f64 + 0.5;
            let yy = y as f64 + 0.5;
            pixels[y * output.0 + x] =
                sample(source, size, a * xx + b * yy + c, d * xx + e * yy + f);
        }
    }
    Ok(pixels)
}

#[cfg(test)]
mod tests {
    use super::*;

    #[test]
    fn identity_and_exterior_are_exact() {
        assert_eq!(
            transform(&[10, 20, 40, 80], (2, 2), (2, 2), [1., 0., 0., 0., 1., 0.]).unwrap(),
            [10, 20, 40, 80]
        );
        assert_eq!(
            transform(&[10, 20], (2, 1), (3, 1), [1., 0., -1., 0., 1., 0.]).unwrap(),
            [0, 10, 20]
        );
    }

    #[test]
    fn zero_mapping_and_half_pixel_edge_are_supported() {
        assert_eq!(
            transform(&[0, 255], (2, 1), (1, 1), [0., 0., 1., 0., 0., 0.5]).unwrap(),
            [127]
        );
        assert_eq!(
            transform(&[93], (1, 1), (2, 1), [1., 0., -0.5, 0., 0., 0.]).unwrap(),
            [93, 0]
        );
    }

    #[test]
    fn invalid_input_rejected_before_allocation() {
        assert!(
            transform(&[], (1, 1), (1, 1), [0.; 6])
                .unwrap_err()
                .contains("length")
        );
        assert!(
            transform(&[0], (1, 1), (1, 1), [f64::NAN; 6])
                .unwrap_err()
                .contains("finite")
        );
        assert!(
            transform(&[], (usize::MAX, 1), (1, 1), [0.; 6])
                .unwrap_err()
                .contains("limits")
        );
        assert!(
            transform(&[0], (1, 1), (8192, 8192), [0.; 6])
                .unwrap_err()
                .contains("limits")
        );
    }
}
