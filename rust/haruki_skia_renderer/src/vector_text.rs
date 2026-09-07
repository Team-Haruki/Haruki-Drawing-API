//! FreeType chart text geometry. No raster or Python image crosses this boundary.
use std::ffi::c_void;

use crate::text_metrics::{TextMetricsRequest, validate_text_metrics_requests};
use freetype::{
    face::{KerningMode, LoadFlag},
    ffi,
};

pub(crate) struct Geometry {
    pub commands: Vec<(&'static str, Vec<f64>)>,
    pub advance: f64,
    pub ascent: f64,
    pub descent: f64,
    pub ink_bbox: [f64; 4],
}

struct Decomposer {
    commands: Vec<(&'static str, Vec<f64>)>,
    pen: f64,
    open: bool,
}
impl Decomposer {
    fn add(&mut self, op: &'static str, points: &[ffi::FT_Vector]) -> i32 {
        if self.commands.len() >= 16384 {
            return 1;
        }
        self.commands.push((
            op,
            points
                .iter()
                .flat_map(|p| [self.pen + p.x as f64 / 64.0, -(p.y as f64) / 64.0])
                .collect(),
        ));
        0
    }
    fn close(&mut self) -> i32 {
        if self.open {
            self.open = false;
            self.add("close", &[])
        } else {
            0
        }
    }
}
// FreeType invokes these synchronously with pointers to live outline vectors
// and the exclusive stack-owned Decomposer passed to FT_Outline_Decompose.
extern "C" fn move_to(p: *const ffi::FT_Vector, state: *mut c_void) -> i32 {
    let state = unsafe { &mut *(state as *mut Decomposer) };
    if state.close() != 0 {
        return 1;
    }
    state.open = true;
    state.add("move", &[unsafe { *p }])
}
extern "C" fn line_to(p: *const ffi::FT_Vector, state: *mut c_void) -> i32 {
    unsafe { &mut *(state as *mut Decomposer) }.add("line", &[unsafe { *p }])
}
extern "C" fn quad_to(
    a: *const ffi::FT_Vector,
    b: *const ffi::FT_Vector,
    state: *mut c_void,
) -> i32 {
    unsafe { &mut *(state as *mut Decomposer) }.add("quad", &[unsafe { *a }, unsafe { *b }])
}
extern "C" fn cubic_to(
    a: *const ffi::FT_Vector,
    b: *const ffi::FT_Vector,
    c: *const ffi::FT_Vector,
    state: *mut c_void,
) -> i32 {
    unsafe { &mut *(state as *mut Decomposer) }
        .add("cubic", &[unsafe { *a }, unsafe { *b }, unsafe { *c }])
}

pub(crate) fn geometry(dir: &str, name: &str, text: &str, size: f64) -> Result<Geometry, String> {
    validate_text_metrics_requests(
        dir,
        name,
        &[TextMetricsRequest {
            text: text.into(),
            size: size as f32,
        }],
    )?;
    crate::basic_text::with_face(dir, name, size as f32, |face| {
        // Match the legacy figure: 100 dpi, with the pixel size expressed as points.
        face.set_char_size((size * 0.72 * 64.0).round() as isize, 0, 100, 100)
            .map_err(|e| e.to_string())?;
        let funcs = ffi::FT_Outline_Funcs {
            move_to,
            line_to,
            conic_to: quad_to,
            cubic_to,
            shift: 0,
            delta: 0,
        };
        let mut state = Decomposer {
            commands: Vec::new(),
            pen: 0.0,
            open: false,
        };
        let mut bounds = [0.0_f64; 4];
        let mut previous = 0;
        for ch in text.chars() {
            let index = face.get_char_index(ch as usize).unwrap_or(0);
            if previous != 0 && index != 0 && face.has_kerning() {
                state.pen += face
                    .get_kerning(previous, index, KerningMode::KerningDefault)
                    .map_err(|e| e.to_string())?
                    .x as f64
                    / 64.0;
            }
            face.load_glyph(index, LoadFlag::DEFAULT)
                .map_err(|e| e.to_string())?;
            let slot = face.glyph();
            let bbox = slot
                .get_glyph()
                .map_err(|e| e.to_string())?
                .get_cbox(ffi::FT_GLYPH_BBOX_SUBPIXELS);
            bounds[0] = bounds[0].min(state.pen + bbox.xMin as f64 / 64.0);
            bounds[1] = bounds[1].min(-(bbox.yMax as f64) / 64.0);
            bounds[2] = bounds[2].max(state.pen + bbox.xMax as f64 / 64.0);
            bounds[3] = bounds[3].max(-(bbox.yMin as f64) / 64.0);
            if slot.outline().is_none() {
                return Err("vector text requires outline glyphs".into());
            }
            // FT_Outline_Decompose borrows the glyph's points and does not change
            // their storage. A copy of the descriptor keeps the Face borrow intact.
            let source = &slot.raw().outline;
            let mut outline = ffi::FT_Outline {
                n_contours: source.n_contours,
                n_points: source.n_points,
                points: source.points,
                tags: source.tags,
                contours: source.contours,
                flags: source.flags,
            };
            let error = unsafe {
                ffi::FT_Outline_Decompose(&mut outline, &funcs, &mut state as *mut _ as *mut c_void)
            };
            if error != 0 || state.close() != 0 {
                return Err("vector text exceeds command limit or has invalid outline".into());
            }
            state.pen += slot.advance().x as f64 / 64.0;
            previous = index;
        }
        let units = face.em_size() as f64;
        if units <= 0.0 {
            return Err("vector font has invalid units per em".into());
        }
        let mut ascent = face.ascender() as f64 * size / units;
        let mut descent = -(face.descender() as f64) * size / units;
        // FreeType owns this optional SFNT table for the lifetime of the face.
        let table = unsafe {
            ffi::FT_Get_Sfnt_Table(face.raw() as *const _ as ffi::FT_Face, ffi::FT_SFNT_OS2)
                as *const ffi::TT_OS2
        };
        if !table.is_null() {
            let table = unsafe { &*table };
            ascent = table.sTypoAscender as f64 * size / units;
            descent = -(table.sTypoDescender as f64) * size / units;
        }
        Ok(Geometry {
            commands: state.commands,
            advance: state.pen,
            ascent: ascent.max(-bounds[1]),
            descent: descent.max(bounds[3]),
            ink_bbox: bounds,
        })
    })
}
