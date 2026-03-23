/// Tap note types matching Python's TapType IntEnum
#[derive(Debug, Clone, Copy, PartialEq, Eq)]
#[repr(i32)]
pub enum TapType {
    Tap = 1,
    Critical = 2,
    Flick = 3,
    Damage = 4,
    Trend = 5,
    CriticalTrend = 6,
    Cancel = 7,
    CriticalCancel = 8,
}

impl TapType {
    pub fn from_i32(v: i32) -> Option<TapType> {
        match v {
            1 => Some(TapType::Tap),
            2 => Some(TapType::Critical),
            3 => Some(TapType::Flick),
            4 => Some(TapType::Damage),
            5 => Some(TapType::Trend),
            6 => Some(TapType::CriticalTrend),
            7 => Some(TapType::Cancel),
            8 => Some(TapType::CriticalCancel),
            _ => None,
        }
    }
}

#[derive(Debug, Clone)]
pub struct Tap;

impl Tap {
    pub fn is_critical_type(note_type: i32) -> bool {
        matches!(
            TapType::from_i32(note_type),
            Some(TapType::Critical | TapType::CriticalTrend | TapType::CriticalCancel)
        )
    }

    pub fn is_trend_type(note_type: i32) -> bool {
        matches!(
            TapType::from_i32(note_type),
            Some(TapType::Trend | TapType::CriticalTrend)
        )
    }

    pub fn is_none_type(note_type: i32) -> bool {
        matches!(
            TapType::from_i32(note_type),
            Some(TapType::Cancel | TapType::CriticalCancel)
        )
    }
}

impl Tap {
    pub fn is_critical(&self) -> bool {
        false // Checked via note_type on NoteBase
    }

    pub fn is_trend(&self) -> bool {
        false
    }

    pub fn is_none(&self) -> bool {
        false
    }

    pub fn is_tick(&self) -> Option<bool> {
        Some(true)
    }

    pub fn is_critical_with_type(&self, note_type: i32) -> bool {
        Self::is_critical_type(note_type)
    }

    pub fn is_trend_with_type(&self, note_type: i32) -> bool {
        Self::is_trend_type(note_type)
    }

    pub fn is_none_with_type(&self, note_type: i32) -> bool {
        Self::is_none_type(note_type)
    }

    pub fn is_tick_with_type(&self, note_type: i32) -> Option<bool> {
        if Self::is_none_type(note_type) {
            return None;
        }
        if Self::is_trend_type(note_type) {
            return Some(false);
        }
        Some(true)
    }
}
