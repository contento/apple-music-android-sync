# ipod_disk.ps1 — iPod disk-level helpers around vendor/ipodpatcher (Windows).
#
#   .\ipod_disk.ps1 build                 # compile vendor/ipodpatcher (needs gcc: MinGW-w64 / MSYS2)
#   .\ipod_disk.ps1 info                  # read-only: disk, sector size, partitions
#   .\ipod_disk.ps1 backup                # save partition map + firmware to ~\ipod-backups\
#   .\ipod_disk.ps1 convert               # ERASE the iPod: Mac (HFS+) -> FAT32
#   .\ipod_disk.ps1 bootloader-add FILE   # add the Rockbox bootloader (bootloader-ipodvideo.ipod)
#   .\ipod_disk.ps1 bootloader-remove     # remove it (Apple firmware only again)
#
# The iPod is found with Get-Disk by its name ("Apple iPod USB Device");
# set $env:IPOD_DISK = '2' to pick disk number 2 yourself. Everything except
# build needs an elevated PowerShell (Run as administrator), and you run it,
# not an agent (AGENTS.md rule 8). After convert, .\ipod.ps1 reloads the music.
Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$Root      = $PSScriptRoot
$Vendor    = Join-Path $Root 'vendor\ipodpatcher'
$Patcher   = Join-Path $Vendor 'ipodpatcher.exe'
$BackupDir = Join-Path $HOME 'ipod-backups'
$BackupMB  = 200   # covers the partition map and the firmware partition (~168 MB)

function Die([string]$msg) { Write-Host "❌ $msg" -ForegroundColor Red; exit 1 }

function Need-Admin {
    $id = [Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()
    if (-not $id.IsInRole([Security.Principal.WindowsBuiltInRole]::Administrator)) {
        Die 'Run this from an elevated PowerShell (Run as administrator).'
    }
}

function Need-Patcher {
    if (-not (Test-Path $Patcher)) { Die 'Build it first: .\ipod_disk.ps1 build' }
}

function Find-Disk {
    if ($env:IPOD_DISK) { return Get-Disk -Number ([int]$env:IPOD_DISK) }
    $found = @(Get-Disk | Where-Object { $_.FriendlyName -match 'iPod' })
    if ($found.Count -ne 1) {
        $names = ($found | ForEach-Object { "$($_.Number): $($_.FriendlyName)" }) -join ', '
        Die "Expected one iPod, found $($found.Count) ($names). Set `$env:IPOD_DISK = '<number>'."
    }
    return $found[0]
}

function Latest-Backup([int]$number) {
    Get-ChildItem -Path $BackupDir -Filter "*-disk$number.img" -ErrorAction SilentlyContinue |
        Sort-Object LastWriteTime -Descending | Select-Object -First 1
}

function Confirm-Erase {
    $answer = Read-Host 'Type ERASE to continue, anything else aborts'
    if ($answer -cne 'ERASE') { Die 'Aborted, nothing written.' }
}

function Run-Patcher([int]$number, [string[]]$rest) {
    # On Windows ipodpatcher takes the disk number (it opens \\.\PhysicalDriveN).
    & $Patcher $number @rest
    if ($LASTEXITCODE -ne 0) { Die "ipodpatcher exited with $LASTEXITCODE" }
}

$cmd  = if ($args.Count -gt 0) { $args[0] } else { '' }
$rest = if ($args.Count -gt 1) { $args[1..($args.Count - 1)] } else { @() }

switch ($cmd) {
    'build' {
        if (-not (Get-Command gcc -ErrorAction SilentlyContinue)) {
            Die 'gcc not found. Install MinGW-w64 (e.g. winget install BrechtSanders.WinLibs.POSIX.UCRT) and reopen PowerShell.'
        }
        # Upstream passes VERSION from its build system; a header avoids
        # PowerShell's quoting of -DVERSION="...".
        $hdr = Join-Path $Vendor 'version-win.h'
        Set-Content -Path $hdr -Value '#define VERSION "rockbox-eafcc2c"' -Encoding ascii
        Push-Location $Vendor
        try {
            gcc -O2 -Wall -W -include version-win.h -o ipodpatcher.exe `
                main.c ipodpatcher.c fat32format.c arc4.c ipodio-win32.c ipodio-win32-scsi.c ipodpatcher_aupd.c
            if ($LASTEXITCODE -ne 0) { Die "gcc exited with $LASTEXITCODE" }
        } finally { Pop-Location }
        Write-Host "✅ $Patcher"
    }

    'info' {
        $d = Find-Disk
        Write-Host "💽 Disk $($d.Number): $($d.FriendlyName)  sector size: $($d.LogicalSectorSize) bytes  $([math]::Round($d.Size / 1GB, 1)) GB  $($d.PartitionStyle)"
        Get-Partition -DiskNumber $d.Number -ErrorAction SilentlyContinue | Format-Table -AutoSize
        Need-Admin; Need-Patcher
        & $Patcher $d.Number -l
    }

    'backup' {
        Need-Admin
        $d = Find-Disk
        New-Item -ItemType Directory -Force -Path $BackupDir | Out-Null
        $out = Join-Path $BackupDir ("{0}-disk{1}.img" -f (Get-Date -Format 'yyyy-MM-dd-HHmmss'), $d.Number)
        $want = $BackupMB * 1MB
        $src = New-Object IO.FileStream("\\.\PhysicalDrive$($d.Number)", [IO.FileMode]::Open,
                                        [IO.FileAccess]::Read, [IO.FileShare]::ReadWrite)
        $dst = [IO.File]::Create($out)
        try {
            $buf = New-Object byte[] (1MB)   # a multiple of 512 and 2048-byte sectors
            $done = 0
            while ($done -lt $want) {
                $n = $src.Read($buf, 0, $buf.Length)
                if ($n -le 0) { break }
                $dst.Write($buf, 0, $n); $done += $n
            }
        } finally { $src.Dispose(); $dst.Dispose() }
        $size = (Get-Item $out).Length
        if ($size -ne $want) { Die "Backup is $size bytes, expected $want." }
        $sig = [Text.Encoding]::ASCII.GetString([IO.File]::ReadAllBytes($out)[0..1])
        Write-Host "✅ $out ($size bytes, starts with '$sig': ER = Mac partition map)"
    }

    'convert' {
        Need-Admin
        $d = Find-Disk
        # ipodpatcher only converts 512-byte-sector iPods; on 2048-byte ones it
        # refuses (and its partition math would be wrong if it did not).
        if ($d.LogicalSectorSize -ne 512) {
            Die ("Disk $($d.Number) has $($d.LogicalSectorSize)-byte sectors; ipodpatcher --convert only supports 512.`n" +
                 "   Restore it in iTunes instead (on Windows it comes back FAT32).")
        }
        Need-Patcher
        $b = Latest-Backup $d.Number
        if (-not $b) { Die "No backup for disk $($d.Number) in $BackupDir. Run .\ipod_disk.ps1 backup first." }
        Write-Host "⚠️  This ERASES everything on disk $($d.Number) ($($d.FriendlyName), $([math]::Round($d.Size / 1GB, 1)) GB)."
        Write-Host "   Backup: $($b.FullName)"
        Confirm-Erase
        Run-Patcher $d.Number @('--convert')
    }

    'bootloader-add' {
        $file = if ($rest.Count -gt 0) { $rest[0] } else { '' }
        if (-not $file -or -not (Test-Path $file)) { Die 'Usage: .\ipod_disk.ps1 bootloader-add bootloader-ipodvideo.ipod' }
        Need-Admin; Need-Patcher
        $d = Find-Disk
        if (-not (Latest-Backup $d.Number)) { Die "No backup for disk $($d.Number) in $BackupDir. Run .\ipod_disk.ps1 backup first." }
        Run-Patcher $d.Number @('-a', (Resolve-Path $file).Path)
    }

    'bootloader-remove' {
        Need-Admin; Need-Patcher
        $d = Find-Disk
        Run-Patcher $d.Number @('-d')
    }

    default {
        Get-Content $PSCommandPath | Select-Object -First 13 | ForEach-Object { $_ -replace '^# ?', '' }
        exit 1
    }
}
