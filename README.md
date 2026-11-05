# Apple Music → Android Sync

```text
  o  o
\______/
  |
     |    https://conten.to
--------
```


Sync Apple Music playlists and a FLAC library to an Android device over USB (`adb`).
Playlists are exported manually from the Music app, then a Python script pushes the tracks to the phone and rewrites the playlist paths for Android.

---

## Folder layout

```
_player/
├── Apple/          ← exported .m3u8 files from Music.app (input)
├── Android/        ← rewritten .m3u8 files for Android players (created by the script)
├── sync.sh                                 ← shell wrapper (macOS / Linux)
├── sync.ps1                                ← PowerShell wrapper (Windows)
├── sync_apple_to_android_list.py
├── export_playlists.sh                     ← shell wrapper for export_playlists.py (macOS)
├── export_playlists.ps1                    ← PowerShell wrapper for export_playlists.py (Windows / iTunes)
├── export_playlists.py                     ← export Music.app / iTunes playlists to Apple/
├── tag_variants.sh                         ← shell wrapper for find_tag_variants.py (macOS)
├── tag_variants.ps1                        ← PowerShell wrapper for find_tag_variants.py (Windows / iTunes)
├── find_tag_variants.py                    ← report Music.app tags that differ only by case/accents
├── clean_music_library.py                  ← list duplicate library entries (macOS, read-only)
├── player_config.py                        ← shared config loader
├── sync_apple_to_android_config.toml       ← your local config (git-ignored)
└── sync_apple_to_android_config.template.toml  ← safe template to commit
```

---

## Step 1 — Export playlists from Music (macOS)

### Scripted (recommended)

```bash
./export_playlists.sh               # re-export the playlists already in Apple/
./export_playlists.sh Balada Tango  # export these playlists
./export_playlists.sh --all         # every playlist you made
./export_playlists.sh --list        # list playlists with track counts
```

```powershell
.\export_playlists.ps1              # Windows: reads from iTunes
.\export_playlists.ps1 --all
```

Files are written in the same format as Music's own export. Like Music, it
only writes tracks that have a local file; cloud-only entries are skipped and
counted (`(N cloud-only skipped)`). Smart playlists are
ignored everywhere. Files in `Apple/` with no matching
playlist are left alone.

### By hand

1. Open **Music.app**.
2. Select the playlist in the sidebar.
3. Use **File › Library › Export Playlist…** to save it as an `.m3u8` file.
   Keyboard shortcut: `Ctrl + Option + E`
4. Save the file into the `Apple/` folder inside `_player`.
5. Repeat for each playlist you want to sync.

> **Tip:** The menu item reads "Export Playlist…" with a Unicode ellipsis character (`…`, U+2026).
> If you assign a custom shortcut via **System Settings › Keyboard › Keyboard Shortcuts › App Shortcuts**,
> the menu title you type must use that exact character — **not three separate dots** (`...`).
> On macOS you can type `…` with `Option + ;`.

### On Windows (iTunes / Apple Music for Windows)

1. Open **iTunes** (or **Apple Music** if you have it from the Microsoft Store).
2. Select the playlist in the sidebar.
3. Go to **File › Library › Export Playlist…**
4. In the Save dialog choose format **M3U8** (preserves Unicode filenames).
5. Save into the `Apple\` folder inside `_player`.
6. Repeat for each playlist you want to sync.

> **Caution — ellipsis character:**
> The menu item ends with a Unicode ellipsis `…` (U+2026), **not** three ASCII periods `...`.
> If you assign a keyboard shortcut via a third-party tool (e.g. AutoHotkey), you must match
> the exact character or the shortcut will fail silently.
> In AutoHotkey use `…` or copy-paste the character directly from the menu title.

---

## Step 2 — Configure paths

Copy the template and fill in your details:

```bash
# macOS / Linux
cp sync_apple_to_android_config.template.toml sync_apple_to_android_config.toml
```

```powershell
# Windows (PowerShell)
Copy-Item sync_apple_to_android_config.template.toml sync_apple_to_android_config.toml
```

Edit `sync_apple_to_android_config.toml` (`#` starts a comment):

```toml
default_device = "internal"
android_root = "/storage/emulated/0/music/_player"

[paths]
player_root = "~/Library/Mobile Documents/com~apple~CloudDocs/Media/_player"
apple_dir   = "~/Library/Mobile Documents/com~apple~CloudDocs/Media/_player/Apple"
android_dir = "~/Library/Mobile Documents/com~apple~CloudDocs/Media/_player/Android"
source_root = "~/Library/Mobile Documents/com~apple~CloudDocs/Media/Music"

[devices.internal]
android_root = "/storage/emulated/0/music/_player"

[devices.a105]   # SD card
android_root = "/storage/6135-3539/music/_player"

[flac]
source = "~/Library/Mobile Documents/com~apple~CloudDocs/Media/_flac"
target_subdir = "_flac"
```

| Key | Description |
|-----|-------------|
| `player_root` | Root of this `_player` folder |
| `apple_dir` | Where exported Apple `.m3u8` files live |
| `android_dir` | Where Android `.m3u8` files are written |
| `source_root` | Root of your local iCloud Music library |
| `default_device` | Device key used when you do not pass `--device` |
| `android_root` | Absolute path to the music folder **on the Android device** |
| `devices.<name>.android_root` | Named Android roots you can select with `--device` |
| `flac.source` | Local FLAC library (Artist/Album/Track) |
| `flac.target_subdir` | FLAC folder name, created next to `android_root` (default `_flac` → `/storage/emulated/0/music/_flac`) |
| `devices.<name>.flac_root` | Optional absolute FLAC folder on that device, overriding the default |

> All paths support `~` for the home directory.
>
> Reading TOML needs Python 3.11 or newer. An older `sync_apple_to_android_config.json`
> with the same keys still works when there is no `.toml` file.
>
> `android_root` is kept for backward compatibility and also acts as the fallback for `internal`.

---

## Step 3 — Connect the phone (one-time)

1. Install adb: `brew install android-platform-tools`
2. On the phone: **Settings › About phone**, tap **Build number** 7 times, then turn on **Developer options › USB debugging**.
3. Plug in the USB cable and accept the **Allow USB debugging** prompt. `adb devices` should list the phone as `device`.

---

## Step 4 — Run the sync script

Use the wrapper for your platform — it always runs from the correct directory:

**macOS / Linux**
```bash
./sync.sh                  # --all: playlists + FLAC (default)
./sync.sh --player         # playlists and their tracks only
./sync.sh --flac           # FLAC folder only
./sync.sh --flac --delete  # also remove device files no longer in _flac
                           # (careful: includes .lrc/.jpg the player downloaded)
./sync.sh --dry-run        # preview only — works without the phone attached
```

**Windows (PowerShell)**
```powershell
.\sync.ps1
.\sync.ps1 --player
.\sync.ps1 --flac
.\sync.ps1 --dry-run
```

> **First-run note (Windows):** PowerShell may block unsigned scripts.
> Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once to allow local scripts.

Or call the Python script directly on either platform:
```bash
python3 sync_apple_to_android_list.py
python3 sync_apple_to_android_list.py --player --dry-run
python3 sync_apple_to_android_list.py --flac --device a105
```

`--all`, `--player` and `--flac` are mutually exclusive; `--all` is the default.
`--device` selects a key from `devices` in your config file.
If omitted, the script uses `default_device` (or `internal` if not set).

The script will:

1. Check that exactly one authorized device is attached (set `ANDROID_SERIAL` if several are).
2. List the files already under `android_root` on the device, with sizes.
3. `--player`: read every `.m3u8` in `Apple/`, queue each referenced track for
   `<android_root>/<Artist>/<Album>/<Track>`, and write an Android-ready playlist to
   `Android/_playlists/<device>/` (pushed to `<android_root>/_playlists/<device>/`).
4. `--flac`: queue every file in `flac.source` for `<flac_root>/…`, by default
   `<parent of android_root>/_flac` (e.g. `/storage/emulated/0/music/_flac`)
   (`.DS_Store` and iCloud placeholders are skipped).
5. Check free space on the device, push only files that are missing or differ in size,
   and ask Android's media scanner to index them. Re-runs are idempotent.

### Output columns

```
files=N    tracks in the playlist / files in the FLAC folder
to push=N  files missing on the device or different in size
already=N  files already on the device (skipped)
evicted=N  iCloud placeholder files not yet downloaded to this Mac
missing=N  files referenced in the playlist but not found on disk
drm=N      DRM-protected .m4p files (skipped — not playable on Android)
```

Paths outside `source_root` are reported inline as `⊘ outside source root`.

---

## Duplicate library entries (cloud with "!")

iCloud marks a second library entry for the same file as *duplicate*; Music
shows it with a cloud-and-exclamation icon. To list them:

```bash
python3 clean_music_library.py --dead-entries
```

Remove them in Music.app: select, `⌘⌫`, and choose **Keep File**. The script
does not delete on purpose — removing a track through scripting can move the
shared file to the Trash.

---

## Android players

The generated playlists use absolute paths matching `android_root` and work with:

- **Poweramp**
- **Sony Music** (Walkman app)

If new tracks don't show up, trigger a library rescan in the player.

---

## Finding duplicate spellings in Music.app

Players group by tags, so `Miguel Bose` and `Miguel Bosé` (or `toto` and `Toto`)
show up as two artists. To list every tag value that differs only by case,
accents or spacing:

```bash
./tag_variants.sh                  # artist, albumArtist, composer, genre, album, name
./tag_variants.sh albumArtist      # only some fields
```

```powershell
.\tag_variants.ps1                 # Windows: reads from iTunes
.\tag_variants.ps1 albumArtist
```

On Windows this needs **iTunes**: the Apple Music app from the Microsoft Store
has no scripting interface. Reading tags over COM is slower than on macOS,
so a large library takes a while.

Each line is one group with its track counts, most common first:

```text
── Album Artist: 16 groups
   Miguel Bose (47)  |  Miguel Bosé (18)
```

Fix the less common spelling in Music.app (search, select, `⌘I`) or iTunes
(`Ctrl+I`), then re-export the playlists. On macOS the first run asks for
permission to control Music.app.

On macOS the script can fix them for you:

```bash
./tag_variants.sh --fix                              # show the plan, ask, then apply
./tag_variants.sh --fix --yes                        # apply without asking
./tag_variants.sh --undo tag_undo/20261105-144134.json   # restore a run
```

Per word, the accented form wins (`Bose` → `Bosé`), real words are capitalised and
short words (`de`, `la`, `the`, `of`…) lowercase mid-name (`toto` → `Toto`,
`Heal The World` → `Heal the World`), and extra spaces are removed. When the
rules pick wrong, list the right spelling in the config — it is applied to every
matching value:

```toml
tag_overrides = ["Diomedes Díaz", "Juan Zaizar"]
```

When Music.app saves a track it re-cases other fields to spellings it already
knows, so run `--fix` again until it reports *Nothing to fix*. If "Keep Music
Media folder organized" is on, renamed artists/albums move files to new
folders: **re-export the playlists** before the next sync.

---

## Requirements

- Python 3.9+
- macOS with iCloud Drive (music files downloaded locally)
- `adb` and a USB-connected device with USB debugging enabled
- The `Apple/` folder must contain at least one `.m3u8` exported from Music.app
