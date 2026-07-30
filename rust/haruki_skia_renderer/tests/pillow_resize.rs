// Compile the compatibility module independently as an integration-test target too. Its unit
// tests carry Pillow 12.3-generated RGBA goldens and therefore verify the exact source file the
// IR interpreter includes, without exposing the internal helper as public API.
#[path = "../src/pillow_resize.rs"]
mod pillow_resize;
