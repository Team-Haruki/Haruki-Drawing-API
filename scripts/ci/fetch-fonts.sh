#!/usr/bin/env bash
# OFL/CC fonts the service refuses to start without (too large to vendor). Same URLs as the
# old "Download CI fonts" steps; skips files that already exist.
set -euo pipefail
cd "$(dirname "$0")/../.."
mkdir -p data
for weight in Regular Bold Heavy; do
  f="data/SourceHanSansSC-${weight}.otf"
  [ -s "$f" ] || curl -fsSL --retry 3 -o "$f" \
    "https://github.com/adobe-fonts/source-han-sans/raw/release/OTF/SimplifiedChinese/SourceHanSansSC-${weight}.otf"
done
# COLR-format Twemoji: FreeType/Skia render it in color (set HARUKI_FONT__EMOJI=TwemojiMozilla).
[ -s data/TwemojiMozilla.ttf ] || curl -fsSL --retry 3 -o data/TwemojiMozilla.ttf \
  "https://github.com/mozilla/twemoji-colr/releases/download/v0.7.0/Twemoji.Mozilla.ttf"
ls -l data/SourceHanSansSC-*.otf data/TwemojiMozilla.ttf
