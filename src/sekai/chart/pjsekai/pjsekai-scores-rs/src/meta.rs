/// Chart metadata matching Python's Meta dataclass
#[derive(Debug, Clone, Default)]
pub struct Meta {
    pub title: Option<String>,
    pub subtitle: Option<String>,
    pub artist: Option<String>,
    pub genre: Option<String>,
    pub designer: Option<String>,
    pub difficulty: Option<String>,
    pub playlevel: Option<String>,
    pub songid: Option<String>,
    pub wave: Option<String>,
    pub waveoffset: Option<String>,
    pub jacket: Option<String>,
    pub background: Option<String>,
    pub movie: Option<String>,
    pub movieoffset: Option<f64>,
    pub basebpm: Option<f64>,
}

impl Meta {
    pub fn new() -> Self {
        Meta::default()
    }

    /// Merge operator matching Python's `__or__`: self takes priority for non-None fields
    pub fn merge(&self, other: &Meta) -> Meta {
        Meta {
            title: self.title.clone().or_else(|| other.title.clone()),
            subtitle: self.subtitle.clone().or_else(|| other.subtitle.clone()),
            artist: self.artist.clone().or_else(|| other.artist.clone()),
            genre: self.genre.clone().or_else(|| other.genre.clone()),
            designer: self.designer.clone().or_else(|| other.designer.clone()),
            difficulty: self.difficulty.clone().or_else(|| other.difficulty.clone()),
            playlevel: self.playlevel.clone().or_else(|| other.playlevel.clone()),
            songid: self.songid.clone().or_else(|| other.songid.clone()),
            wave: self.wave.clone().or_else(|| other.wave.clone()),
            waveoffset: self.waveoffset.clone().or_else(|| other.waveoffset.clone()),
            jacket: self.jacket.clone().or_else(|| other.jacket.clone()),
            background: self.background.clone().or_else(|| other.background.clone()),
            movie: self.movie.clone().or_else(|| other.movie.clone()),
            movieoffset: self.movieoffset.or(other.movieoffset),
            basebpm: self.basebpm.or(other.basebpm),
        }
    }

    /// Set a metadata field by lowercase name (matching Python's setattr pattern)
    pub fn set_field(&mut self, name: &str, value: &str) {
        match name {
            "title" => self.title = Some(value.to_string()),
            "subtitle" => self.subtitle = Some(value.to_string()),
            "artist" => self.artist = Some(value.to_string()),
            "genre" => self.genre = Some(value.to_string()),
            "designer" => self.designer = Some(value.to_string()),
            "difficulty" => self.difficulty = Some(value.to_string()),
            "playlevel" => self.playlevel = Some(value.to_string()),
            "songid" => self.songid = Some(value.to_string()),
            "wave" => self.wave = Some(value.to_string()),
            "waveoffset" => self.waveoffset = Some(value.to_string()),
            "jacket" => self.jacket = Some(value.to_string()),
            "background" => self.background = Some(value.to_string()),
            "movie" => self.movie = Some(value.to_string()),
            "movieoffset" => {
                if let Ok(v) = value.parse::<f64>() {
                    self.movieoffset = Some(v);
                }
            }
            "basebpm" => {
                if let Ok(v) = value.parse::<f64>() {
                    self.basebpm = Some(v);
                }
            }
            _ => {}
        }
    }

    /// Check if a field name is valid
    pub fn has_field(name: &str) -> bool {
        matches!(
            name,
            "title" | "subtitle" | "artist" | "genre" | "designer" | "difficulty"
            | "playlevel" | "songid" | "wave" | "waveoffset" | "jacket"
            | "background" | "movie" | "movieoffset" | "basebpm"
        )
    }
}
