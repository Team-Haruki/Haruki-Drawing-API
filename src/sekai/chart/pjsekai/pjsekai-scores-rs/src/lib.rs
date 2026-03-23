pub mod fraction;
pub mod meta;
pub mod notes;
pub mod line;
pub mod lyric;
pub mod score;
pub mod rebase;
pub mod drawing;

// Re-exports for convenience
pub use fraction::Fraction;
pub use meta::Meta;
pub use notes::{NoteData, NoteIdx};
pub use notes::tap::{Tap, TapType};
pub use notes::directional::{Directional, DirectionalType};
pub use notes::slide::{Slide, SlideType};
pub use notes::event::Event;
pub use lyric::Lyric;
pub use score::Score;
pub use rebase::Rebase;
pub use drawing::{Drawing, MusicMeta};

/// Python bindings via PyO3 (only compiled with `--features python`)
#[cfg(feature = "python")]
mod python;

#[cfg(feature = "python")]
use pyo3::prelude::*;

#[cfg(feature = "python")]
#[pymodule]
fn pjsekai_scores(m: &Bound<'_, PyModule>) -> PyResult<()> {
    python::register(m)
}
