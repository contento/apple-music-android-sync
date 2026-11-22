# Usage (Windows — the iPod shows up as a drive letter, e.g. E:\; it must be
# FAT32 / "Windows format", a Mac-formatted iPod cannot be read on Windows):
#   .\ipod.ps1 --dry-run        # preview: songs to copy, playlist matches
#   .\ipod.ps1 --limit 10       # test with 10 new songs
#   .\ipod.ps1                  # copy new songs, rewrite iTunesDB with Apple/ playlists
#   .\ipod.ps1 --ipod E:\       # if more than one iPod is plugged in
# Needs ffprobe on PATH (winget install Gyan.FFmpeg).
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
# Print accented names and emoji correctly in the console and when redirected.
$env:PYTHONUTF8 = '1'
Push-Location $PSScriptRoot
try {
    python -u ipod_load.py @args
} finally {
    Pop-Location
}
