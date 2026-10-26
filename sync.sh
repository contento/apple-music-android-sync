#!/usr/bin/env zsh
# Usage:
#   ./sync.sh                  # --all: playlists + FLAC (default)
#   ./sync.sh --player         # playlists and their tracks only
#   ./sync.sh --flac           # FLAC folder only
#   ./sync.sh --flac --delete  # also remove device files gone from _flac
#   ./sync.sh --dry-run        # preview only
set -euo pipefail
cd "$(dirname "$0")"
python3 -u sync_apple_to_android_list.py "$@"
