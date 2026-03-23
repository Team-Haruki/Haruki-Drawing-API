use super::{NoteData, NoteIdx, NO_NOTE};
use super::tap::Tap;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(i32)]
pub enum DirectionalType {
    Up = 1,
    Down = 2,
    UpperLeft = 3,
    UpperRight = 4,
    LowerLeft = 5,
    LowerRight = 6,
}

impl DirectionalType {
    pub fn from_i32(v: i32) -> Option<DirectionalType> {
        match v {
            1 => Some(DirectionalType::Up),
            2 => Some(DirectionalType::Down),
            3 => Some(DirectionalType::UpperLeft),
            4 => Some(DirectionalType::UpperRight),
            5 => Some(DirectionalType::LowerLeft),
            6 => Some(DirectionalType::LowerRight),
            _ => None,
        }
    }
}

#[derive(Debug, Clone)]
pub struct Directional {
    pub tap_idx: NoteIdx,
}

impl Directional {
    pub fn new() -> Self {
        Directional { tap_idx: NO_NOTE }
    }

    pub fn is_critical(&self, arena: &[NoteData]) -> bool {
        if self.tap_idx != NO_NOTE {
            let tap_note = &arena[self.tap_idx];
            return Tap::is_critical_type(tap_note.note_type());
        }
        false
    }

    pub fn is_trend(&self, arena: &[NoteData]) -> bool {
        if self.tap_idx != NO_NOTE {
            let tap_note = &arena[self.tap_idx];
            return Tap::is_trend_type(tap_note.note_type());
        }
        false
    }

    pub fn is_tick(&self, arena: &[NoteData]) -> Option<bool> {
        if self.is_none_inner(arena) {
            return None;
        }
        if self.is_trend(arena) {
            return Some(false);
        }
        Some(true)
    }

    fn is_none_inner(&self, _arena: &[NoteData]) -> bool {
        false
    }
}
