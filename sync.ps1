# Usage:
#   .\sync.ps1                  # --all: playlists + FLAC (default)
#   .\sync.ps1 --player         # playlists and their tracks only
#   .\sync.ps1 --flac           # FLAC folder only
#   .\sync.ps1 --flac --delete  # also remove device files gone from _flac
#   .\sync.ps1 --dry-run        # preview only
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    python sync_apple_to_android_list.py @args
} finally {
    Pop-Location
}
