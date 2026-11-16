#!/usr/bin/env python3
"""
fetch_artwork.py

Version : v1.0
Created : 2026-11-16

Find cover art for Music.app albums whose tracks have no artwork (macOS).
Looks each album up on MusicBrainz and downloads its front cover from the
Cover Art Archive (both free, no key). Only albums with a clear match are
used: same title (case/accents ignored) and artist, MusicBrainz score 90+.

  (no flag)  Match and download into art_cache/, print a report. Nothing
             in Music.app changes.
  --apply    Also embed the downloaded cover in every artwork-less track
             of those albums. Tracks that already have art are never touched.
             Undo: remove the artwork in Music.app (Get Info ▸ Artwork).

Albums with no album tag are skipped (nothing to look up).
Misses are remembered in art_cache/misses.json; --retry looks again.

Usage:
    python3 fetch_artwork.py                 # dry run: match + download + report
    python3 fetch_artwork.py --apply         # embed the covers
    python3 fetch_artwork.py --artist "Niche"

Sends artist and album names to musicbrainz.org and coverartarchive.org.
"""

from __future__ import annotations

import argparse
import collections
import json
import subprocess
import sys
import time
import unicodedata
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

HERE = Path(__file__).resolve().parent
CACHE = HERE / "art_cache"
HEADERS = {"User-Agent": "apple-music-android-sync artwork fetcher (personal use)"}

JXA_READ = """
const t = Application('Music').libraryPlaylists[0].fileTracks;
JSON.stringify({pid: t.persistentID(), artist: t.artist(), albumArtist: t.albumArtist(),
                album: t.album(), name: t.name(), art: t.artworks().map(a => a.length)});
"""

APPLY_SCRIPT = """
on run argv
  set pic to read (POSIX file (item 1 of argv)) as picture
  tell application "Music"
    repeat with i from 2 to count of argv
      try
        set t to (first track of library playlist 1 whose persistent ID is (item i of argv))
        if (count of artworks of t) is 0 then set data of artwork 1 of t to pic
      end try
    end repeat
  end tell
end run
"""


def fold(s: str) -> str:
    s = unicodedata.normalize("NFD", s or "").casefold()
    return "".join(c for c in s if c.isalnum())


def get(url: str, binary: bool = False):
    for attempt in range(3):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=30) as r:
                data = r.read()
                return data if binary else json.loads(data)
        except urllib.error.HTTPError as e:
            if e.code in (404, 400):
                return None
            time.sleep(3 * (attempt + 1))
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            time.sleep(3 * (attempt + 1))
    return None


def find_cover(artist: str, album: str) -> tuple[bytes, str] | None:
    """(jpeg bytes, "MusicBrainz release title") of a clear match, or None."""
    query = f'release:"{album}" AND artist:"{artist}"'
    res = get("https://musicbrainz.org/ws/2/release?" + urllib.parse.urlencode(
        {"query": query, "fmt": "json", "limit": 8}))
    time.sleep(1.1)  # MusicBrainz allows one request per second
    if not res:
        return None
    for rel in res.get("releases", []):
        credit = " ".join(c.get("name", "") for c in rel.get("artist-credit", []))
        if rel.get("score", 0) < 90 or fold(rel.get("title", "")) != fold(album):
            continue
        if fold(artist) not in fold(credit) and fold(credit) not in fold(artist):
            continue
        img = get(f"https://coverartarchive.org/release/{rel['id']}/front-500", binary=True)
        if img and img[:2] == b"\xff\xd8":  # JPEG only
            return img, f"{rel['title']} ({credit})"
    return None


def main() -> None:
    if sys.platform != "darwin":
        sys.exit("This script reads the library from Music.app, so it only works on macOS.")
    parser = argparse.ArgumentParser(description="Find cover art for albums without artwork.")
    parser.add_argument("--apply", action="store_true", help="Embed the covers in Music.app.")
    parser.add_argument("--artist", metavar="NAME", help="Only albums whose artist contains NAME.")
    parser.add_argument("--retry", action="store_true", help="Look up earlier misses again.")
    args = parser.parse_args()

    res = subprocess.run(["osascript", "-l", "JavaScript", "-e", JXA_READ],
                         capture_output=True, text=True, encoding="utf-8")
    if res.returncode != 0:
        sys.exit(f"Could not read Music.app: {res.stderr.strip()}")
    d = json.loads(res.stdout)

    albums: dict[tuple[str, str], list[str]] = collections.defaultdict(list)
    skipped = 0
    for i, pid in enumerate(d["pid"]):
        if d["art"][i]:
            continue
        if not d["album"][i]:
            skipped += 1
            continue
        artist = d["albumArtist"][i] or d["artist"][i]
        if args.artist and args.artist.casefold() not in artist.casefold():
            continue
        albums[(artist, d["album"][i])].append(pid)
    print(f"{sum(len(v) for v in albums.values())} tracks without art in {len(albums)} albums "
          f"({skipped} more have no album tag and are skipped)\n")

    CACHE.mkdir(exist_ok=True)
    misses_file = CACHE / "misses.json"
    misses = set(json.loads(misses_file.read_text())) if misses_file.exists() and not args.retry else set()
    found = missed = embedded = 0
    start = time.time()
    try:
        for n, ((artist, album), pids) in enumerate(sorted(albums.items()), 1):
            name = f"{fold(artist)[:40]}-{fold(album)[:60]}"
            image = CACHE / f"{name}.jpg"
            note = ""
            if f"{artist}|{album}" in misses and not image.exists():
                missed += 1
                continue
            if not image.exists():
                hit = find_cover(artist, album)
                if hit:
                    image.write_bytes(hit[0])
                    note = hit[1]
                else:
                    misses.add(f"{artist}|{album}")
                    missed += 1
                    print(f"  ✗ {artist} / {album}")
                    continue
            found += 1
            print(f"  ✓ {artist} / {album} → {note or 'cached'}  ({len(pids)} tracks)")
            if args.apply:
                r = subprocess.run(["osascript", "-e", APPLY_SCRIPT, str(image), *pids],
                                   capture_output=True, text=True)
                if r.returncode != 0:
                    print(f"      could not embed: {r.stderr.strip()}")
                else:
                    embedded += len(pids)
            if n % 25 == 0:
                misses_file.write_text(json.dumps(sorted(misses)))
    except KeyboardInterrupt:
        print("\nStopped; run again to continue.")
    finally:
        misses_file.write_text(json.dumps(sorted(misses)))
    print(f"\nCovers found for {found} albums, {missed} without a clear match "
          f"({(time.time() - start) / 60:.0f} min)."
          + (f" Embedded in {embedded} tracks." if args.apply else " Run with --apply to embed them."))


if __name__ == "__main__":
    main()
