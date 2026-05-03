# Apple Music → Android Sync

Sync Apple Music playlists to an Android device via Google Drive.
Playlists are exported manually from the Music app, then a Python script copies the tracks and rewrites the paths for Android.

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
    "source_root": "~/Library/Mobile Documents/com~apple~CloudDocs/Media/Music",
    "drive_target": "~/Library/CloudStorage/GoogleDrive-YOUR_EMAIL@gmail.com/My Drive/Music/_player"
  },
  "android_root": "/storage/emulated/0/music/_player"
}
```

| Key | Description |
|-----|-------------|
| `player_root` | Root of this `_player` folder |
| `apple_dir` | Where exported Apple `.m3u8` files live |
| `android_dir` | Where Android `.m3u8` files are written |
| `source_root` | Root of your local iCloud Music library |
| `drive_target` | Google Drive folder that syncs to your phone |
| `android_root` | Absolute path to the music folder **on the Android device** |

> All paths support `~` for the home directory.

---

## Step 3 — Run the sync script

Use the wrapper for your platform — it always runs from the correct directory:

**macOS / Linux**
```bash
./sync.sh            # full sync
./sync.sh --dry-run  # preview only — no files written or copied
```

**Windows (PowerShell)**
```powershell
.\sync.ps1
.\sync.ps1 --dry-run
```

> **First-run note (Windows):** PowerShell may block unsigned scripts.
> Run `Set-ExecutionPolicy -Scope CurrentUser RemoteSigned` once to allow local scripts.

Or call the Python script directly on either platform:
```bash
python3 sync_apple_to_android_list.py
python3 sync_apple_to_android_list.py --dry-run
```

The script will:

1. Read every `.m3u8` in `Apple/`.
2. Copy each referenced track to `drive_target`, preserving the `Artist/Album/Track` folder structure.
   Already-synced files (same size) are skipped — the operation is idempotent.
3. Write an Android-ready `.m3u8` to `Android/` and copy it to `drive_target`.

### Output columns

```
copied=N   tracks newly copied to Drive
already=N  tracks already present (skipped)
drm=N      DRM-protected .m4p files (skipped — not playable on Android)
evicted=N  iCloud placeholder files not yet downloaded to this Mac
missing=N  files referenced in the playlist but not found on disk
outside=N  paths outside source_root (unexpected)
```

---

## Android players

The generated playlists use absolute paths matching `android_root` and work with:

- **Poweramp**
- **Sony Music** (Walkman app)

Make sure Google Drive has finished syncing to the device before scanning for new tracks.

---

## Requirements

- Python 3.9+
- macOS with iCloud Drive and Google Drive for Desktop mounted
- The `Apple/` folder must contain at least one `.m3u8` exported from Music.app
