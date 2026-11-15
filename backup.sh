#!/usr/bin/env zsh
# Back up Apple/, Android/ and sync_apple_to_android_config.toml into an
# ISO-dated folder (YYYY-MM-DD) in iCloud Drive. If today's folder already
# exists, it is deleted and replaced, so it always mirrors the current state.
#
# Usage:
#   ./backup.sh                # back up to the default iCloud folder
#   ./backup.sh /some/dir      # back up somewhere else
set -euo pipefail
cd "$(dirname "$0")"

dest_root="${1:-$HOME/Library/Mobile Documents/com~apple~CloudDocs/WIP/apple-music-android-sync}"
items=(Apple Android sync_apple_to_android_config.toml)

for item in $items; do
  [[ -e $item ]] || { print -u2 "Missing: $item"; exit 1; }
done

dest="$dest_root/$(date +%F)"

rm -rf "$dest"
mkdir -p "$dest"
cp -Rp $items "$dest/"
print "Backed up ${(j:, :)items} to:\n  $dest"
