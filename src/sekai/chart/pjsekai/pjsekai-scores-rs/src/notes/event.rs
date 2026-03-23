use crate::fraction::Fraction;

/// An event in the score timeline (BPM changes, bar length, sections, etc.)
#[derive(Debug, Clone)]
pub struct Event {
    pub bar: Fraction,
    pub bpm: Option<Fraction>,
    pub bar_length: Option<Fraction>,
    pub sentence_length: Option<i32>,
    pub speed: Option<f64>,
    pub section: Option<String>,
    pub text: Option<String>,
}

impl Event {
    pub fn new(bar: Fraction) -> Self {
        Event {
            bar,
            bpm: None,
            bar_length: None,
            sentence_length: None,
            speed: None,
            section: None,
            text: None,
        }
    }

    pub fn with_bpm(mut self, bpm: Fraction) -> Self {
        self.bpm = Some(bpm);
        self
    }

    pub fn with_bar_length(mut self, bar_length: Fraction) -> Self {
        self.bar_length = Some(bar_length);
        self
    }

    pub fn with_sentence_length(mut self, sl: i32) -> Self {
        self.sentence_length = Some(sl);
        self
    }

    pub fn with_speed(mut self, speed: f64) -> Self {
        self.speed = Some(speed);
        self
    }

    pub fn with_section(mut self, section: String) -> Self {
        self.section = Some(section);
        self
    }

    pub fn with_text(mut self, text: String) -> Self {
        self.text = Some(text);
        self
    }

    /// Merge operator matching Python's `__or__`: prefer other's non-None values
    pub fn merge(&self, other: &Event) -> Event {
        Event {
            bar: other.bar,
            bpm: other.bpm.or(self.bpm),
            bar_length: other.bar_length.or(self.bar_length),
            sentence_length: other.sentence_length.or(self.sentence_length),
            speed: other.speed.or(self.speed),
            section: other.section.clone().or_else(|| self.section.clone()),
            text: other.text.clone().or_else(|| self.text.clone()),
        }
    }

    /// Merge in-place: update self with non-None values from other
    pub fn merge_from(&mut self, other: &Event) {
        self.bar = other.bar;
        if other.bpm.is_some() {
            self.bpm = other.bpm;
        }
        if other.bar_length.is_some() {
            self.bar_length = other.bar_length;
        }
        if other.sentence_length.is_some() {
            self.sentence_length = other.sentence_length;
        }
        if other.speed.is_some() {
            self.speed = other.speed;
        }
        if other.section.is_some() {
            self.section = other.section.clone();
        }
        if other.text.is_some() {
            self.text = other.text.clone();
        }
    }
}
