#!/usr/bin/env python3
"""
clean_music_library.py

Version : v1.3
Created : 2026-11-05

List Music.app library entries that need cleaning up (macOS). Read-only,
except --copies --fix.

  --dead-entries
    List library entries that iCloud marks "duplicate" (the cloud-with-!
    icon) and that point to the same file as another entry. Per file, the
    entry to keep is the first one iCloud does not mark duplicate (or the
    first one if all are).

  --copies
    List songs the library holds more than once as separate files: Music.app
    names the second import "<Track> 1.m4a" in the same folder, or the
    entries share artist, album and title (and length within 5 s). For each group it shows bitrate,
    size, length, plays and the exported playlists (Apple/*.m3u8) that use
    each file, and marks with ★ the one to keep: in a playlist, then not
    marked "duplicate" by iCloud, then highest bitrate, then most plays. Delete the others in Music.app with ⌘⌫ →
    "Move to Trash" (they are separate files), or run --copies --fix: it
    writes tag_undo/copies-<timestamp>.json, removes those entries from
    the library and moves their files to the Trash with Finder (Put Back
    works). Entries whose file another entry also uses are skipped, and so
    are songs that have both a lossless (FLAC, ALAC, WAV, AIFF) and a
    compressed version.

  --orphans
    List audio files in the Music folder (source_root) that no library entry
    points to. They never reach the player (the sync copies only library
    tracks). Re-import them with File ▸ Add to Library, or delete them.
    Files are not changed.

  --low-bitrate
    List lossy tracks under 128 kbps (upgrade candidates), most worth
    replacing first: in a playlist, then most plays, then lowest bitrate.
    Read-only.

Remove dead entries by hand in Music.app: select, ⌘⌫, and choose "Keep File".
This script deliberately does not delete: removing a track through
scripting can move its file to the Trash even when another entry still
uses it, leaving the kept entry pointing into the Trash.

Usage:
    python3 clean_music_library.py --dead-entries
    python3 clean_music_library.py --copies
    python3 clean_music_library.py --copies --fix
    python3 clean_music_library.py --orphans
    python3 clean_music_library.py --low-bitrate
"""

from __future__ import annotations

import argparse
import collections
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
import urllib.parse
from pathlib import Path

import player_config

# JXA: every file track's ID, file path, iCloud status and display tags.
JXA_READ = """
const ft = Application('Music').libraryPlaylists[0].fileTracks;
JSON.stringify({pid: ft.persistentID(), loc: ft.location().map(l => l ? l.toString() : ''),
                status: ft.cloudStatus(), artist: ft.artist(), album: ft.album(), name: ft.name()});
"""


# JXA: what --copies shows for each file track. Locations are read one by one
# only when the bulk read fails (a dead entry makes it throw).
JXA_COPIES = """
const ft = Application('Music').libraryPlaylists[0].fileTracks;
let loc;
try { loc = ft.location().map(l => l ? l.toString() : ''); }
catch (e) {
  loc = [];
  for (let j = 0; j < ft.length; j++) {
    try { const l = ft[j].location(); loc.push(l ? l.toString() : ''); } catch (e2) { loc.push(''); }
  }
}
JSON.stringify({loc: loc, pid: ft.persistentID(), status: ft.cloudStatus(),
                artist: ft.artist(), album: ft.album(), name: ft.name(),
                bitrate: ft.bitRate(), size: ft.size(), duration: ft.duration(),
                plays: ft.playedCount()});
"""


# JXA: remove the library entries whose persistent IDs are in argv.
JXA_DELETE = """
function run(argv) {
  const lib = Application('Music').libraryPlaylists[0];
  const gone = [];
  for (const id of argv) {
    const t = lib.tracks.whose({persistentID: id});
    if (t.length) { t[0].delete(); gone.push(id); }
  }
  return JSON.stringify(gone);
}
"""

# AppleScript: move the POSIX paths in argv to the Trash with Finder (keeps "Put Back").
TRASH_SCRIPT = """
on run argv
  tell application "Finder"
    repeat with p in argv
      delete (POSIX file (p as text) as alias)
    end repeat
  end tell
end run
"""

LOSSLESS_EXTENSIONS = {".flac", ".wav", ".aif", ".aiff", ".alac"}


def is_lossless(d: dict[str, list], i: int) -> bool:
    """FLAC/WAV/AIFF by extension; ALAC (.m4a) by its bitrate."""
    ext = os.path.splitext(d["loc"][i])[1].lower()
    return ext in LOSSLESS_EXTENSIONS or (d["bitrate"][i] or 0) >= 500


UNDO_DIR = Path(__file__).resolve().parent / "tag_undo"


def read_tracks(script: str = JXA_READ) -> dict[str, list]:
    res = subprocess.run(["osascript", "-l", "JavaScript", "-e", script],
                         capture_output=True, text=True, encoding="utf-8")
    if res.returncode != 0:
        sys.exit(f"Could not read Music.app library: {res.stderr.strip()}")
    return json.loads(res.stdout)


def dead_entries(d: dict[str, list[str]]) -> list[int]:
    """Indexes of "duplicate" entries that share their file with a kept entry."""
    by_file: dict[str, list[int]] = collections.defaultdict(list)
    for i, loc in enumerate(d["loc"]):
        if loc:
            by_file[loc].append(i)

    dead: list[int] = []
    for indexes in by_file.values():
        if len(indexes) < 2:
            continue
        keep = next((i for i in indexes if d["status"][i] != "duplicate"), indexes[0])
        dead += [i for i in indexes if i != keep and d["status"][i] == "duplicate"]
    return dead


def nfc(s: str) -> str:
    return unicodedata.normalize("NFC", s).casefold()


def playlist_users() -> dict[str, list[str]]:
    """{file path: [playlist names]} from the exported Apple/*.m3u8 files."""
    apple = player_config.load_config().get("paths", {}).get("apple_dir")
    apple_dir = Path(apple).expanduser() if apple else Path(__file__).resolve().parent / "Apple"
    users: dict[str, list[str]] = collections.defaultdict(list)
    for m3u in sorted(apple_dir.glob("*.m3u8")):
        for line in m3u.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                if line.startswith("file://"):
                    line = urllib.parse.unquote(urllib.parse.urlparse(line).path)
                users[nfc(line)].append(m3u.stem)
    return users


def copy_groups(d: dict[str, list]) -> list[list[int]]:
    """Groups of entries (distinct files) that hold the same song."""
    parent = list(range(len(d["loc"])))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    by_file: dict[str, int] = {}
    seen: dict[tuple, list[int]] = collections.defaultdict(list)
    for i, loc in enumerate(d["loc"]):
        if not loc or nfc(loc) in by_file:
            continue  # cloud-only, or a second entry for the same file (see --dead-entries)
        by_file[nfc(loc)] = i
        folder, base = os.path.split(nfc(loc))
        stem, ext = os.path.splitext(base)
        keys = [("file", folder, re.sub(r" \d+$", "", stem), ext),
                ("tags", nfc(d["artist"][i]), nfc(d["album"][i]), nfc(d["name"][i]))]
        for k in keys:
            for j in seen[k]:
                # Same tags but a different length is a different recording.
                if k[0] == "file" or abs((d["duration"][i] or 0) - (d["duration"][j] or 0)) <= 5:
                    parent[find(i)] = find(j)
            seen[k].append(i)
    groups: dict[int, list[int]] = collections.defaultdict(list)
    for i in by_file.values():
        groups[find(i)].append(i)
    return [g for g in groups.values() if len(g) > 1]


def list_copies(fix: bool) -> None:
    d = read_tracks(JXA_COPIES)
    shared = collections.Counter(nfc(l) for l in d["loc"] if l)
    users = playlist_users()
    groups = copy_groups(d)
    groups.sort(key=lambda g: (d["artist"][g[0]].casefold(), d["album"][g[0]].casefold(),
                               d["name"][g[0]].casefold()))
    print(f"{len(d['loc'])} file tracks\n")
    remove: list[int] = []
    kept_both = 0
    for g in groups:
        both = len({is_lossless(d, i) for i in g}) == 2
        def rank(i: int) -> tuple:
            return (bool(users.get(nfc(d["loc"][i]))), d["status"][i] != "duplicate",
                    d["bitrate"][i] or 0, d["plays"][i] or 0)
        keep = -1 if both else max(g, key=rank)
        note = "  (lossless + compressed: left alone)" if both else ""
        print(f"{d['artist'][g[0]]} / {d['album'][g[0]]} / {d['name'][g[0]]}{note}")
        for i in sorted(g, key=lambda i: d["loc"][i]):
            secs = int(d["duration"][i] or 0)
            in_pl = ", ".join(users.get(nfc(d["loc"][i]), [])) or "-"
            print(f"  {'★' if i == keep else ' '} {d['bitrate'][i] or 0:>4} kbps  "
                  f"{(d['size'][i] or 0) / 1e6:5.1f} MB  {secs // 60}:{secs % 60:02}  "
                  f"{d['status'][i]:<9} plays={d['plays'][i] or 0:<3} playlists={in_pl}  "
                  f"{os.path.basename(d['loc'][i])}")
        if both:
            kept_both += 1
            continue
        remove += [i for i in g if i != keep]
    print(f"\n{len(groups)} songs with copies, {len(remove)} files to remove.")
    if kept_both:
        print(f"  {kept_both} songs have a lossless and a compressed version; they are left alone.")
    in_playlists = [i for i in remove if users.get(nfc(d["loc"][i]))]
    if in_playlists:
        print(f"  ⚠ {len(in_playlists)} of them are in a playlist too; the playlist loses that entry.")
    if not fix:
        print("Run with --fix to remove them (library entry + file to the Trash), then "
              "re-export the playlists and ./sync.sh --delete.")
        return
    remove_copies(d, [i for i in remove if shared[nfc(d["loc"][i])] == 1], users)
    skipped = [i for i in remove if shared[nfc(d["loc"][i])] > 1]
    for i in skipped:
        print(f"  ⊘ skipped, another entry shares the file (see --dead-entries): {d['loc'][i]}")


def remove_copies(d: dict[str, list], remove: list[int], users: dict[str, list[str]]) -> None:
    """Delete the entries from Music.app and move their files to the Trash."""
    UNDO_DIR.mkdir(exist_ok=True)
    record = UNDO_DIR / f"copies-{time.strftime('%Y%m%d-%H%M%S')}.json"
    record.write_text(json.dumps([
        {k: d[k][i] for k in ("pid", "loc", "artist", "album", "name", "bitrate", "plays", "status")}
        | {"playlists": users.get(nfc(d["loc"][i]), [])}
        for i in remove], ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"\nRecord of removed entries: {record}  (files go to the Trash; drag back to re-add)")

    ids = [d["pid"][i] for i in remove]
    res = subprocess.run(["osascript", "-l", "JavaScript", "-e", JXA_DELETE, *ids],
                         capture_output=True, text=True, encoding="utf-8")
    if res.returncode != 0:
        sys.exit(f"Could not delete from Music.app: {res.stderr.strip()}")
    print(f"  removed {len(json.loads(res.stdout))} library entries")

    files = [d["loc"][i] for i in remove if os.path.exists(d["loc"][i])]
    for k in range(0, len(files), 50):
        res = subprocess.run(["osascript", "-e", TRASH_SCRIPT, *files[k:k + 50]],
                             capture_output=True, text=True, encoding="utf-8")
        if res.returncode != 0:
            sys.exit(f"Could not move files to the Trash: {res.stderr.strip()}")
    print(f"  moved {len(files)} files to the Trash")


def list_low_bitrate(limit_kbps: int = 128) -> None:
    """Lossy tracks below `limit_kbps`, ranked by how much an upgrade would be heard."""
    d = read_tracks(JXA_COPIES)
    users = playlist_users()
    low = [i for i in range(len(d["pid"]))
           if 0 < (d["bitrate"][i] or 0) < limit_kbps and not is_lossless(d, i)]

    def path(i: int) -> str:
        p = d["loc"][i]
        return urllib.parse.unquote(urllib.parse.urlparse(p).path) if p.startswith("file:") else p

    def lists(i: int) -> list[str]:
        return users.get(nfc(path(i)), [])

    low.sort(key=lambda i: (not lists(i), -(d["plays"][i] or 0), d["bitrate"][i],
                            d["artist"][i].casefold(), d["name"][i].casefold()))
    print(f"{len(d['pid'])} file tracks\n")
    print(f"   {'kbps':>4}  {'plays':>5}  track  [playlists]")
    for i in low:
        pl = lists(i)
        print(f"   {d['bitrate'][i]:>4}  {d['plays'][i] or 0:>5}  {d['artist'][i]} – {d['name'][i]}"
              f"  ({d['album'][i]})" + (f"  [{', '.join(sorted(set(pl)))}]" if pl else ""))
    in_pl = sum(1 for i in low if lists(i))
    print(f"\n{len(low)} lossy tracks under {limit_kbps} kbps; {in_pl} are in a playlist, "
          f"{sum(1 for i in low if d['plays'][i])} have been played.")


AUDIO_EXTENSIONS = {".mp3", ".m4a", ".m4p", ".aac", ".flac", ".wav", ".aif", ".aiff", ".ogg", ".opus", ".wma"}


def list_orphans() -> None:
    """Audio files under source_root that no library entry points to."""
    root = Path(player_config.load_config()["paths"]["source_root"]).expanduser()
    d = read_tracks(JXA_COPIES)
    known = {nfc(urllib.parse.unquote(urllib.parse.urlparse(p).path) if p.startswith("file:") else p)
             for p in d["loc"] if p}
    orphans = sorted(
        os.path.join(r, f) for r, _, files in os.walk(root) for f in files
        if not f.startswith(".") and os.path.splitext(f)[1].lower() in AUDIO_EXTENSIONS
        and nfc(os.path.join(r, f)) not in known)
    print(f"{len(d['loc'])} library entries\n")
    for p in orphans:
        print(f"   {os.path.relpath(p, root)}   ({os.path.getsize(p) / 1e6:.1f} MB)")
    print(f"\n{len(orphans)} audio files not in the library. Add them with File ▸ Add to Library, or delete them.")


def main() -> None:
    if sys.platform != "darwin":
        sys.exit("This script only works with Music.app on macOS.")
    parser = argparse.ArgumentParser(description="List Music.app library entries that need cleaning up.")
    parser.add_argument("--dead-entries", action="store_true",
                        help="List iCloud 'duplicate' entries that share a file with another entry.")
    parser.add_argument("--copies", action="store_true",
                        help="List songs held more than once as separate files, with the one to keep.")
    parser.add_argument("--orphans", action="store_true",
                        help="List audio files in the Music folder that are not in the library.")
    parser.add_argument("--low-bitrate", action="store_true",
                        help="List lossy tracks under 128 kbps, most worth replacing first.")
    parser.add_argument("--fix", action="store_true",
                        help="With --copies: remove the copies not marked ★ (entry + file to the Trash).")
    args = parser.parse_args()
    if not args.dead_entries and not args.copies and not args.orphans and not args.low_bitrate:
        parser.error("choose what to list: --dead-entries, --copies, --orphans or --low-bitrate")
    if args.low_bitrate:
        list_low_bitrate()
        if not args.dead_entries and not args.copies and not args.orphans:
            return
        print()
    if args.orphans:
        list_orphans()
        if not args.dead_entries and not args.copies:
            return
        print()
    if args.fix and not args.copies:
        parser.error("--fix works with --copies only")
    if args.copies:
        list_copies(args.fix)
        if not args.dead_entries:
            return
        print()

    d = read_tracks()
    print(f"{len(d['pid'])} file tracks\n")
    dead = sorted(dead_entries(d), key=lambda i: (d["artist"][i].casefold(), d["album"][i].casefold(),
                                                  d["name"][i].casefold()))
    for i in dead:
        print(f"   {d['artist'][i]} / {d['album'][i]} / {d['name'][i]}")
    print(f"\n{len(dead)} duplicate entries. Remove them in Music.app with ⌘⌫ → Keep File.")


if __name__ == "__main__":
    main()
