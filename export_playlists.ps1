# Usage (Windows — reads playlists from iTunes; starts it if it isn't running):
#   .\export_playlists.ps1               # re-export the playlists already in Apple\
#   .\export_playlists.ps1 Balada Tango  # export these playlists
#   .\export_playlists.ps1 --all         # every playlist you made
#   .\export_playlists.ps1 --list        # list playlists with track counts
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
# Print accented names correctly in the console and when redirected.
$env:PYTHONUTF8 = '1'
Push-Location $PSScriptRoot
try {
    python -u export_playlists.py @args
} finally {
    Pop-Location
}
