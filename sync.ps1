# Usage:
#   .\sync.ps1            # full sync
#   .\sync.ps1 --dry-run  # preview only
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
Push-Location $PSScriptRoot
try {
    python sync_apple_to_android_list.py @args
} finally {
    Pop-Location
}
