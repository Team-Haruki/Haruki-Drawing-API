# Native text alignment with Pillow

## Why platform Skia measurements did not match

The local CPython 3.14t Pillow build uses FreeType BASIC layout (RAQM is absent).
Skia uses CoreText on macOS. The same Source Han font file therefore does not imply
the same advances, bounds, or grayscale glyph mask. Whole-run rounding and the old
CoreText gamma calibration cannot correct per-glyph placement.

The BASIC contract also includes details beyond ink bounds: the horizontal pen line
contributes to `getbbox`, ascender/descender are pixel-rounded FreeType size metrics,
and legacy kerning uses Pillow's historical 26.6 convention. Empty text has a zero
Pillow bbox, while a space has advance even though it paints no pixels. Fractional
X/Y origins have opposite half-pixel ties because FreeType's Y axis points upward.
Reference implementation: [Pillow's FreeType driver](https://github.com/python-pillow/Pillow/blob/12.3.0/src/_imagingft.c).

## Implementation

- `basic_text.rs` loads/measures/rasterizes with FreeType entirely in Rust. A bounded
  per-thread face LRU (16 faces, path + file size + modification time) avoids sharing
  mutable FreeType faces across free-threaded workers. Text length, font size, and
  mask allocation remain bounded; errors propagate through the normal fail-open path.
- `measure_text_batch(..., engine="freetype_basic")` exposes BASIC-compatible
  advance/bbox/anchor metrics. The default `engine="skia"` retains the previous API
  behavior for custom-profile callers. `TEXT_METRICS_CAPABILITY` is now 2.
- `Text.engine="freetype_basic"` produces an A8 mask natively, then Skia applies
  the paint/shader, scene transform, clip, and final composition. This is native
  FreeType + Skia, not a call back to Pillow and not CoreText glyph rasterization.
  `IR_CAPABILITY` is now 18 on both sides and in both CI smoke checks.
- IRPainter selects this mode for BASIC non-emoji, non-adaptive text. The CJK
  reference baseline is then measured natively; IRBuilder does not call Pillow to
  normalize that node's baseline. Explicit PIL font inputs retain their actual
  source path instead of losing it to basename normalization.
- Linux uses system FreeType, as Skia already does. macOS statically bundles the
  BASIC scaler so the wheel does not depend on a developer's Homebrew dylib path.

## Verification on macOS

`scripts/skia_text_parity.py` compares both engines against explicit Pillow BASIC,
using three Source Han weights, seven sizes, and seven text cases (147 combinations).
It records advances/bounds plus foreground-only pixel error and writes a visual
comparison. Native BASIC had zero metric mismatches and zero pixel differences on
the white-background corpus. The platform Skia control had metric mismatches in
147 cases, including the empty-string anchor convention.

Tests additionally cover 294 Source Han metric combinations, TrueType legacy kerning,
fractional sizes/origins, dark/transparent backgrounds, concurrent font/size use,
missing fonts, oversized masks, and the absence of Pillow baseline measurement.

The first full page sweep with the new mode produced 64 `ok`, one pre-existing
`profile` budget failure, and two missing custom-profile fixtures. A control run
with the old text engine in the same worktree produced 59 `ok`, six budget failures,
and the same two missing fixtures: 59 renderable cases improved in mean pixel error,
six stayed equal, none worsened. Profile improved from mean 3.435 to 2.999 but still
exceeded its 2.514 budget. Some local assets are absent, so this is a controlled
comparison of text modes, not a claim that the whole production corpus is pristine.
Warm parity on both backends reported zero cache drift.

```bash
uv run maturin develop --release --manifest-path rust/haruki_skia_renderer/Cargo.toml
uv run python -X gil=0 scripts/skia_text_parity.py
uv run pytest -q tests/test_native_basic_text.py tests/test_native_text_metrics.py
uv run python -X gil=0 scripts/skia_parity_sweep.py --out-dir out/parity-native-basic-text
uv run python -X gil=0 scripts/skia_warm_parity.py --backend both
```

## Remaining boundaries

Shared BASIC layout now uses `base/font_metrics.py` and `base/text_layout.py` without
importing Pillow. Legacy font objects and failure recovery remain isolated adapters.
This does not yet retire all Pillow text services: RAQM shaping, emoji raster parity, adaptive
text masks, outline strokes and explicit letter spacing require separate parity
work. IRPainter retains the existing Skia path for RAQM/emoji/adaptive runs, while
explicit BASIC IR rejects unimplemented stroke/adaptive/spacing instead of silently
dropping effects. Font fallback, scaled/rotated scenes and other platforms still
need their own release evidence; a single unscaled glyph corpus cannot prove them.

Shared emoji run measurement now lives in `base/emoji_layout.py`: native BASIC advances plus
font-size emoji squares reproduce the legacy Pilmoji layout grammar without importing Pillow.
The native colored emoji raster remains a separate parity concern.

The ordinary profile scale discrepancy is now resolved: `Canvas.get_img(scale)` resizes the
completed logical raster, whereas the native path previously drew at the enlarged device size.
`Scene.post_resize` (IR capability 25) preserves the logical BASIC glyph masks and runs native
RGBA bilinear filtering before encoding. Profile mean is now 1.547 and p99 is 15, within the
unchanged 2.514 / 32.5 budgets; its drawer and full service request pass the no-Pillow checks.
Explicit `Scene.scale` remains a matrix transform for callers that require that separate contract.

For ImageDraw compositions on a translucent panel, `Text.mask_lerp` opts into
straight-RGBA interpolation by glyph coverage, including the destination alpha.
This uses the same BASIC glyph mask and bounded native readback; ordinary Text
keeps its existing composition. The explicit mode requires a solid fill and an
identity surface outside masked Group layers. The help panel uses it and preserves
its legacy Pillow pixels without any Pillow dependency on the native path.
