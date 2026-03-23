pub mod tap;
pub mod directional;
pub mod slide;
pub mod event;

use crate::fraction::Fraction;

pub use tap::{Tap, TapType};
pub use directional::{Directional, DirectionalType};
pub use slide::{Slide, SlideType};
pub use event::Event;

/// Index into the note arena
pub type NoteIdx = usize;

/// Sentinel value meaning "no note"
pub const NO_NOTE: NoteIdx = usize::MAX;

/// Common note fields shared by all note types
#[derive(Debug, Clone)]
pub struct NoteBase {
    pub bar: Fraction,
    pub lane: i32,
    pub width: i32,
    pub note_type: i32,
    pub speed: Option<f64>,
}

impl NoteBase {
    pub fn new(bar: Fraction, lane: i32, width: i32, note_type: i32) -> Self {
        NoteBase {
            bar,
            lane,
            width,
            note_type,
            speed: None,
        }
    }
}

/// A note stored in the arena. Uses enum for type discrimination
/// and indices for cross-references (avoiding Rc/RefCell).
#[derive(Debug, Clone)]
pub enum NoteData {
    Tap(NoteBase, Tap),
    Directional(NoteBase, Directional),
    Slide(NoteBase, Slide),
}

impl NoteData {
    pub fn base(&self) -> &NoteBase {
        match self {
            NoteData::Tap(b, _) => b,
            NoteData::Directional(b, _) => b,
            NoteData::Slide(b, _) => b,
        }
    }

    pub fn base_mut(&mut self) -> &mut NoteBase {
        match self {
            NoteData::Tap(b, _) => b,
            NoteData::Directional(b, _) => b,
            NoteData::Slide(b, _) => b,
        }
    }

    pub fn bar(&self) -> Fraction {
        self.base().bar
    }

    pub fn lane(&self) -> i32 {
        self.base().lane
    }

    pub fn width(&self) -> i32 {
        self.base().width
    }

    pub fn note_type(&self) -> i32 {
        self.base().note_type
    }

    pub fn speed(&self) -> Option<f64> {
        self.base().speed
    }

    pub fn is_tap(&self) -> bool {
        matches!(self, NoteData::Tap(..))
    }

    pub fn is_directional(&self) -> bool {
        matches!(self, NoteData::Directional(..))
    }

    pub fn is_slide(&self) -> bool {
        matches!(self, NoteData::Slide(..))
    }

    pub fn as_tap(&self) -> Option<&Tap> {
        if let NoteData::Tap(_, t) = self { Some(t) } else { None }
    }

    pub fn as_directional(&self) -> Option<&Directional> {
        if let NoteData::Directional(_, d) = self { Some(d) } else { None }
    }

    pub fn as_slide(&self) -> Option<&Slide> {
        if let NoteData::Slide(_, s) = self { Some(s) } else { None }
    }

    pub fn as_slide_mut(&mut self) -> Option<&mut Slide> {
        if let NoteData::Slide(_, s) = self { Some(s) } else { None }
    }

    pub fn as_directional_mut(&mut self) -> Option<&mut Directional> {
        if let NoteData::Directional(_, d) = self { Some(d) } else { None }
    }

    /// Check if this note is critical (delegates to type-specific logic)
    pub fn is_critical(&self, arena: &[NoteData]) -> bool {
        match self {
            NoteData::Tap(_, t) => t.is_critical(),
            NoteData::Directional(_, d) => d.is_critical(arena),
            NoteData::Slide(_, s) => s.is_critical(arena),
        }
    }

    /// Check if this note is a trend note
    pub fn is_trend(&self, arena: &[NoteData]) -> bool {
        match self {
            NoteData::Tap(_, t) => t.is_trend(),
            NoteData::Directional(_, d) => d.is_trend(arena),
            NoteData::Slide(_, s) => s.is_trend(arena),
        }
    }

    /// Check if this note should not be rendered
    pub fn is_none(&self, arena: &[NoteData]) -> bool {
        match self {
            NoteData::Tap(_, t) => t.is_none(),
            NoteData::Directional(_, _d) => false,
            NoteData::Slide(_, s) => s.is_none(arena),
        }
    }

    /// Check if this note should render a tick mark.
    /// Returns Some(true/false) or None if not applicable.
    pub fn is_tick(&self, arena: &[NoteData]) -> Option<bool> {
        match self {
            NoteData::Tap(_, t) => t.is_tick(),
            NoteData::Directional(_, d) => d.is_tick(arena),
            NoteData::Slide(b, s) => s.is_tick(arena, b.note_type),
        }
    }
}
