use super::{NoteData, NoteIdx, NO_NOTE};
use super::tap::Tap;

#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(i32)]
pub enum SlideType {
    Start = 1,
    End = 2,
    Relay = 3,
    Invisible = 5,
}

impl SlideType {
    pub fn from_i32(v: i32) -> Option<SlideType> {
        match v {
            1 => Some(SlideType::Start),
            2 => Some(SlideType::End),
            3 => Some(SlideType::Relay),
            5 => Some(SlideType::Invisible),
            _ => None,
        }
    }
}

#[derive(Debug, Clone)]
pub struct Slide {
    pub channel: i32,
    pub decoration: bool,
    pub tap_idx: NoteIdx,
    pub directional_idx: NoteIdx,
    pub next_idx: NoteIdx,
    pub head_idx: NoteIdx,
}

impl Slide {
    pub fn new(channel: i32, decoration: bool) -> Self {
        Slide {
            channel,
            decoration,
            tap_idx: NO_NOTE,
            directional_idx: NO_NOTE,
            next_idx: NO_NOTE,
            head_idx: NO_NOTE,
        }
    }

    pub fn is_path(&self, note_type: i32) -> bool {
        if note_type == 0 {
            return false;
        }
        if note_type != 3 {
            return true;
        }
        // type == 3 (RELAY)
        if self.directional_idx != NO_NOTE {
            return true;
        }
        if self.tap_idx == NO_NOTE && self.directional_idx == NO_NOTE {
            return true;
        }
        false
    }

    pub fn is_critical(&self, arena: &[NoteData]) -> bool {
        if self.tap_idx != NO_NOTE {
            if Tap::is_critical_type(arena[self.tap_idx].note_type()) {
                return true;
            }
        }
        if self.directional_idx != NO_NOTE {
            if let Some(d) = arena[self.directional_idx].as_directional() {
                if d.is_critical(arena) {
                    return true;
                }
            }
        }
        if self.head_idx != NO_NOTE {
            // Check head's critical status (but avoid infinite recursion for self-referencing head)
            let head = &arena[self.head_idx];
            if let Some(hs) = head.as_slide() {
                if hs.tap_idx != NO_NOTE && Tap::is_critical_type(arena[hs.tap_idx].note_type()) {
                    return true;
                }
                if hs.directional_idx != NO_NOTE {
                    if let Some(d) = arena[hs.directional_idx].as_directional() {
                        if d.is_critical(arena) {
                            return true;
                        }
                    }
                }
            }
        }
        false
    }

    pub fn is_trend(&self, arena: &[NoteData]) -> bool {
        if self.tap_idx != NO_NOTE {
            if Tap::is_trend_type(arena[self.tap_idx].note_type()) {
                return true;
            }
        }
        if self.directional_idx != NO_NOTE {
            if let Some(d) = arena[self.directional_idx].as_directional() {
                if d.is_trend(arena) {
                    return true;
                }
            }
        }
        false
    }

    pub fn is_none(&self, arena: &[NoteData]) -> bool {
        if self.tap_idx != NO_NOTE {
            if Tap::is_none_type(arena[self.tap_idx].note_type()) {
                return true;
            }
        }
        if self.directional_idx != NO_NOTE {
            // Directional.is_none() is always false in Python
            return false;
        }
        false
    }

    pub fn is_tick(&self, arena: &[NoteData], note_type: i32) -> Option<bool> {
        if self.is_none(arena) {
            return None;
        }
        if self.decoration {
            if self.tap_idx != NO_NOTE {
                let tap_tick = Tap::is_tick_with_type(&Tap, arena[self.tap_idx].note_type());
                if let Some(true) = tap_tick {
                    return tap_tick;
                }
            }
            if self.directional_idx != NO_NOTE {
                if let Some(d) = arena[self.directional_idx].as_directional() {
                    let d_tick = d.is_tick(arena);
                    if let Some(true) = d_tick {
                        return d_tick;
                    }
                }
            }
            // Check if either returned a non-None value
            let tap_is_some = self.tap_idx != NO_NOTE;
            let dir_is_some = self.directional_idx != NO_NOTE;
            if tap_is_some || dir_is_some {
                return Some(false);
            }
            return None;
        }

        if matches!(SlideType::from_i32(note_type), Some(SlideType::Invisible)) {
            return None;
        }
        if self.is_trend(arena) {
            return Some(false);
        }
        if matches!(SlideType::from_i32(note_type), Some(SlideType::Relay)) {
            return Some(false);
        }

        Some(true)
    }
}
