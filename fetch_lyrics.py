#!/usr/bin/env python3
"""
fetch_lyrics.py

Version : v1.0
Created : 2026-11-16

Download timed lyrics (.lrc) from LRCLIB (https://lrclib.net, free, no key)
for the Music.app library and save each next to its audio file as
"<song>.lrc". Players such as FiiO Music show a same-named .lrc found in
the song's folder. Audio files and Music.app tags are never changed.

A track is looked up by artist, title, album and length (LRCLIB only
accepts a match within 2 s); if that fails, a search by artist and title
picks the result whose length is closest (within 3 s). Only synced
(timed) lyrics are saved. Misses are remembered in lyrics_cache.json so a
re-run skips them (use --retry to look again).

Usage:
    python3 fetch_lyrics.py --sample 200      # try 200 random tracks, report the match rate
    python3 fetch_lyrics.py --run             # whole library (resumable, safe to stop)
    python3 fetch_lyrics.py --run --artist "Queen"
    python3 fetch_lyrics.py --run --retry     # look up earlier misses again

Sends artist, title, album and length of the tracks to lrclib.net.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from clean_music_library import JXA_COPIES, read_tracks

CACHE = Path(__file__).resolve().parent / "lyrics_cache.json"
API = "https://lrclib.net/api"
HEADERS = {"User-Agent": "apple-music-android-sync lyrics fetcher (personal use)"}
DELAY = 0.25  # seconds between requests


def call(path: str, params: dict) -> object | None:
    url = f"{API}/{path}?{urllib.parse.urlencode(params)}"
    for attempt in range(4):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=HEADERS), timeout=20) as r:
                return json.load(r)
        except urllib.error.HTTPError as e:
            if e.code == 404:
                return None
            if e.code == 429:
                time.sleep(10 * (attempt + 1))
                continue
            if e.code >= 500:
                time.sleep(3 * (attempt + 1))
                continue
            return None
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            time.sleep(3 * (attempt + 1))
    return None


def find_synced(artist: str, title: str, album: str, duration: float) -> str | None:
    hit = call("get", {"artist_name": artist, "track_name": title, "album_name": album,
                       "duration": round(duration)})
    if isinstance(hit, dict) and hit.get("syncedLyrics"):
        return hit["syncedLyrics"]
    results = call("search", {"artist_name": artist, "track_name": title})
    if isinstance(results, list):
        close = [x for x in results if x.get("syncedLyrics") and abs((x.get("duration") or 0) - duration) <= 3]
        if close:
            return min(close, key=lambda x: abs(x["duration"] - duration))["syncedLyrics"]
    return None


def lrc_path(location: str) -> str:
    return os.path.splitext(location)[0] + ".lrc"


def main() -> None:
    if sys.platform != "darwin":
        sys.exit("This script reads the library from Music.app, so it only works on macOS.")
    parser = argparse.ArgumentParser(description="Download timed lyrics (.lrc) from LRCLIB.")
    parser.add_argument("--sample", type=int, metavar="N", help="Try N random tracks and report the match rate.")
    parser.add_argument("--run", action="store_true", help="Process the whole library.")
    parser.add_argument("--artist", metavar="NAME", help="Only tracks whose artist contains NAME.")
    parser.add_argument("--retry", action="store_true", help="Look up earlier misses again.")
    args = parser.parse_args()
    if not args.sample and not args.run:
        parser.error("choose --sample N or --run")

    d = read_tracks(JXA_COPIES)
    cache: dict[str, bool] = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    todo = []
    for i, loc in enumerate(d["loc"]):
        if not loc or os.path.exists(lrc_path(loc)) or not d["duration"][i]:
            continue
        if args.artist and args.artist.casefold() not in (d["artist"][i] or "").casefold():
            continue
        if d["pid"][i] in cache and not args.retry:
            continue
        todo.append(i)
    if args.sample:
        random.seed(1)
        todo = random.sample(todo, min(args.sample, len(todo)))
        print(f"Sample of {len(todo)} tracks (nothing is cached; files ARE written)\n")
    else:
        print(f"{len(todo)} tracks to look up\n")

    found = missed = 0
    start = time.time()
    try:
        for n, i in enumerate(todo, 1):
            lrc = find_synced(d["artist"][i], d["name"][i], d["album"][i], d["duration"][i])
            if lrc:
                Path(lrc_path(d["loc"][i])).write_text(lrc, encoding="utf-8")
                found += 1
            else:
                missed += 1
            if not args.sample:
                cache[d["pid"][i]] = bool(lrc)
            if n % 100 == 0 or n == len(todo):
                if not args.sample:
                    CACHE.write_text(json.dumps(cache))
                rate = (time.time() - start) / n
                print(f"  {n}/{len(todo)}  found {found}  missed {missed}  "
                      f"(~{rate * (len(todo) - n) / 60:.0f} min left)", flush=True)
            time.sleep(DELAY)
    except KeyboardInterrupt:
        print("\nStopped; progress is saved, run again to continue.")
    finally:
        if not args.sample:
            CACHE.write_text(json.dumps(cache))
    total = found + missed
    if total:
        print(f"\nMatch rate: {found}/{total} ({100 * found / total:.0f}%)")


if __name__ == "__main__":
    main()
