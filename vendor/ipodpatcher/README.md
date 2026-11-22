# ipodpatcher (vendored from Rockbox)

Rockbox's tool for iPod partitions and bootloaders, used here to convert a
Mac-formatted (HFS+) iPod Video to FAT32 and install the Rockbox bootloader.

- **Source:** <https://github.com/Rockbox/rockbox/tree/master/utils/ipodpatcher>,
  commit `eafcc2c9de0b6052df2b2a42e83fe42f0e3eaf9c` (2026-11-21), unmodified.
  The C sources for macOS/Linux and Windows are copied; upstream's build
  files, Windows resources and embedded bootloaders are left out.
- **License:** GPL v2 or later (see [COPYING](COPYING) and each file's header).
  It is a separate program; the rest of this repo is not covered by it.
- **Build:** `make` (Xcode command-line tools), or on Windows
  `..\..\ipod_disk.ps1 build` (needs MinGW-w64 gcc). The `ipodpatcher` binary is
  gitignored. Built without `BOOTOBJS`, so `--install` is not available:
  add the bootloader with `-a bootloader-ipodvideo.ipod`.

## Usage (destructive, needs sudo)

```sh
diskutil list external                       # find the iPod, e.g. disk6
diskutil unmountDisk /dev/disk6
sudo ./ipodpatcher /dev/disk6 -l             # read-only: partitions, "Sector size is 512 bytes"
sudo dd if=/dev/rdisk6 of=$HOME/ipod-first-200MB.img bs=1m count=200   # backup first
sudo ./ipodpatcher /dev/disk6 --convert      # ERASES the iPod: HFS+ → FAT32 (asks y/n)
sudo ./ipodpatcher /dev/disk6 -a bootloader-ipodvideo.ipod   # add the Rockbox bootloader
sudo ./ipodpatcher /dev/disk6 -d             # remove it again (Apple firmware only)
```

`--convert` is marked experimental upstream and refuses iPods whose sectors
are not 512 bytes. Afterwards `../../ipod.sh` reloads the music.
