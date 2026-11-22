#!/usr/bin/env zsh
# Usage:
#   ./ipod.sh --dry-run        # preview: songs to copy, playlist matches
#   ./ipod.sh --limit 10       # test with 10 new songs
#   ./ipod.sh                  # copy new songs, rewrite iTunesDB with Apple/ playlists
set -euo pipefail
cd "$(dirname "$0")"
python3 -u ipod_load.py "$@"
