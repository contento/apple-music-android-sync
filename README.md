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
├── sync_apple_to_android_config.json       ← your local config (git-ignored)
└── sync_apple_to_android_config.template.json  ← safe template to commit
```

---

## Step 1 — Export playlists from Music (macOS)

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
cp sync_apple_to_android_config.template.json sync_apple_to_android_config.json
```

```powershell
# Windows (PowerShell)
Copy-Item sync_apple_to_android_config.template.json sync_apple_to_android_config.json
```

Edit `sync_apple_to_android_config.json`:

```json
{
  "paths": {
    "player_root": "~/Library/Mobile Documents/com~apple~CloudDocs/Media/_player",
    "apple_dir":   "~/Library/Mobile Documents/com~apple~CloudDocs/Media/_player/Apple",
    "android_dir": "~/Library/Mobile Documents/com~apple~CloudDocs/Media/_player/Android",
    "source_root": "~/Library/Mobile Documents/com~apple~CloudDocs/Media/Music"
  },
  "default_device": "internal",
  "android_root": "/storage/emulated/0/music/_player",
  "devices": {
    "internal": {
      "android_root": "/storage/emulated/0/music/_player"
    },
    "a105": {
      "android_root": "/storage/6135-3539/music/_player"
    }
  }
}
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
| `flac.target_subdir` | Folder under `android_root` for the FLAC files (default `_flac`) |

> All paths support `~` for the home directory.
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
4. `--flac`: queue every file in `flac.source` for `<android_root>/_flac/…`
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

## Android players

The generated playlists use absolute paths matching `android_root` and work with:

- **Poweramp**
- **Sony Music** (Walkman app)

If new tracks don't show up, trigger a library rescan in the player.

---

## Requirements

- Python 3.9+
- macOS with iCloud Drive (music files downloaded locally)
- `adb` and a USB-connected device with USB debugging enabled
- The `Apple/` folder must contain at least one `.m3u8` exported from Music.app
