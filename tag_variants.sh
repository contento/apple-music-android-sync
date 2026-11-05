#!/usr/bin/env zsh
# Usage (macOS — reads tags from Music.app; Windows: tag_variants.ps1):
#   ./tag_variants.sh                    # all fields
#   ./tag_variants.sh albumArtist genre  # only these fields
#   Fields: artist, albumArtist, composer, genre, album, name
set -euo pipefail
cd "$(dirname "$0")"
python3 -u find_tag_variants.py "$@"
