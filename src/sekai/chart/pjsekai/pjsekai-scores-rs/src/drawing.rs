use std::fmt::Write;

use crate::fraction::Fraction;
use crate::lyric::Lyric;
use crate::notes::directional::DirectionalType;
use crate::notes::event::Event;
use crate::notes::slide::SlideType;
use crate::notes::{NoteData, NoteIdx, NO_NOTE};
use crate::score::Score;

const DEFAULT_CSS: &str = include_str!("../css/default.css");

/// Cover object for skill/fever overlays
#[derive(Debug, Clone)]
pub enum CoverObject {
    Rect {
        bar_from: Fraction,
        css_class: String,
        bar_to: Fraction,
    },
    Text {
        bar_from: Fraction,
        css_class: String,
        text: String,
    },
}

/// Configuration for the drawing
pub struct DrawingConfig {
    pub n_lanes: i32,
    pub lane_width: i32,
    pub time_height: f64,
    pub note_size: i32,
    pub flick_height: i32,
    pub lane_padding: i32,
    pub time_padding: i32,
    pub slide_path_padding: f64,
    pub meta_size: i32,
    pub tick_length: i32,
    pub tick_2_length: i32,
    pub note_host: String,
    pub target_segment_seconds: f64,
}

impl Default for DrawingConfig {
    fn default() -> Self {
        DrawingConfig {
            n_lanes: 12,
            lane_width: 16,
            time_height: 360.0,
            note_size: 16,
            flick_height: 24,
            lane_padding: 40,
            time_padding: 32,
            slide_path_padding: -1.0,
            meta_size: 192,
            tick_length: 24,
            tick_2_length: 8,
            note_host: "https://asset3.pjsekai.moe/live/note/custom01".to_string(),
            target_segment_seconds: 8.0,
        }
    }
}

/// Main drawing struct that generates SVG from a Score
pub struct Drawing {
    pub config: DrawingConfig,
    pub style_sheet: String,
    pub skill: bool,
    pub music_meta: Option<MusicMeta>,
    pub special_cover_objects: Vec<CoverObject>,
}

/// Music metadata for skill score display
#[derive(Debug, Clone)]
pub struct MusicMeta {
    pub fever_end_time: f64,
    pub fever_score: f64,
    pub skill_score_solo: Vec<f64>,
    pub skill_score_multi: Vec<f64>,
}

impl Drawing {
    pub fn new(
        note_host: Option<String>,
        style_sheet: Option<String>,
        skill: bool,
        music_meta: Option<MusicMeta>,
        target_segment_seconds: Option<f64>,
    ) -> Self {
        let mut config = DrawingConfig::default();
        if let Some(nh) = note_host {
            config.note_host = nh;
        }
        if let Some(tss) = target_segment_seconds {
            config.target_segment_seconds = tss;
        }

        let mut css = DEFAULT_CSS.to_string();
        if let Some(extra) = style_sheet {
            css.push('\n');
            css.push_str(&extra);
        }

        Drawing {
            config,
            style_sheet: css,
            skill,
            music_meta,
            special_cover_objects: Vec::new(),
        }
    }

    /// Generate a complete SVG string from a score
    pub fn svg(&mut self, score: &mut Score, lyric: Option<&Lyric>) -> String {
        let n_bars = score.notes.last().map(|n| n.bar().ceil() as i32).unwrap_or(0);

        // Build skill cover objects (mutates self)
        if self.skill {
            self.build_skill_covers(score);
        }

        // Now safe to borrow config immutably
        let cfg = &self.config;

        // Segment the chart
        let target_pixel_height = cfg.time_height * cfg.target_segment_seconds;
        let mut segments: Vec<(i32, i32)> = Vec::new(); // (bar_start, bar_stop)
        let mut bar_start = 0;
        let mut event = Event::new(Fraction::zero());
        event.bpm = Some(Fraction::from_integer(120));
        event.bar_length = Some(Fraction::from_integer(4));
        event.sentence_length = Some(4);

        for i in 0..=n_bars {
            let e = score.get_event(Fraction::from_integer(i as i64));
            let current_height = cfg.time_height
                * score.get_time_delta(
                    Fraction::from_integer(bar_start as i64),
                    Fraction::from_integer(i as i64),
                );

            if bar_start != i
                && (e.section != event.section
                    || current_height >= target_pixel_height
                    || i == n_bars)
            {
                segments.push((bar_start, i));
                bar_start = i;
            }

            event.merge_from(&e);
        }

        // Generate each segment SVG
        let mut segment_svgs: Vec<(String, f64, f64)> = Vec::new(); // (svg, width, height)
        let mut total_width: f64 = 0.0;
        let mut max_height: f64 = 0.0;

        for (start, stop) in &segments {
            let (svg_content, w, h) = self.render_sentence(score, lyric, *start, *stop);
            total_width += w;
            if h > max_height {
                max_height = h;
            }
            segment_svgs.push((svg_content, w, h));
        }

        // Build final SVG
        let final_width = total_width + cfg.lane_padding as f64 * 2.0;
        let final_height =
            max_height + cfg.time_padding as f64 * 2.0 + cfg.meta_size as f64 + cfg.time_padding as f64 * 2.0;

        let mut svg = String::with_capacity(1024 * 64);
        write!(
            svg,
            r#"<svg xmlns="http://www.w3.org/2000/svg" xmlns:xlink="http://www.w3.org/1999/xlink" width="{}" height="{}">"#,
            round(final_width),
            round(final_height),
        ).unwrap();

        // Defs
        write!(svg, "<defs>").unwrap();
        write!(svg, "<style>{}</style>", self.style_sheet).unwrap();

        // Gradients
        svg.push_str(r#"<linearGradient id="decoration-gradient" x1="0" y1="1" x2="0" y2="0">"#);
        svg.push_str(r#"<stop offset="0" stop-color="var(--color-start)"/>"#);
        svg.push_str(r#"<stop offset="1" stop-color="var(--color-stop)"/>"#);
        svg.push_str("</linearGradient>");

        svg.push_str(r#"<linearGradient id="decoration-critical-gradient" x1="0" y1="1" x2="0" y2="0">"#);
        svg.push_str(r#"<stop offset="0" stop-color="var(--color-start)"/>"#);
        svg.push_str(r#"<stop offset="1" stop-color="var(--color-stop)"/>"#);
        svg.push_str("</linearGradient>");

        // Note symbols
        self.write_note_symbols(&mut svg);
        svg.push_str("</defs>");

        // Background
        write!(
            svg,
            r#"<rect x="0" y="0" width="{}" height="{}" class="background"/>"#,
            round(final_width),
            round(max_height + cfg.time_padding as f64 * 2.0),
        ).unwrap();

        // Meta area
        write!(
            svg,
            r#"<rect x="0" y="{}" width="{}" height="{}" class="meta"/>"#,
            round(max_height + cfg.time_padding as f64 * 2.0),
            round(final_width),
            round(cfg.meta_size as f64 + cfg.time_padding as f64 * 2.0),
        ).unwrap();

        // Meta line
        write!(
            svg,
            r#"<line x1="0" y1="{y}" x2="{x2}" y2="{y}" class="meta-line"/>"#,
            y = round(max_height + cfg.time_padding as f64 * 2.0),
            x2 = round(final_width),
        ).unwrap();

        // Jacket image
        let jacket_url = score.meta.jacket.as_deref().unwrap_or(
            "https://storage.sekai.best/sekai-jp-assets/thumbnail/chara_rip/res009_no021_normal.png",
        );
        write!(
            svg,
            r#"<image href="{}" x="{}" y="{}" width="{}" height="{}"/>"#,
            escape_xml(jacket_url),
            cfg.lane_padding * 2,
            round(max_height + cfg.time_padding as f64 * 3.0),
            cfg.meta_size,
            cfg.meta_size,
        ).unwrap();

        // Title
        let title = [
            score.meta.title.as_deref(),
            score.meta.artist.as_deref(),
        ]
        .iter()
        .filter_map(|x| *x)
        .collect::<Vec<_>>()
        .join(" - ");
        let title = if title.is_empty() { "Untitled".to_string() } else { title };

        write!(
            svg,
            r#"<text x="{}" y="{}" class="title">{}</text>"#,
            cfg.meta_size + cfg.lane_padding * 4,
            round(cfg.meta_size as f64 + max_height + cfg.time_padding as f64 * 3.0 - 16.0),
            escape_xml(&title),
        ).unwrap();

        // Subtitle
        let subtitle_parts: Vec<String> = [
            score.meta.difficulty.as_ref().map(|d| d.to_uppercase()),
            score.meta.playlevel.clone(),
            Some("Code by pjsekai.moe, Modified by bilibili @xfl03 (3-3.dev),Generated by HarukiBot NEO".to_string()),
        ]
        .iter()
        .filter_map(|x| x.clone())
        .collect();
        let subtitle = subtitle_parts.join(" ");

        write!(
            svg,
            r#"<text x="{}" y="{}" class="subtitle">{}</text>"#,
            cfg.meta_size + cfg.lane_padding * 4,
            round(cfg.meta_size as f64 / 3.0 + max_height + cfg.time_padding as f64 * 3.0 - 8.0),
            escape_xml(&subtitle),
        ).unwrap();

        // Add segments
        let mut x_offset: f64 = 0.0;
        for (svg_content, w, h) in &segment_svgs {
            write!(
                svg,
                r#"<svg x="{}" y="{}" width="{}" height="{}">{}</svg>"#,
                round(x_offset + cfg.lane_padding as f64),
                round(max_height - h + cfg.time_padding as f64),
                round(*w),
                round(*h),
                svg_content,
            ).unwrap();
            x_offset += w;
        }

        svg.push_str("</svg>");
        svg
    }

    fn build_skill_covers(&mut self, score: &mut Score) {
        if let Some(ref mm) = self.music_meta {
            for e in &score.events.clone() {
                if e.text.as_deref() == Some("SUPER FEVER!!") {
                    let fever_end_bar = score.get_bar_by_time(mm.fever_end_time);
                    self.special_cover_objects.push(CoverObject::Rect {
                        bar_from: e.bar,
                        css_class: "fever-duration".to_string(),
                        bar_to: fever_end_bar,
                    });
                    self.special_cover_objects.push(CoverObject::Text {
                        bar_from: e.bar,
                        css_class: "skill-score".to_string(),
                        text: format!("multi+{:.2}%", mm.fever_score * 100.0),
                    });
                }
            }
        }

        let events = score.events.clone();
        let mut skill_i = 0usize;
        for e in &events {
            if e.text.as_deref() != Some("SKILL") {
                continue;
            }
            let skill_time = score.get_time(e.bar);
            self.special_cover_objects.push(CoverObject::Rect {
                bar_from: score.get_bar_by_time(skill_time - 5.0 / 60.0),
                css_class: "skill-great".to_string(),
                bar_to: score.get_bar_by_time(skill_time + 5.0 + 5.0 / 60.0),
            });
            self.special_cover_objects.push(CoverObject::Rect {
                bar_from: score.get_bar_by_time(skill_time - 2.5 / 60.0),
                css_class: "skill-perfect".to_string(),
                bar_to: score.get_bar_by_time(skill_time + 5.0 + 2.5 / 60.0),
            });
            self.special_cover_objects.push(CoverObject::Rect {
                bar_from: e.bar,
                css_class: "skill-duration".to_string(),
                bar_to: score.get_bar_by_time(skill_time + 5.0),
            });

            if let Some(ref mm) = self.music_meta {
                if skill_i < mm.skill_score_solo.len() {
                    let solo = format!("+{:.2}%", mm.skill_score_solo[skill_i] * 100.0);
                    let multi = format!("+{:.2}%", mm.skill_score_multi[skill_i] * 100.0);
                    let text = if solo != multi {
                        format!("solo{solo} multi{multi}")
                    } else {
                        solo
                    };
                    self.special_cover_objects.push(CoverObject::Text {
                        bar_from: e.bar,
                        css_class: "skill-score".to_string(),
                        text,
                    });
                }
            }
            skill_i += 1;
        }
    }

    fn write_note_symbols(&self, svg: &mut String) {
        let cfg = &self.config;
        let note_m_ratio = 1200;

        for note_number in 0..7 {
            // Base symbol
            write!(
                svg,
                r#"<symbol id="notes-{note_number}" viewBox="0 0 112 56">"#,
            ).unwrap();
            write!(
                svg,
                r#"<image href="{}/notes_{note_number}.png" x="-3" y="-3" width="118" height="62"/>"#,
                self.config.note_host,
            ).unwrap();
            svg.push_str("</symbol>");

            // Middle symbol
            write!(
                svg,
                r#"<symbol id="notes-{note_number}-middle" viewBox="0 0 {} 56">"#,
                112 * note_m_ratio,
            ).unwrap();
            write!(
                svg,
                r#"<image href="{}/notes_{note_number}.png" x="{}" y="-3" width="{}" height="62" preserveAspectRatio="none"/>"#,
                self.config.note_host,
                -(3 + 28) * note_m_ratio,
                118 * note_m_ratio,
            ).unwrap();
            svg.push_str("</symbol>");

            // Per-lane symbols
            for i in 1..=cfg.n_lanes {
                let note_height = cfg.note_size as f64;
                let note_width = cfg.lane_width as f64 * (i + 1) as f64;
                let note_inner_width = cfg.lane_width as f64 * i as f64;

                let note_l_width = note_height / 56.0 * 32.0;
                let note_r_width = note_l_width;
                let note_m_width = note_inner_width - (note_l_width + note_r_width) / 2.0 - 2.0;
                let note_padding_x = (note_width - note_l_width - note_m_width - note_r_width) / 2.0;

                write!(
                    svg,
                    r#"<symbol id="notes-{note_number}-{i}" viewBox="0 0 {note_width} {note_height}">"#,
                ).unwrap();

                // Left clip path
                write!(
                    svg,
                    r#"<clipPath id="notes-{note_number}-{i}-left"><rect x="0" y="0" width="{note_l_width}" height="{note_height}"/></clipPath>"#,
                ).unwrap();

                // Middle clip path
                write!(
                    svg,
                    r#"<clipPath id="notes-{note_number}-{i}-middle"><rect x="0" y="0" width="{note_m_width}" height="{note_height}"/></clipPath>"#,
                ).unwrap();

                // Right clip path
                write!(
                    svg,
                    r#"<clipPath id="notes-{note_number}-{i}-right"><rect x="{}" y="0" width="{note_r_width}" height="{note_height}"/></clipPath>"#,
                    note_height / 56.0 * 80.0,
                ).unwrap();

                // Left use
                write!(
                    svg,
                    r##"<use href="#notes-{note_number}" x="{}" y="0" width="{}" height="{note_height}" clip-path="url(#notes-{note_number}-{i}-left)"/>"##,
                    note_padding_x,
                    note_height * 2.0,
                ).unwrap();

                // Middle use
                write!(
                    svg,
                    r##"<use href="#notes-{note_number}-middle" x="{}" y="0" width="{}" height="{note_height}" clip-path="url(#notes-{note_number}-{i}-middle)"/>"##,
                    note_padding_x + note_l_width,
                    note_height * note_m_ratio as f64 * 2.0,
                ).unwrap();

                // Right use
                write!(
                    svg,
                    r##"<use href="#notes-{note_number}" x="{}" y="0" width="{}" height="{note_height}" clip-path="url(#notes-{note_number}-{i}-right)"/>"##,
                    note_padding_x + note_l_width + note_m_width + note_r_width - note_height * 2.0,
                    note_height * 2.0,
                ).unwrap();

                svg.push_str("</symbol>");
            }
        }
    }

    /// Render a single segment (sentence) of the chart
    fn render_sentence(
        &self,
        score: &mut Score,
        lyric: Option<&Lyric>,
        bar_start: i32,
        bar_stop: i32,
    ) -> (String, f64, f64) {
        let cfg = &self.config;
        let bar_start_f = Fraction::from_integer(bar_start as i64);
        let bar_stop_f = Fraction::from_integer(bar_stop as i64);

        let height = cfg.time_height * score.get_time_delta(bar_start_f, bar_stop_f);
        let width = cfg.lane_width as f64 * cfg.n_lanes as f64 + cfg.lane_padding as f64 * 2.0;

        let mut slide_paths = String::new();
        let mut among_images = String::new();
        let mut note_images = String::new();
        let mut flick_images_rev: Vec<String> = Vec::new();
        let mut tick_texts = String::new();

        // Process notes
        let active = score.active_notes.clone();
        let notes_snapshot: Vec<NoteData> = score.notes.clone();

        for (idx_in_active, &note_idx) in active.iter().enumerate() {
            let note = &notes_snapshot[note_idx];

            if note.is_slide() {
                // For slides, check if any part of the chain is in view
                if let Some(slide) = note.as_slide() {
                    let head_idx = slide.head_idx;
                    if head_idx == NO_NOTE {
                        continue;
                    }
                    let mut cur_idx = head_idx;
                    let mut before = false;
                    let mut found = false;
                    loop {
                        // Skip to path nodes
                        let cur = &notes_snapshot[cur_idx];
                        if let Some(s) = cur.as_slide() {
                            if !s.is_path(cur.note_type()) {
                                if s.next_idx == NO_NOTE {
                                    break;
                                }
                                cur_idx = s.next_idx;
                                continue;
                            }
                        }

                        let bar = notes_snapshot[cur_idx].bar();
                        if bar_start_f - Fraction::from_integer(1) <= bar
                            && bar < bar_stop_f + Fraction::from_integer(1)
                        {
                            found = true;
                            break;
                        } else if bar < bar_start_f - Fraction::from_integer(1) {
                            before = true;
                        } else if before && bar_stop_f + Fraction::from_integer(1) < bar {
                            found = true;
                            break;
                        }

                        if let Some(s) = notes_snapshot[cur_idx].as_slide() {
                            if s.next_idx == NO_NOTE {
                                break;
                            }
                            cur_idx = s.next_idx;
                        } else {
                            break;
                        }
                    }

                    if !found {
                        continue;
                    }
                }
            } else {
                // Non-slide: simple bar range check
                let bar = note.bar();
                if !(bar_start_f - Fraction::from_integer(1) <= bar
                    && bar < bar_stop_f + Fraction::from_integer(1))
                {
                    continue;
                }
            }

            // Tick text
            let is_tick = note.is_tick(&notes_snapshot);
            if let Some(tick_val) = is_tick {
                if tick_val {
                    // Find next tick note
                    let mut next_tick_idx: Option<NoteIdx> = None;
                    for &nidx in &active[idx_in_active..] {
                        let n = &notes_snapshot[nidx];
                        if n.is_tick(&notes_snapshot) == Some(true) && n.bar() > note.bar() {
                            next_tick_idx = Some(nidx);
                            break;
                        }
                    }
                    self.write_tick_text(
                        score,
                        &notes_snapshot,
                        note_idx,
                        next_tick_idx,
                        bar_stop_f,
                        &mut tick_texts,
                    );
                } else {
                    // is_tick == false: just a short line
                    let y = cfg.time_height * score.get_time_delta(note.bar(), bar_stop_f) + cfg.time_padding as f64;
                    write!(
                        tick_texts,
                        r#"<line x1="{}" y1="{}" x2="{}" y2="{}" class="tick-line"/>"#,
                        round(cfg.lane_padding as f64 - cfg.tick_2_length as f64),
                        round(y),
                        round(cfg.lane_padding as f64),
                        round(y),
                    ).unwrap();
                }
            }

            // Render note
            match note {
                NoteData::Tap(..) => {
                    self.write_note_image(score, &notes_snapshot, note_idx, bar_stop_f, &mut note_images);
                }
                NoteData::Directional(..) => {
                    self.write_flick_image(score, &notes_snapshot, note_idx, bar_stop_f, &mut flick_images_rev);
                    self.write_note_image(score, &notes_snapshot, note_idx, bar_stop_f, &mut note_images);
                }
                NoteData::Slide(_, slide) => {
                    if !slide.decoration {
                        match SlideType::from_i32(note.note_type()) {
                            Some(SlideType::Start) => {
                                self.write_slide_path(score, &notes_snapshot, note_idx, bar_stop_f, &mut slide_paths, &mut among_images);
                                self.write_note_image(score, &notes_snapshot, note_idx, bar_stop_f, &mut note_images);
                            }
                            Some(SlideType::End) => {
                                if slide.directional_idx != NO_NOTE {
                                    self.write_flick_image(score, &notes_snapshot, note_idx, bar_stop_f, &mut flick_images_rev);
                                }
                                self.write_note_image(score, &notes_snapshot, note_idx, bar_stop_f, &mut note_images);
                            }
                            _ => {}
                        }
                    } else {
                        if matches!(SlideType::from_i32(note.note_type()), Some(SlideType::Start)) {
                            self.write_slide_path(score, &notes_snapshot, note_idx, bar_stop_f, &mut slide_paths, &mut among_images);
                        }
                        if slide.tap_idx != NO_NOTE {
                            self.write_note_image(score, &notes_snapshot, slide.tap_idx, bar_stop_f, &mut note_images);
                            if slide.directional_idx != NO_NOTE {
                                self.write_flick_image(score, &notes_snapshot, note_idx, bar_stop_f, &mut flick_images_rev);
                            }
                        }
                    }
                }
            }
        }

        // Build the sentence SVG
        let mut svg = String::with_capacity(1024 * 16);

        // Background
        write!(
            svg,
            r#"<rect x="0" y="0" width="{}" height="{}" class="background"/>"#,
            round(width),
            round(height + cfg.time_padding as f64 * 2.0),
        ).unwrap();

        // Lane background
        write!(
            svg,
            r#"<rect x="{}" y="0" width="{}" height="{}" class="lane"/>"#,
            cfg.lane_padding,
            round(cfg.lane_width as f64 * cfg.n_lanes as f64),
            round(height + cfg.time_padding as f64 * 2.0),
        ).unwrap();

        // Cover objects
        for cover in &self.special_cover_objects {
            match cover {
                CoverObject::Text { bar_from, css_class, text } => {
                    if *bar_from < bar_start_f - Fraction::from_f64(0.2)
                        || *bar_from >= bar_stop_f - Fraction::from_f64(0.1)
                    {
                        continue;
                    }
                    let y = cfg.time_height * score.get_time_delta(*bar_from, bar_stop_f) + cfg.time_padding as f64;
                    let x = cfg.lane_width as f64 * cfg.n_lanes as f64 + cfg.lane_padding as f64 * 2.0 - 3.0;
                    write!(
                        svg,
                        r#"<text x="{}" y="{}" transform="rotate(-90, {}, {})" class="{}">{}</text>"#,
                        round(x), round(y), round(x), round(y),
                        escape_xml(css_class), escape_xml(text),
                    ).unwrap();
                }
                CoverObject::Rect { bar_from, css_class, bar_to } => {
                    let cover_from = if *bar_from > bar_start_f - Fraction::from_f64(0.2) {
                        *bar_from
                    } else {
                        bar_start_f - Fraction::from_f64(0.2)
                    };
                    let cover_to = if *bar_to < bar_stop_f + Fraction::from_f64(0.2) {
                        *bar_to
                    } else {
                        bar_stop_f + Fraction::from_f64(0.2)
                    };
                    if cover_to <= cover_from {
                        continue;
                    }
                    let y = cfg.time_height * score.get_time_delta(cover_to, bar_stop_f) + cfg.time_padding as f64;
                    let h = cfg.time_height * score.get_time_delta(cover_from, cover_to);
                    write!(
                        svg,
                        r#"<rect x="{}" y="{}" width="{}" height="{}" class="{}"/>"#,
                        cfg.lane_padding,
                        round(y),
                        round(cfg.lane_width as f64 * cfg.n_lanes as f64),
                        round(h),
                        escape_xml(css_class),
                    ).unwrap();
                }
            }
        }

        // Lane lines
        for lane in (0..=cfg.n_lanes).step_by(2) {
            let x = cfg.lane_width as f64 * lane as f64 + cfg.lane_padding as f64;
            write!(
                svg,
                r#"<line x1="{}" y1="0" x2="{}" y2="{}" class="lane-line"/>"#,
                round(x),
                round(x),
                round(height + cfg.time_padding as f64 * 2.0),
            ).unwrap();
        }

        // Bar and beat lines
        for bar in bar_start..=bar_stop {
            let bar_f = Fraction::from_integer(bar as i64);
            let y = cfg.time_height * score.get_time_delta(bar_f, bar_stop_f) + cfg.time_padding as f64;
            let x1 = cfg.lane_width as f64 * 0.0 + cfg.lane_padding as f64;
            let x2 = cfg.lane_width as f64 * cfg.n_lanes as f64 + cfg.lane_padding as f64;

            write!(
                svg,
                r#"<line x1="{}" y1="{}" x2="{}" y2="{}" class="bar-line"/>"#,
                round(x1), round(y), round(x2), round(y),
            ).unwrap();

            let event = score.get_event(bar_f);
            let bar_length = event.bar_length.unwrap_or(Fraction::from_integer(4)).to_f64().ceil() as i32;
            let bar_length_frac = event.bar_length.unwrap_or(Fraction::from_integer(4));

            for beat_i in 1..bar_length {
                let beat_bar = bar_f + Fraction::new(beat_i as i64, 1) / bar_length_frac;
                let beat_y = cfg.time_height * score.get_time_delta(beat_bar, bar_stop_f)
                    + cfg.time_padding as f64;
                write!(
                    svg,
                    r#"<line x1="{}" y1="{}" x2="{}" y2="{}" class="beat-line"/>"#,
                    round(x1), round(beat_y), round(x2), round(beat_y),
                ).unwrap();
            }
        }

        // Event labels
        let mut print_events: Vec<Event> = Vec::new();
        let mut all_events: Vec<Event> = (bar_start..=bar_stop)
            .map(|i| Event::new(Fraction::from_integer(i as i64)))
            .collect();
        all_events.extend(score.events.clone());
        all_events.sort_by(|a, b| a.bar.partial_cmp(&b.bar).unwrap_or(std::cmp::Ordering::Equal));

        for event in &all_events {
            if event.speed.is_some() {
                let y = cfg.time_height * score.get_time_delta(event.bar, bar_stop_f) + cfg.time_padding as f64;
                let x1 = cfg.lane_padding as f64;
                let x2 = cfg.lane_width as f64 * cfg.n_lanes as f64 + cfg.lane_padding as f64;

                write!(
                    svg,
                    r#"<line x1="{}" y1="{}" x2="{}" y2="{}" class="speed-line"/>"#,
                    round(x1), round(y), round(x2), round(y),
                ).unwrap();
                write!(
                    svg,
                    r#"<text x="{}" y="{}" class="speed-text">{}x</text>"#,
                    round(x2 - 2.0),
                    round(y - 2.0),
                    format_g(event.speed.unwrap()),
                ).unwrap();
                continue;
            }

            if let Some(last) = print_events.last_mut() {
                if (event.bar - last.bar).to_f64() <= 1.0 / 16.0 {
                    last.merge_from(event);
                } else {
                    print_events.push(event.clone());
                }
            } else {
                print_events.push(event.clone());
            }

            let special = event.bpm.is_some()
                || event.bar_length.is_some()
                || event.speed.is_some()
                || event.section.is_some()
                || event.text.is_some();

            let y = cfg.time_height * score.get_time_delta(event.bar, bar_stop_f) + cfg.time_padding as f64;
            write!(
                svg,
                r#"<line x1="{}" y1="{}" x2="{}" y2="{}" class="{}"/>"#,
                round(cfg.lane_width as f64 * 0.0),
                round(y),
                round(cfg.lane_padding as f64),
                round(y),
                if special { "event-flag" } else { "bar-count-flag" },
            ).unwrap();
        }

        // Event text labels
        for event in &print_events {
            if !(bar_start_f - Fraction::from_integer(1) <= event.bar
                && event.bar < bar_stop_f + Fraction::from_integer(1))
            {
                continue;
            }

            let mut parts: Vec<String> = Vec::new();
            if event.bar.trunc() as i64 == *event.bar.numer() && *event.bar.denom() == 1 {
                parts.push(format!("#{}", format_g(event.bar.to_f64())));
            }
            if let Some(bpm) = event.bpm {
                parts.push(format!("{} BPM", format_g(bpm.to_f64())));
            }
            if let Some(bl) = event.bar_length {
                parts.push(format!("{}/4", format_g(bl.to_f64())));
            }
            if let Some(ref section) = event.section {
                parts.push(section.clone());
            }
            if let Some(ref text) = event.text {
                parts.push(text.clone());
            }

            let text = parts.join(", ");
            if text.is_empty() {
                continue;
            }

            let special = event.bpm.is_some()
                || event.bar_length.is_some()
                || event.speed.is_some()
                || event.section.is_some()
                || event.text.is_some();

            let y = cfg.time_height * score.get_time_delta(event.bar, bar_stop_f) + cfg.time_padding as f64;
            write!(
                svg,
                r#"<text x="{}" y="{}" transform="rotate(-90, {}, {})" class="{}">{}</text>"#,
                round(cfg.lane_padding as f64 + 8.0),
                round(y - cfg.lane_width as f64 * 1.5),
                round(cfg.lane_padding as f64),
                round(y),
                if special { "event-text" } else { "bar-count-text" },
                escape_xml(&text),
            ).unwrap();
        }

        // Lyrics
        if let Some(lyric) = lyric {
            for word in &lyric.words {
                if !(bar_start_f - Fraction::from_integer(1) <= word.bar
                    && word.bar < bar_stop_f + Fraction::from_integer(1))
                {
                    continue;
                }
                let y = cfg.time_height * score.get_time_delta(word.bar, bar_stop_f) + cfg.time_padding as f64;
                let x = cfg.lane_width as f64 * cfg.n_lanes as f64 + cfg.lane_padding as f64;
                write!(
                    svg,
                    r#"<text x="{}" y="{}" transform="rotate(-90, {}, {})" class="lyric-text">{}</text>"#,
                    round(x),
                    round(y + 16.0),
                    round(x),
                    round(y),
                    escape_xml(&word.text),
                ).unwrap();
            }
        }

        // Layer order: slides → notes → amongs → flicks (reversed) → ticks
        svg.push_str(&slide_paths);
        svg.push_str(&note_images);
        svg.push_str(&among_images);
        for flick in flick_images_rev.iter().rev() {
            svg.push_str(flick);
        }
        svg.push_str(&tick_texts);

        (svg, width, height + cfg.time_padding as f64 * 2.0)
    }

    fn write_slide_path(
        &self,
        score: &mut Score,
        arena: &[NoteData],
        start_idx: NoteIdx,
        bar_stop: Fraction,
        slide_paths: &mut String,
        among_images: &mut String,
    ) {
        let cfg = &self.config;
        let mut lefts: Vec<[(f64, f64); 4]> = Vec::new();
        let mut rights: Vec<[(f64, f64); 4]> = Vec::new();

        let mut cur_idx = start_idx;

        loop {
            let cur_type = arena[cur_idx].note_type();
            if matches!(SlideType::from_i32(cur_type), Some(SlideType::End)) {
                break;
            }

            let slide = match arena[cur_idx].as_slide() {
                Some(s) => s,
                None => break,
            };
            if slide.next_idx == NO_NOTE {
                break;
            }

            let mut amongs: Vec<NoteIdx> = Vec::new();
            let mut next_idx = slide.next_idx;

            loop {
                let next_type = arena[next_idx].note_type();
                if matches!(SlideType::from_i32(next_type), Some(SlideType::Relay)) {
                    amongs.push(next_idx);
                }

                let next_slide = match arena[next_idx].as_slide() {
                    Some(s) => s,
                    None => break,
                };
                if next_slide.is_path(next_type) {
                    break;
                }
                if next_slide.next_idx == NO_NOTE {
                    break;
                }
                next_idx = next_slide.next_idx;
            }

            let (l, r) = self.get_bezier_coordinates(score, arena, cur_idx, next_idx, bar_stop);
            lefts.push(l);
            rights.push(r);

            // Add among images
            for &among_idx in &amongs {
                let among_bar = arena[among_idx].bar();
                let y = cfg.time_height * score.get_time_delta(among_bar, bar_stop) + cfg.time_padding as f64;
                let x_l = binary_solution_for_x(y, &l);
                let x_r = binary_solution_for_x(y, &r);
                let x = (x_l + x_r) / 2.0;
                let w = cfg.lane_width as f64;
                let h = cfg.lane_width as f64;

                let is_crit = arena[among_idx].is_critical(arena);
                write!(
                    among_images,
                    r#"<image href="{}/notes_long_among{}.png" x="{}" y="{}" width="{}" height="{}"/>"#,
                    self.config.note_host,
                    if is_crit { "_crtcl" } else { "" },
                    round(x - w / 2.0),
                    round(y - h / 2.0),
                    round(w),
                    round(h),
                ).unwrap();
            }

            cur_idx = next_idx;
        }

        if lefts.is_empty() {
            return;
        }

        // Build path data
        let is_critical = arena[start_idx].is_critical(arena);
        let is_decoration = arena[start_idx].as_slide().map(|s| s.decoration).unwrap_or(false);

        let class_name = if is_decoration {
            if is_critical { "decoration-critical" } else { "decoration" }
        } else {
            if is_critical { "slide-critical" } else { "slide" }
        };

        let mut d = String::new();
        // Forward left edges
        for (i, l) in lefts.iter().enumerate() {
            if i == 0 {
                write!(d, "M{},{}", round(l[0].0), round(l[0].1)).unwrap();
            }
            write!(
                d,
                "C{},{},{},{},{},{}",
                round(l[1].0), round(l[1].1),
                round(l[2].0), round(l[2].1),
                round(l[3].0), round(l[3].1),
            ).unwrap();
        }
        // Reverse right edges
        for (i, r) in rights.iter().rev().enumerate() {
            if i == 0 {
                write!(d, "L{},{}", round(r[3].0), round(r[3].1)).unwrap();
            }
            write!(
                d,
                "C{},{},{},{},{},{}",
                round(r[2].0), round(r[2].1),
                round(r[1].0), round(r[1].1),
                round(r[0].0), round(r[0].1),
            ).unwrap();
        }
        d.push('z');

        write!(slide_paths, r#"<path d="{d}" class="{class_name}"/>"#).unwrap();
    }

    fn get_bezier_coordinates(
        &self,
        score: &mut Score,
        arena: &[NoteData],
        idx0: NoteIdx,
        idx1: NoteIdx,
        bar_stop: Fraction,
    ) -> ([(f64, f64); 4], [(f64, f64); 4]) {
        let cfg = &self.config;
        let slide_0 = &arena[idx0];
        let slide_1 = &arena[idx1];

        let y_0 = cfg.time_height * score.get_time_delta(slide_0.bar(), bar_stop) + cfg.time_padding as f64;
        let y_1 = cfg.time_height * score.get_time_delta(slide_1.bar(), bar_stop) + cfg.time_padding as f64;

        let ease_in = slide_0.as_slide().and_then(|s| {
            if s.directional_idx != NO_NOTE {
                DirectionalType::from_i32(arena[s.directional_idx].note_type())
                    .filter(|dt| matches!(dt, DirectionalType::Down))
            } else {
                None
            }
        }).is_some();

        let ease_out = slide_0.as_slide().and_then(|s| {
            if s.directional_idx != NO_NOTE {
                DirectionalType::from_i32(arena[s.directional_idx].note_type())
                    .filter(|dt| matches!(dt, DirectionalType::LowerLeft | DirectionalType::LowerRight))
            } else {
                None
            }
        }).is_some();

        let is_decoration = slide_0.as_slide().map(|s| s.decoration).unwrap_or(false);
        let spp = if is_decoration { 0.0 } else { cfg.slide_path_padding };

        let l0_x = cfg.lane_width as f64 * (slide_0.lane() - 2) as f64 + cfg.lane_padding as f64 - spp;
        let l1_x = cfg.lane_width as f64 * (slide_1.lane() - 2) as f64 + cfg.lane_padding as f64 - spp;
        let r0_x = cfg.lane_width as f64 * (slide_0.lane() - 2 + slide_0.width()) as f64 + cfg.lane_padding as f64 + spp;
        let r1_x = cfg.lane_width as f64 * (slide_1.lane() - 2 + slide_1.width()) as f64 + cfg.lane_padding as f64 + spp;

        let mid_y = (y_0 + y_1) / 2.0;

        let left = [
            (l0_x, y_0),
            (l0_x, if ease_in { mid_y } else { y_0 }),
            (l1_x, if ease_out { mid_y } else { y_1 }),
            (l1_x, y_1),
        ];

        let right = [
            (r0_x, y_0),
            (r0_x, if ease_in { mid_y } else { y_0 }),
            (r1_x, if ease_out { mid_y } else { y_1 }),
            (r1_x, y_1),
        ];

        (left, right)
    }

    fn write_note_image(
        &self,
        score: &mut Score,
        arena: &[NoteData],
        note_idx: NoteIdx,
        bar_stop: Fraction,
        out: &mut String,
    ) {
        let cfg = &self.config;
        let note = &arena[note_idx];

        let y = cfg.time_height * score.get_time_delta(note.bar(), bar_stop) + cfg.time_padding as f64;
        let x = cfg.lane_width as f64 * (note.lane() as f64 - 2.5) + cfg.lane_padding as f64;
        let w = cfg.lane_width as f64 * (note.width() + 1) as f64;
        let h = cfg.lane_width as f64 / 64.0 * 56.0 * 2.0;

        if note.is_none(arena) {
            return;
        }

        let note_number;
        if note.is_trend(arena) {
            // Add friction among image
            self.write_friction_among_image(score, arena, note_idx, bar_stop, out);
            if note.is_critical(arena) {
                note_number = 5;
            } else if note.is_directional() {
                note_number = 6;
            } else {
                note_number = 4;
            }
        } else {
            if note.is_critical(arena) {
                note_number = 0;
            } else if note.is_directional() {
                note_number = 3;
            } else if note.is_slide() {
                if matches!(SlideType::from_i32(note.note_type()), Some(SlideType::End)) {
                    if let Some(s) = note.as_slide() {
                        if s.directional_idx != NO_NOTE {
                            note_number = 3;
                        } else {
                            note_number = 1;
                        }
                    } else {
                        note_number = 1;
                    }
                } else {
                    note_number = 1;
                }
            } else {
                note_number = 2;
            }
        }

        write!(
            out,
            r##"<use href="#notes-{}-{}" x="{}" y="{}" width="{}" height="{}"/>"##,
            note_number,
            note.width(),
            round(x),
            round(y - h / 2.0),
            round(w),
            round(h),
        ).unwrap();
    }

    fn write_friction_among_image(
        &self,
        score: &mut Score,
        arena: &[NoteData],
        note_idx: NoteIdx,
        bar_stop: Fraction,
        out: &mut String,
    ) {
        let cfg = &self.config;
        let note = &arena[note_idx];

        let y = cfg.time_height * score.get_time_delta(note.bar(), bar_stop) + cfg.time_padding as f64;
        let x = cfg.lane_width as f64 * (note.lane() as f64 + note.width() as f64 / 2.0 - 2.0) + cfg.lane_padding as f64;
        let w = cfg.lane_width as f64 * 0.75;
        let h = cfg.lane_width as f64 * 0.75;

        let suffix = if note.is_critical(arena) {
            "_crtcl"
        } else if note.is_directional() {
            "_flick"
        } else {
            "_long"
        };

        write!(
            out,
            r#"<image href="{}/notes_friction_among{}.png" x="{}" y="{}" width="{}" height="{}"/>"#,
            self.config.note_host,
            suffix,
            round(x - w / 2.0),
            round(y - h / 2.0),
            round(w),
            round(h),
        ).unwrap();
    }

    fn write_flick_image(
        &self,
        score: &mut Score,
        arena: &[NoteData],
        note_idx: NoteIdx,
        bar_stop: Fraction,
        out: &mut Vec<String>,
    ) {
        let cfg = &self.config;
        let note = &arena[note_idx];

        let y = cfg.time_height * score.get_time_delta(note.bar(), bar_stop) + cfg.time_padding as f64;

        if note.is_none(arena) {
            return;
        }

        let dir_type = if note.is_directional() {
            DirectionalType::from_i32(note.note_type())
        } else if note.is_slide() {
            if let Some(s) = note.as_slide() {
                if s.directional_idx != NO_NOTE {
                    DirectionalType::from_i32(arena[s.directional_idx].note_type())
                } else {
                    None
                }
            } else {
                None
            }
        } else {
            None
        };

        let flick_type = match dir_type {
            Some(DirectionalType::UpperLeft) => Some(DirectionalType::UpperLeft),
            Some(DirectionalType::UpperRight) => Some(DirectionalType::UpperRight),
            Some(DirectionalType::Up) => Some(DirectionalType::Up),
            Some(_) => None,
            None => Some(DirectionalType::Up),
        };

        let flick_type = match flick_type {
            Some(t) => t,
            None => return,
        };

        let width = if note.width() < 6 { note.width() } else { 6 };
        let h0 = cfg.flick_height as f64;
        let h = h0 * ((width as f64 + 3.0) / 3.0_f64).powf(0.75);
        let w = h0 * 1.5 * ((width as f64 + 0.5) / 3.0_f64).powf(0.75);
        let x = cfg.lane_width as f64 * (note.lane() as f64 - 2.0 + note.width() as f64 / 2.0)
            + cfg.lane_padding as f64;

        let bias = match flick_type {
            DirectionalType::UpperLeft => -(cfg.note_size as f64) / 4.0,
            DirectionalType::UpperRight => cfg.note_size as f64 / 4.0,
            _ => 0.0,
        };

        let is_diagonal = matches!(flick_type, DirectionalType::UpperLeft | DirectionalType::UpperRight);
        let is_crit = note.is_critical(arena);

        let mut img = String::new();
        write!(
            img,
            r#"<image href="{}/notes_flick_arrow{}_{:02}{}.png" x="{}" y="{}" width="{}" height="{}""#,
            self.config.note_host,
            if is_crit { "_crtcl" } else { "" },
            width,
            if is_diagonal { "_diagonal" } else { "" },
            round(x - w / 2.0 + bias),
            round(y + cfg.note_size as f64 / 4.0 - h),
            round(w),
            round(h),
        ).unwrap();

        if matches!(flick_type, DirectionalType::UpperRight) {
            write!(
                img,
                r#" transform-origin="{} 0" transform="scale(-1, 1)""#,
                round(x + bias),
            ).unwrap();
        }

        img.push_str("/>");
        out.push(img);
    }

    fn write_tick_text(
        &self,
        score: &mut Score,
        arena: &[NoteData],
        note_idx: NoteIdx,
        next_idx: Option<NoteIdx>,
        bar_stop: Fraction,
        out: &mut String,
    ) {
        let cfg = &self.config;
        let note = &arena[note_idx];
        let y = cfg.time_height * score.get_time_delta(note.bar(), bar_stop) + cfg.time_padding as f64;

        let next_idx = match next_idx {
            Some(idx) => idx,
            None => {
                // Just draw a short tick line
                write!(
                    out,
                    r#"<line x1="{}" y1="{}" x2="{}" y2="{}" class="tick-line"/>"#,
                    round(cfg.lane_padding as f64 - cfg.tick_2_length as f64),
                    round(y),
                    round(cfg.lane_padding as f64),
                    round(y),
                ).unwrap();
                return;
            }
        };

        let next = &arena[next_idx];
        if next.bar() == note.bar() || (next.bar() - note.bar()).to_f64() > 1.0 {
            // Use distance to next bar
            let interval_frac = Fraction::from_integer(note.bar().floor() + 1) - note.bar();
            self.write_tick_with_interval(score, y, interval_frac, note.bar(), out);
        } else if (next.bar() - note.bar()).to_f64() > 0.5
            && next.bar().floor() != note.bar().floor()
        {
            let interval_frac = Fraction::from_integer(note.bar().floor() + 1) - note.bar();
            self.write_tick_with_interval(score, y, interval_frac, note.bar(), out);
        } else {
            let interval_frac = next.bar() - note.bar();
            self.write_tick_with_interval(score, y, interval_frac, note.bar(), out);
        }
    }

    fn write_tick_with_interval(
        &self,
        score: &mut Score,
        y: f64,
        interval: Fraction,
        bar: Fraction,
        out: &mut String,
    ) {
        let cfg = &self.config;
        let event = score.get_event(bar);
        let bar_length = event.bar_length.unwrap_or(Fraction::from_integer(4));
        let interval = (interval * bar_length / Fraction::from_integer(4)).limit_denominator(100);

        if interval == Fraction::zero() {
            return;
        }

        let text = if *interval.numer() != 1 {
            format!("{}/{}", interval.numer(), interval.denom())
        } else {
            format!("/{}", interval.denom())
        };

        write!(
            out,
            r#"<line x1="{}" y1="{}" x2="{}" y2="{}" class="tick-line"/>"#,
            round(cfg.lane_padding as f64 - cfg.tick_length as f64),
            round(y),
            round(cfg.lane_padding as f64),
            round(y),
        ).unwrap();

        write!(
            out,
            r#"<text x="{}" y="{}" class="tick-text">{}</text>"#,
            round(cfg.lane_padding as f64 - 4.0),
            round(y - 2.0),
            escape_xml(&text),
        ).unwrap();
    }
}

/// Binary search to find x-coordinate on a cubic Bézier curve at a given y
fn binary_solution_for_x(y: f64, curve: &[(f64, f64); 4]) -> f64 {
    binary_solution_for_x_inner(y, curve, 0.0, 1.0, 0.1)
}

fn binary_solution_for_x_inner(y: f64, curve: &[(f64, f64); 4], start: f64, end: f64, epsilon: f64) -> f64 {
    let t = (start + end) / 2.0;
    let t1 = 1.0 - t;

    let px = curve[0].0 * t1 * t1 * t1
        + curve[1].0 * 3.0 * t1 * t1 * t
        + curve[2].0 * 3.0 * t1 * t * t
        + curve[3].0 * t * t * t;

    let py = curve[0].1 * t1 * t1 * t1
        + curve[1].1 * 3.0 * t1 * t1 * t
        + curve[2].1 * 3.0 * t1 * t * t
        + curve[3].1 * t * t * t;

    if (py - y).abs() < epsilon {
        px
    } else if py > y {
        binary_solution_for_x_inner(y, curve, t, end, epsilon)
    } else {
        binary_solution_for_x_inner(y, curve, start, t, epsilon)
    }
}

fn round(v: f64) -> f64 {
    (v * 100.0).round() / 100.0
}

fn escape_xml(s: &str) -> String {
    s.replace('&', "&amp;")
        .replace('<', "&lt;")
        .replace('>', "&gt;")
        .replace('"', "&quot;")
}

/// Format a float like Python's %g (remove trailing zeros)
fn format_g(v: f64) -> String {
    if v == v.floor() {
        format!("{}", v as i64)
    } else {
        format!("{v}")
    }
}
