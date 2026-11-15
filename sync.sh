#!/usr/bin/env zsh
# Usage:
#   ./sync.sh                  # --all: whole Music library + playlists + FLAC (default)
#   ./sync.sh --player         # Music library + playlists only
#   ./sync.sh --flac           # FLAC folder only
#   ./sync.sh --delete         # also remove device files no longer in the library / FLAC folder
#   ./sync.sh --dry-run        # preview only (add --verbose to list every file)
set -euo pipefail
cd "$(dirname "$0")"
python3 -u sync_apple_to_android_list.py "$@"
