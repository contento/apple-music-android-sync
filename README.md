# Apple Music → Android Sync

Sync Apple Music playlists to an Android device via Google Drive.
Playlists are exported manually from the Music app, then a Python script copies the tracks and rewrites the paths for Android.

---

## Folder layout

```
_player/
├── Apple/          ← exported .m3u8 files from Music.app (input)
├── Android/        ← rewritten .m3u8 files for Android players (output)
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

### On Windows (iTunes)

1. Open **iTunes**.
2. Select the playlist.
3. Go to **File › Library › Export Playlist…**
4. In the Save dialog choose format **M3U** or **M3U8** (prefer M3U8 for Unicode filenames).
5. Save into the `Apple/` folder.

> **Caution — ellipsis on Windows:**
> If you try to set a keyboard shortcut for this menu item in a third-party tool (e.g. AutoHotkey),
> be aware that the menu text in iTunes/Music uses a Unicode ellipsis `…` (U+2026), **not** three
> ASCII periods `...`. Matching against the wrong character will cause the shortcut to fail silently.
> In AutoHotkey you can embed the character with `…` or copy-paste it directly from the menu.

---

## Step 2 — Configure paths

Copy the template and fill in your details:

```bash
cp sync_apple_to_android_config.template.json sync_apple_to_android_config.json
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

```bash
# Preview what would happen (no files written or copied)
python3 sync_apple_to_android_list.py --dry-run

# Full sync
python3 sync_apple_to_android_list.py
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
