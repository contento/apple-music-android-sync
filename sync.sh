#!/usr/bin/env zsh
# Usage:
#   ./sync.sh            # full sync
#   ./sync.sh --dry-run  # preview only
set -euo pipefail
cd "$(dirname "$0")"
python3 sync_apple_to_android_list.py "$@"
