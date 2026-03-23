use clap::Parser;
use std::fs;

use pjsekai_scores::{Drawing, Lyric, Rebase, Score};

#[derive(Parser, Debug)]
#[command(name = "pjsekai-scores", about = "Project SEKAI score (.sus) to SVG converter")]
struct Args {
    /// The .sus score file
    score: String,

    /// Customized BPM, beats and sections (JSON)
    #[arg(long)]
    rebase: Option<String>,

    /// Lyrics file
    #[arg(long)]
    lyric: Option<String>,

    /// Custom CSS stylesheet
    #[arg(long)]
    css: Option<String>,

    /// Base URL for note asset files
    #[arg(long, default_value = "https://asset3.pjsekai.moe/live/note/custom01")]
    note_host: String,

    /// Output SVG file path
    #[arg(short, long)]
    output: Option<String>,
}

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let args = Args::parse();

    // Determine output path
    let output = if let Some(out) = &args.output {
        if std::path::Path::new(out).is_dir() {
            let stem = std::path::Path::new(&args.score)
                .file_stem()
                .unwrap_or_default()
                .to_string_lossy();
            format!("{}/{stem}.svg", out)
        } else {
            out.clone()
        }
    } else {
        let stem = std::path::Path::new(&args.score)
            .file_stem()
            .unwrap_or_default()
            .to_string_lossy();
        let dir = std::path::Path::new(&args.score)
            .parent()
            .unwrap_or(std::path::Path::new("."));
        format!("{}/{stem}.svg", dir.display())
    };

    // Parse score
    let mut score = Score::open(&args.score)?;

    // Apply rebase if provided
    if let Some(rebase_path) = &args.rebase {
        let json_str = fs::read_to_string(rebase_path)?;
        let rebase = Rebase::from_json(&json_str)?;
        score = rebase.apply(&mut score);
    }

    // Load lyrics
    let lyric = if let Some(lyric_path) = &args.lyric {
        let content = fs::read_to_string(lyric_path)?;
        Some(Lyric::load(&content))
    } else {
        None
    };

    // Load custom CSS
    let custom_css = if let Some(css_path) = &args.css {
        Some(fs::read_to_string(css_path)?)
    } else {
        None
    };

    // Generate SVG
    let mut drawing = Drawing::new(
        Some(args.note_host),
        custom_css,
        false,
        None,
        None,
    );

    let svg = drawing.svg(&mut score, lyric.as_ref());

    // Write output
    fs::write(&output, &svg)?;
    eprintln!("Written to {output}");

    Ok(())
}
