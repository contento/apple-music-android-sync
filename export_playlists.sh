#!/usr/bin/env zsh
# Usage (macOS — reads playlists from Music.app; Windows: export_playlists.ps1):
#   ./export_playlists.sh               # re-export the playlists already in Apple/
#   ./export_playlists.sh Balada Tango  # export these playlists
#   ./export_playlists.sh --all         # every playlist you made
#   ./export_playlists.sh --list        # list playlists with track counts
set -euo pipefail
cd "$(dirname "$0")"
python3 -u export_playlists.py "$@"
