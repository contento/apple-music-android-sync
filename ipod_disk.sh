#!/usr/bin/env zsh
# ipod_disk.sh — iPod disk-level helpers around vendor/ipodpatcher (macOS).
#
#   ./ipod_disk.sh build                 # compile vendor/ipodpatcher
#   ./ipod_disk.sh info                  # read-only: disk, sector size, partitions
#   ./ipod_disk.sh backup                # save partition map + firmware to ~/ipod-backups/
#   ./ipod_disk.sh convert               # ERASE the iPod: Mac (HFS+) → FAT32
#   ./ipod_disk.sh bootloader-add FILE   # add the Rockbox bootloader (bootloader-ipodvideo.ipod)
#   ./ipod_disk.sh bootloader-remove     # remove it (Apple firmware only again)
#
# The iPod's disk (e.g. disk6) is found by its media name "iPod"; set
# IPOD_DISK=disk6 to choose one yourself. Everything except build and the
# diskutil part of info needs sudo, and you run it, not an agent (AGENTS.md
# rule 8). After convert, ./ipod.sh reloads the music.
set -euo pipefail
cd "$(dirname "$0")"
PATCHER=vendor/ipodpatcher/ipodpatcher
BACKUP_DIR=$HOME/ipod-backups
BACKUP_MB=200   # covers the partition map and the firmware partition (~168 MB)

die() { print -u2 "❌ $*"; exit 1; }

find_disk() {
  if [[ -n ${IPOD_DISK:-} ]]; then print ${IPOD_DISK#/dev/}; return; fi
  local found=() d
  for d in $(diskutil list external physical | awk '/^\/dev\/disk/{sub("/dev/","",$1); print $1}'); do
    diskutil info $d | grep -qE 'Media Name: +iPod$' && found+=$d
  done
  (( ${#found} == 1 )) || die "Expected one iPod, found ${#found} (${found[*]:-none}). Set IPOD_DISK=diskN."
  print $found[1]
}

block_size() { diskutil info $1 | awk -F: '/Device Block Size/{gsub(/[^0-9]/,"",$2); print $2}'; }

need_patcher() { [[ -x $PATCHER ]] || die "Build it first: ./ipod_disk.sh build"; }

latest_backup() { ls -t $BACKUP_DIR/*-$1.img 2>/dev/null | head -1; }

confirm() {
  print -n "Type ERASE to continue, anything else aborts: "
  local answer; read answer
  [[ $answer == ERASE ]] || die "Aborted, nothing written."
}

cmd=${1:-}; (( $# )) && shift
case $cmd in
  build)
    make -C vendor/ipodpatcher
    ;;

  info)
    d=$(find_disk)
    print "💽 /dev/$d  sector size: $(block_size $d) bytes"
    diskutil list $d
    need_patcher
    diskutil unmountDisk /dev/$d >/dev/null
    sudo $PATCHER /dev/$d -l || true
    print "ℹ️  Unmounted for ipodpatcher; replug or run 'diskutil mountDisk /dev/$d' to mount again."
    ;;

  backup)
    d=$(find_disk)
    mkdir -p $BACKUP_DIR
    out=$BACKUP_DIR/$(date +%Y-%m-%d-%H%M%S)-$d.img
    diskutil unmountDisk /dev/$d >/dev/null
    sudo dd if=/dev/r$d of=$out bs=1m count=$BACKUP_MB
    sudo chown $USER $out
    size=$(stat -f %z $out)
    (( size == BACKUP_MB * 1024 * 1024 )) || die "Backup is $size bytes, expected $((BACKUP_MB * 1024 * 1024))."
    sig=$(head -c 2 $out)
    print "✅ $out ($size bytes, starts with '${sig}': ER = Mac partition map)"
    ;;

  convert)
    d=$(find_disk)
    bs=$(block_size $d)
    # ipodpatcher only converts 512-byte-sector iPods; on 2048-byte ones it
    # refuses (and its partition math would be wrong if it did not).
    [[ $bs == 512 ]] || die "/dev/$d has $bs-byte sectors; ipodpatcher --convert only supports 512.
   Restore it in iTunes on Windows instead (it comes back FAT32)."
    need_patcher
    b=$(latest_backup $d)
    [[ -n $b ]] || die "No backup for $d in $BACKUP_DIR. Run ./ipod_disk.sh backup first."
    print "⚠️  This ERASES everything on /dev/$d ($(diskutil info $d | awk -F: '/Disk Size/{print $2}' | xargs))."
    print "   Backup: $b"
    confirm
    diskutil unmountDisk /dev/$d >/dev/null
    sudo $PATCHER /dev/$d --convert
    ;;

  bootloader-add)
    file=${1:-}
    [[ -f $file ]] || die "Usage: ./ipod_disk.sh bootloader-add bootloader-ipodvideo.ipod"
    d=$(find_disk)
    need_patcher
    [[ -n $(latest_backup $d) ]] || die "No backup for $d in $BACKUP_DIR. Run ./ipod_disk.sh backup first."
    diskutil unmountDisk /dev/$d >/dev/null
    sudo $PATCHER /dev/$d -a $file
    ;;

  bootloader-remove)
    d=$(find_disk)
    need_patcher
    diskutil unmountDisk /dev/$d >/dev/null
    sudo $PATCHER /dev/$d -d
    ;;

  *)
    sed -n '2,13p' $0 | sed 's/^# \{0,1\}//'
    exit 1
    ;;
esac
