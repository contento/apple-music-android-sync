# Usage (Windows — reads tags from iTunes; starts it if it isn't running):
#   .\tag_variants.ps1                    # all fields
#   .\tag_variants.ps1 albumArtist genre  # only these fields
#   Fields: artist, albumArtist, composer, genre, album, name
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
# Print accented names correctly in the console and when redirected.
$env:PYTHONUTF8 = '1'
Push-Location $PSScriptRoot
try {
    python -u find_tag_variants.py @args
} finally {
    Pop-Location
}
