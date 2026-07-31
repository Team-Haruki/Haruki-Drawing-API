//! Bounded, standalone text measurement for native custom-profile layout.
//!
//! The GeneralContentView display-list builder needs font measurements before a Render IR
//! scene exists.  This module deliberately uses the same Skia `Font` profile as the IR text
//! renderer so layout and rasterization agree without asking Pillow to load or measure a font.

use std::sync::OnceLock;

use skia_safe::{Font, FontHinting, Typeface};

use crate::load_typeface_checked;

pub(crate) const MAX_TEXT_METRICS_BATCH: usize = 1_024;
pub(crate) const MAX_TEXT_METRICS_CHARS: usize = 4_096;
pub(crate) const MAX_TEXT_METRICS_TOTAL_CHARS: usize = 65_536;
pub(crate) const MAX_TEXT_METRICS_FONT_PATH_BYTES: usize = 4_096;
pub(crate) const MAX_TEXT_METRICS_SIZE: f32 = 2_048.0;

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct TextMetricsRequest {
    pub(crate) text: String,
    pub(crate) size: f32,
}

#[derive(Clone, Debug, PartialEq)]
pub(crate) struct TextMetricsResult {
    /// Horizontal cursor advance in Skia device units.
    pub(crate) advance: f32,
    /// Glyph ink bounds relative to the alphabetic baseline.
    pub(crate) ink_bbox: [f32; 4],
    /// The same ink bounds translated to Pillow's default horizontal-text origin (`la`):
    /// `(0, 0)` is the left ascender anchor, so the alphabetic baseline is at `y = ascent`.
    pub(crate) pillow_bbox: [f32; 4],
    /// Positive distance from the alphabetic baseline to the font ascender.
    pub(crate) ascent: f32,
    /// Positive distance from the alphabetic baseline to the font descender.
    pub(crate) descent: f32,
    pub(crate) leading: f32,
    pub(crate) line_spacing: f32,
    /// Raw Skia font extrema, relative to the alphabetic baseline.
    pub(crate) font_top: f32,
    pub(crate) font_bottom: f32,
    pub(crate) cap_height: f32,
    pub(crate) x_height: f32,
}

#[derive(Clone, Copy)]
struct TextFontProfile {
    hinting: FontHinting,
    force_auto_hinting: bool,
    linear_metrics: bool,
}

static TEXT_FONT_PROFILE: OnceLock<TextFontProfile> = OnceLock::new();

fn default_text_font_profile() -> TextFontProfile {
    if cfg!(target_os = "linux") {
        TextFontProfile {
            hinting: FontHinting::Slight,
            force_auto_hinting: false,
            linear_metrics: false,
        }
    } else {
        TextFontProfile {
            hinting: FontHinting::Normal,
            force_auto_hinting: false,
            linear_metrics: false,
        }
    }
}

/// Build the same configured font used by `interp`'s IR Text nodes.
///
/// This is kept in a standalone module because text metrics are needed before a scene exists.
/// The IR interpreter should call this shared helper once its in-flight node changes settle.
pub(crate) fn configured_text_font(typeface: Typeface, size: f32) -> Font {
    let profile = TEXT_FONT_PROFILE.get_or_init(|| {
        let default = default_text_font_profile();
        match std::env::var("HARUKI_SKIA_TEXT_HINTING")
            .ok()
            .map(|name| name.to_ascii_lowercase())
            .as_deref()
        {
            Some("none") => TextFontProfile {
                hinting: FontHinting::None,
                force_auto_hinting: false,
                linear_metrics: false,
            },
            Some("slight") => TextFontProfile {
                hinting: FontHinting::Slight,
                force_auto_hinting: false,
                linear_metrics: false,
            },
            Some("full") => TextFontProfile {
                hinting: FontHinting::Full,
                force_auto_hinting: false,
                linear_metrics: false,
            },
            Some("auto") => TextFontProfile {
                hinting: FontHinting::Full,
                force_auto_hinting: true,
                linear_metrics: false,
            },
            Some("linear") => TextFontProfile {
                hinting: FontHinting::Normal,
                force_auto_hinting: false,
                linear_metrics: true,
            },
            Some("normal") => TextFontProfile {
                hinting: FontHinting::Normal,
                force_auto_hinting: false,
                linear_metrics: false,
            },
            _ => default,
        }
    });
    let mut font = Font::from_typeface(typeface, size);
    font.set_hinting(profile.hinting)
        .set_force_auto_hinting(profile.force_auto_hinting)
        .set_linear_metrics(profile.linear_metrics);
    font
}

pub(crate) fn validate_text_metrics_requests(
    font_dir: &str,
    font_name: &str,
    requests: &[TextMetricsRequest],
) -> Result<(), String> {
    if font_dir.len() > MAX_TEXT_METRICS_FONT_PATH_BYTES {
        return Err(format!(
            "font directory exceeds {MAX_TEXT_METRICS_FONT_PATH_BYTES} UTF-8 bytes"
        ));
    }
    if font_name.is_empty() {
        return Err("font name/path must not be empty".to_string());
    }
    if font_name.len() > MAX_TEXT_METRICS_FONT_PATH_BYTES {
        return Err(format!(
            "font name/path exceeds {MAX_TEXT_METRICS_FONT_PATH_BYTES} UTF-8 bytes"
        ));
    }
    if requests.len() > MAX_TEXT_METRICS_BATCH {
        return Err(format!(
            "text metrics batch has {} entries; maximum is {MAX_TEXT_METRICS_BATCH}",
            requests.len()
        ));
    }

    let mut total_chars = 0_usize;
    for (index, request) in requests.iter().enumerate() {
        if !request.size.is_finite() || request.size <= 0.0 || request.size > MAX_TEXT_METRICS_SIZE
        {
            return Err(format!(
                "text metrics request {index} has invalid font size {}; expected 0 < size <= \
                 {MAX_TEXT_METRICS_SIZE}",
                request.size
            ));
        }
        let chars = request.text.chars().count();
        if chars > MAX_TEXT_METRICS_CHARS {
            return Err(format!(
                "text metrics request {index} has {chars} characters; maximum is \
                 {MAX_TEXT_METRICS_CHARS}"
            ));
        }
        total_chars = total_chars
            .checked_add(chars)
            .ok_or_else(|| "text metrics aggregate character count overflow".to_string())?;
        if total_chars > MAX_TEXT_METRICS_TOTAL_CHARS {
            return Err(format!(
                "text metrics batch has {total_chars} total characters; maximum is \
                 {MAX_TEXT_METRICS_TOTAL_CHARS}"
            ));
        }
    }
    Ok(())
}

fn measure_loaded_text_batch(
    typeface: &Typeface,
    requests: &[TextMetricsRequest],
) -> Vec<TextMetricsResult> {
    requests
        .iter()
        .map(|request| {
            let font = configured_text_font(typeface.clone(), request.size);
            let (advance, ink) = font.measure_str(&request.text, None);
            let (line_spacing, metrics) = font.metrics();
            // Skia stores ascender as a negative baseline-relative y.  The public API follows
            // Pillow/FreeType convention and exposes positive distances instead.
            let ascent = -metrics.ascent;
            let descent = metrics.descent;
            TextMetricsResult {
                advance,
                ink_bbox: [ink.left, ink.top, ink.right, ink.bottom],
                pillow_bbox: [ink.left, ink.top + ascent, ink.right, ink.bottom + ascent],
                ascent,
                descent,
                leading: metrics.leading,
                line_spacing,
                font_top: metrics.top,
                font_bottom: metrics.bottom,
                cap_height: metrics.cap_height,
                x_height: metrics.x_height,
            }
        })
        .collect()
}

pub(crate) fn measure_text_metrics_batch(
    font_dir: &str,
    font_name: &str,
    requests: &[TextMetricsRequest],
) -> Result<Vec<TextMetricsResult>, String> {
    validate_text_metrics_requests(font_dir, font_name, requests)?;
    let (typeface, fell_back) = load_typeface_checked(font_dir, font_name);
    if fell_back {
        return Err(format!(
            "font could not be resolved without fallback: name={font_name:?} dir={font_dir:?}"
        ));
    }
    Ok(measure_loaded_text_batch(&typeface, requests))
}

#[cfg(test)]
mod tests {
    use skia_safe::{FontMgr, FontStyle};

    use super::*;

    fn default_typeface() -> Typeface {
        let manager = FontMgr::default();
        manager
            .match_family_style("sans-serif", FontStyle::normal())
            .or_else(|| manager.legacy_make_typeface(None, FontStyle::normal()))
            .expect("Skia must expose a default test typeface")
    }

    #[test]
    fn loaded_batch_reports_baseline_relative_bounds_and_pillow_translation() {
        let requests = vec![
            TextMetricsRequest {
                text: "Haruki".to_string(),
                size: 32.0,
            },
            TextMetricsRequest {
                text: "未来".to_string(),
                size: 24.0,
            },
        ];
        let measured = measure_loaded_text_batch(&default_typeface(), &requests);
        assert_eq!(measured.len(), 2);
        for result in measured {
            assert!(result.advance > 0.0);
            assert!(result.ascent > 0.0);
            assert!(result.descent >= 0.0);
            assert_eq!(result.pillow_bbox[0], result.ink_bbox[0]);
            assert_eq!(result.pillow_bbox[2], result.ink_bbox[2]);
            assert_eq!(result.pillow_bbox[1], result.ink_bbox[1] + result.ascent);
            assert_eq!(result.pillow_bbox[3], result.ink_bbox[3] + result.ascent);
        }
    }

    #[test]
    fn validation_rejects_oversized_or_invalid_requests() {
        let valid = TextMetricsRequest {
            text: "ok".to_string(),
            size: 16.0,
        };
        assert!(validate_text_metrics_requests("", "font.ttf", &[valid.clone()]).is_ok());

        let too_many = vec![valid.clone(); MAX_TEXT_METRICS_BATCH + 1];
        assert!(validate_text_metrics_requests("", "font.ttf", &too_many).is_err());
        let too_long = TextMetricsRequest {
            text: "x".repeat(MAX_TEXT_METRICS_CHARS + 1),
            size: 16.0,
        };
        assert!(validate_text_metrics_requests("", "font.ttf", &[too_long]).is_err());
        for size in [
            0.0,
            -1.0,
            f32::NAN,
            f32::INFINITY,
            MAX_TEXT_METRICS_SIZE + 1.0,
        ] {
            let invalid = TextMetricsRequest {
                text: "x".to_string(),
                size,
            };
            assert!(validate_text_metrics_requests("", "font.ttf", &[invalid]).is_err());
        }
    }

    #[test]
    fn strict_batch_rejects_a_missing_font_instead_of_measuring_fallback() {
        let request = TextMetricsRequest {
            text: "must not use sans-serif".to_string(),
            size: 20.0,
        };
        let error = measure_text_metrics_batch(
            "/definitely/missing/haruki-font-root",
            "definitely-missing-haruki-font.ttf",
            &[request],
        )
        .expect_err("missing fonts are not valid for layout metrics");
        assert!(error.contains("without fallback"), "{error}");
    }
}
