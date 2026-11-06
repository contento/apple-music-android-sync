# AGENTS.md

Notes for AI coding agents (and humans) working in this repo. Read the
[README](README.md) for how to use the scripts; this file covers what must
stay true and how to check it.

## What this repo does

Music.app (macOS) → exported `.m3u8` playlists in `Apple/` →
`sync_apple_to_android_list.py` pushes the tracks to an Android player over
`adb` and writes Android playlists. The main device is a **FiiO M21**
(`internal` in the config), played with **FiiO Music**.

| File | Role |
|------|------|
| `export_playlists.py` (`.sh`/`.ps1`) | Music.app / iTunes → `Apple/*.m3u8` |
| `sync_apple_to_android_list.py` (`sync.sh`/`.ps1`) | Push tracks, FLAC folder and playlists to the device |
| `find_tag_variants.py` (`tag_variants.sh`/`.ps1`) | Find/fix tags differing only by case/accents; undo files in `tag_undo/` |
| `clean_music_library.py` | Read-only lister of dead/duplicate library entries |
| `player_config.py` | Loads `sync_apple_to_android_config.toml` (shared by all scripts) |

`sync_apple_to_android_config.toml`, `Apple/` and `Android/` are gitignored
(personal paths). When you add a config key, add it to
`sync_apple_to_android_config.template.toml` and the README table too.

## Rules that must not break

These come from real failures ("no songs available" in FiiO Music):

1. **Playlist paths must match the device's spelling exactly.** FiiO Music
   looks each line up in its own song database by exact path (and falls
   back to reading the file); other players match against Android's media
   library. Android shared storage is case-insensitive, so a wrong path
   still *opens* the file — `adb shell test -f` passes while a player may
   show the track twice or not at all. Verify against the media library
   (see below), not the file system.
2. **Folder case:** the real folder is `/storage/emulated/0/Music` (capital
   M). `resolve_device_case()` fixes the configured roots at runtime; keep it.
3. **Accent encoding:** macOS hands out NFD (decomposed) names; files on the
   device are mostly NFC, a few NFD. Never write a path built from the Mac
   name — use `device_spelling()`, which takes the device's existing
   spelling and falls back to NFC for new files.
4. **Comparisons use `key()`** (NFC + casefold). Lookups go through `key()`;
   what gets *written* is the device's real spelling.
5. **`playlist_copy_dir`** (per device) copies each playlist to FiiO Music's
   import folder `/storage/emulated/0/FiiOMusic/playlist` as
   `<name>.playlist.m3u8`. FiiO Music's "Import m3u playlist" ignores any
   other file name (found by decompiling `PlayListManager.B()`, app 3.3.4)
   and names the playlist from the part before `.playlist.m3u8`; a playlist
   named `Favorites` goes into FiiO's favorites instead. The script never
   deletes in that folder.
6. **`--delete` is destructive** on the device. Run with `--dry-run` first
   and show the user the list. Never touch hidden files or other devices'
   `_playlists/` folders.
7. **`find_tag_variants.py --fix` edits the Music library.** Only run it when
   asked; it writes an undo file to `tag_undo/` first.

## How to verify a change

Requires the FiiO plugged in (`adb devices` shows `model:FiiO_M21`).

```sh
python3 -m py_compile *.py
./sync.sh --player --dry-run        # expect sane counts, root shown as .../Music/_player
./sync.sh --player                  # real run (pushes only what changed)
```

Then check every playlist line in the FiiO import folder exists **verbatim**
in Android's media library:

```sh
adb shell "content query --uri content://media/external/audio/media --projection _data" \
  | tr -d '\r' | sed -n 's/.*_data=//p' > /tmp/lib.txt
rm -rf /tmp/pl && adb pull /storage/emulated/0/FiiOMusic/playlist /tmp/pl >/dev/null
python3 - <<'EOF'
import os
lib = set(open('/tmp/lib.txt', encoding='utf-8').read().splitlines())
bad = 0
for n in sorted(os.listdir('/tmp/pl')):
    lines = [l.strip() for l in open(f'/tmp/pl/{n}', encoding='utf-8')
             if l.strip() and not l.startswith('#')]
    ok = sum(l in lib for l in lines)
    bad += len(lines) - ok
    print(f"{n:24} {ok}/{len(lines)} in library")
print("OK" if bad == 0 else f"FAIL: {bad} paths not in library")
EOF
```

Every file must be named `*.playlist.m3u8` and every playlist must report
N/N. Newly pushed tracks only appear in the
library after the media scan the script triggers; if a fresh push shows
missing, wait a few seconds and re-run the check.

If FiiO Music still imports nothing, read its log while the user runs
**Import m3u playlist**:

```sh
adb logcat -c   # then import on the device, then:
adb logcat -d | grep -E 'PlayListManager|M3uHelper' | grep -E 'FILENAME|importM3uFileToDb|TRUESONG NULL|SAFPATH'
```

`FILENAME:` lines with no `importM3uFileToDb:` means the file names are
wrong; `TRUESONG NULL` means a path could not be read. To check the app's
logic after a FiiO Music update: pull the APK (`adb shell pm path
com.fiio.music`), decompile with `jadx` (`brew install jadx`) and read
`com/fiio/music/util/PlayListManager.java`.

`missing=N` in the sync summary means the file is absent **on the Mac**
(e.g. the playlist was re-exported with tracks not downloaded); that is a
library problem, not a sync bug.

## Conventions

- Python 3.11+ (`tomllib`), standard library only; `adb` from
  `brew install android-platform-tools`.
- Each script has a docstring with version/usage; update it with behavior
  changes, and keep the README in sync.
- Commit messages: short imperative subject ("Let --delete clean up …").
