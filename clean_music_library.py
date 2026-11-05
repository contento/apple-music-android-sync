#!/usr/bin/env python3
"""
clean_music_library.py

Version : v1.0
Created : 2026-11-05

List Music.app library entries that need cleaning up (macOS). Read-only.

  --dead-entries
    List library entries that iCloud marks "duplicate" (the cloud-with-!
    icon) and that point to the same file as another entry. Per file, the
    entry to keep is the first one iCloud does not mark duplicate (or the
    first one if all are).

Remove them by hand in Music.app: select, ⌘⌫, and choose "Keep File".
This script deliberately does not delete: removing a track through
scripting can move its file to the Trash even when another entry still
uses it, leaving the kept entry pointing into the Trash.

Usage:
    python3 clean_music_library.py --dead-entries
"""

from __future__ import annotations

import argparse
import collections
import json
import subprocess
import sys

# JXA: every file track's ID, file path, iCloud status and display tags.
JXA_READ = """
const ft = Application('Music').libraryPlaylists[0].fileTracks;
JSON.stringify({pid: ft.persistentID(), loc: ft.location().map(l => l ? l.toString() : ''),
                status: ft.cloudStatus(), artist: ft.artist(), album: ft.album(), name: ft.name()});
"""


def read_tracks() -> dict[str, list[str]]:
    res = subprocess.run(["osascript", "-l", "JavaScript", "-e", JXA_READ],
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


def main() -> None:
    if sys.platform != "darwin":
        sys.exit("This script only works with Music.app on macOS.")
    parser = argparse.ArgumentParser(description="List Music.app library entries that need cleaning up.")
    parser.add_argument("--dead-entries", action="store_true",
                        help="List iCloud 'duplicate' entries that share a file with another entry.")
    args = parser.parse_args()
    if not args.dead_entries:
        parser.error("choose what to list, e.g. --dead-entries")

    d = read_tracks()
    print(f"{len(d['pid'])} file tracks\n")
    dead = sorted(dead_entries(d), key=lambda i: (d["artist"][i].casefold(), d["album"][i].casefold(),
                                                  d["name"][i].casefold()))
    for i in dead:
        print(f"   {d['artist'][i]} / {d['album'][i]} / {d['name'][i]}")
    print(f"\n{len(dead)} duplicate entries. Remove them in Music.app with ⌘⌫ → Keep File.")


if __name__ == "__main__":
    main()
