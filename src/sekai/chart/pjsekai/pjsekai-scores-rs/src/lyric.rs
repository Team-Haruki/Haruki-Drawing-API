use crate::fraction::Fraction;
use regex::Regex;

/// A word in the lyrics, synchronized to a bar position
#[derive(Debug, Clone)]
pub struct Word {
    pub bar: Fraction,
    pub text: String,
}

/// Container for synchronized lyrics
#[derive(Debug, Clone)]
pub struct Lyric {
    pub words: Vec<Word>,
}

impl Lyric {
    pub fn new() -> Self {
        Lyric { words: Vec::new() }
    }

    /// Parse lyrics from text content (matching Python's Lyric.load)
    pub fn load(content: &str) -> Lyric {
        let re = Regex::new(r"^(\d+): (.*)$").unwrap();
        let mut lyric = Lyric::new();

        for line in content.lines() {
            let line = line.trim();
            if let Some(caps) = re.captures(line) {
                let bar: i64 = caps[1].parse().unwrap_or(0);
                let text_part = &caps[2];
                let texts: Vec<&str> = text_part.split('/').collect();
                let len = texts.len() as i64;
                for (i, text) in texts.iter().enumerate() {
                    if !text.is_empty() {
                        lyric.words.push(Word {
                            bar: Fraction::from_integer(bar) + Fraction::new(i as i64, len),
                            text: text.to_string(),
                        });
                    }
                }
            }
        }

        lyric
    }
}

impl Default for Lyric {
    fn default() -> Self {
        Lyric::new()
    }
}
