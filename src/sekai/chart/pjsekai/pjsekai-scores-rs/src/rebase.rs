use serde_json::Value;

use crate::fraction::Fraction;
use crate::meta::Meta;
use crate::notes::event::Event;
use crate::notes::slide::Slide;
use crate::notes::tap::Tap;
use crate::notes::directional::Directional;
use crate::notes::{NoteBase, NoteData, NO_NOTE};
use crate::score::Score;

fn bar_to_hash(bar: Fraction) -> u64 {
    let bits = bar.to_f64().to_bits();
    bits
}

/// Rebase transformation: applies custom timing/BPM adjustments to a score
pub struct Rebase {
    pub offset: f64,
    pub events: Vec<Event>,
    pub meta: Meta,
}

impl Rebase {
    /// Load from a JSON string
    pub fn from_json(json_str: &str) -> Result<Rebase, serde_json::Error> {
        let v: Value = serde_json::from_str(json_str)?;
        Ok(Self::from_value(&v))
    }

    /// Load from a serde_json::Value
    pub fn from_value(v: &Value) -> Rebase {
        let offset = v.get("offset").and_then(|v| v.as_f64()).unwrap_or(0.0);

        let events: Vec<Event> = v
            .get("events")
            .and_then(|v| v.as_array())
            .map(|arr| {
                arr.iter()
                    .map(|ev| {
                        let mut event = Event::new(Fraction::from_f64(
                            ev.get("bar").and_then(|v| v.as_f64()).unwrap_or(0.0),
                        ));
                        if let Some(bpm) = ev.get("bpm").and_then(|v| v.as_f64()) {
                            event.bpm = Some(Fraction::from_f64(bpm));
                        }
                        if let Some(bl) = ev.get("barLength").and_then(|v| v.as_f64()) {
                            event.bar_length = Some(Fraction::from_f64(bl));
                        }
                        if let Some(sl) = ev.get("sentenceLength").and_then(|v| v.as_i64()) {
                            event.sentence_length = Some(sl as i32);
                        }
                        if let Some(section) = ev.get("section").and_then(|v| v.as_str()) {
                            event.section = Some(section.to_string());
                        }
                        if let Some(text) = ev.get("text").and_then(|v| v.as_str()) {
                            event.text = Some(text.to_string());
                        }
                        event
                    })
                    .collect()
            })
            .unwrap_or_default();

        let mut meta = Meta::new();
        if let Some(meta_obj) = v.get("meta").and_then(|v| v.as_object()) {
            for (k, val) in meta_obj {
                if let Some(s) = val.as_str() {
                    meta.set_field(k, s);
                } else if let Some(f) = val.as_f64() {
                    meta.set_field(k, &f.to_string());
                }
            }
        }

        Rebase {
            offset,
            events,
            meta,
        }
    }

    /// Apply rebase transformation to a score, producing a new score
    pub fn apply(&self, source: &mut Score) -> Score {
        let mut score = Score::new();
        score.meta = source.meta.merge(&self.meta);
        score.events = self.events.clone();

        // Clone out source data to avoid borrow issues
        let active_notes = source.active_notes.clone();
        let notes_snapshot = source.notes.clone();

        // Pre-compute all source times we need
        let mut bar_to_time: std::collections::HashMap<u64, f64> = std::collections::HashMap::new();
        for &note_idx in &active_notes {
            let note = &notes_snapshot[note_idx];
            let bar = note.bar();
            let key = bar_to_hash(bar);
            if !bar_to_time.contains_key(&key) {
                bar_to_time.insert(key, source.get_time(bar));
            }
            // Also handle linked notes
            match note {
                NoteData::Directional(_, dir) => {
                    if dir.tap_idx != NO_NOTE {
                        let tb = notes_snapshot[dir.tap_idx].bar();
                        let k = bar_to_hash(tb);
                        if !bar_to_time.contains_key(&k) {
                            bar_to_time.insert(k, source.get_time(tb));
                        }
                    }
                }
                NoteData::Slide(_, slide) => {
                    if slide.tap_idx != NO_NOTE {
                        let tb = notes_snapshot[slide.tap_idx].bar();
                        let k = bar_to_hash(tb);
                        if !bar_to_time.contains_key(&k) {
                            bar_to_time.insert(k, source.get_time(tb));
                        }
                    }
                    if slide.directional_idx != NO_NOTE {
                        let db = notes_snapshot[slide.directional_idx].bar();
                        let k = bar_to_hash(db);
                        if !bar_to_time.contains_key(&k) {
                            bar_to_time.insert(k, source.get_time(db));
                        }
                        if let Some(d) = notes_snapshot[slide.directional_idx].as_directional() {
                            if d.tap_idx != NO_NOTE && d.tap_idx != slide.tap_idx {
                                let dtb = notes_snapshot[d.tap_idx].bar();
                                let k = bar_to_hash(dtb);
                                if !bar_to_time.contains_key(&k) {
                                    bar_to_time.insert(k, source.get_time(dtb));
                                }
                            }
                        }
                    }
                }
                _ => {}
            }
        }

        let rebase_bar = |bar: Fraction, score: &mut Score| -> Fraction {
            let source_time = bar_to_time.get(&bar_to_hash(bar)).copied().unwrap_or(0.0);
            score.get_bar_by_time(source_time - self.offset)
        };

        // Rebase each note
        for &note_idx in &active_notes {
            let note = &notes_snapshot[note_idx];
            match note {
                NoteData::Tap(base, _tap) => {
                    let new_bar = rebase_bar(base.bar, &mut score);
                    score.notes.push(NoteData::Tap(
                        NoteBase::new(new_bar, base.lane, base.width, base.note_type),
                        Tap,
                    ));
                }
                NoteData::Directional(base, dir) => {
                    let new_bar = rebase_bar(base.bar, &mut score);
                    score.notes.push(NoteData::Directional(
                        NoteBase::new(new_bar, base.lane, base.width, base.note_type),
                        Directional::new(),
                    ));
                    if dir.tap_idx != NO_NOTE {
                        let tap_base = notes_snapshot[dir.tap_idx].base();
                        let tap_bar = rebase_bar(tap_base.bar, &mut score);
                        score.notes.push(NoteData::Tap(
                            NoteBase::new(tap_bar, tap_base.lane, tap_base.width, tap_base.note_type),
                            Tap,
                        ));
                    }
                }
                NoteData::Slide(base, slide) => {
                    let new_bar = rebase_bar(base.bar, &mut score);
                    score.notes.push(NoteData::Slide(
                        NoteBase::new(new_bar, base.lane, base.width, base.note_type),
                        Slide::new(slide.channel, slide.decoration),
                    ));
                    if slide.tap_idx != NO_NOTE {
                        let tap_base = notes_snapshot[slide.tap_idx].base();
                        let tap_bar = rebase_bar(tap_base.bar, &mut score);
                        score.notes.push(NoteData::Tap(
                            NoteBase::new(tap_bar, tap_base.lane, tap_base.width, tap_base.note_type),
                            Tap,
                        ));
                    }
                    if slide.directional_idx != NO_NOTE {
                        let dir_base = notes_snapshot[slide.directional_idx].base();
                        let dir_bar = rebase_bar(dir_base.bar, &mut score);
                        score.notes.push(NoteData::Directional(
                            NoteBase::new(dir_bar, dir_base.lane, dir_base.width, dir_base.note_type),
                            Directional::new(),
                        ));
                        if let Some(d) = notes_snapshot[slide.directional_idx].as_directional() {
                            if d.tap_idx != NO_NOTE && d.tap_idx != slide.tap_idx {
                                let dt_base = notes_snapshot[d.tap_idx].base();
                                let dt_bar = rebase_bar(dt_base.bar, &mut score);
                                score.notes.push(NoteData::Tap(
                                    NoteBase::new(dt_bar, dt_base.lane, dt_base.width, dt_base.note_type),
                                    Tap,
                                ));
                            }
                        }
                    }
                }
            }
        }

        // Rebase speed/text events from source
        let source_events = source.events.clone();
        for event in &source_events {
            if event.speed.is_some() || event.text.is_some() {
                let source_time = source.get_time(event.bar);
                let new_bar = score.get_bar_by_time(source_time - self.offset);
                let mut new_event = event.clone();
                new_event.bar = new_bar;
                score.events.push(new_event);
            }
        }
        score.events.sort_by(|a, b| a.bar.partial_cmp(&b.bar).unwrap_or(std::cmp::Ordering::Equal));

        // Sort notes and re-link
        score.notes.sort_by(|a, b| a.bar().partial_cmp(&b.bar()).unwrap_or(std::cmp::Ordering::Equal));
        score.init_notes();
        score.init_events();

        score
    }
}
