#!/usr/bin/env python3
"""
export_playlists.py

Version : v1.0
Created : 2026-11-05

Export playlists from Music.app (macOS) or iTunes (Windows, over COM) to
.m3u8 files in Apple/, in the same format as File › Library ›
Export Playlist… (UTF-8, CR line endings). Smart playlists are ignored.

Like Music's own export, only tracks with a local file are written;
cloud-only entries are skipped and counted.

Usage:
    python3 export_playlists.py               # re-export playlists already in Apple/
    python3 export_playlists.py Balada Tango  # export these playlists
    python3 export_playlists.py --all         # every playlist you made
    python3 export_playlists.py --list        # list playlists, export nothing
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import tempfile
import unicodedata
from pathlib import Path

import player_config

SCRIPT_DIR = Path(__file__).resolve().parent

# JXA: playlist summary, plus the tracks of the playlists named in argv.
JXA = """
function run(argv) {
  const m = Application('Music');
  const pls = m.userPlaylists;
  const names = pls.name(), smart = pls.smart(), kind = pls.specialKind();
  const out = {playlists: names.map((n, i) => ({name: n, smart: smart[i], special: kind[i] !== 'none',
                                                 count: pls[i].tracks.length})),
               tracks: {}};
  if (!argv.length) return JSON.stringify(out);

  const ft = m.libraryPlaylists[0].fileTracks;
  const loc = {}, pid = ft.persistentID(), l = ft.location();
  pid.forEach((p, i) => { if (l[i]) loc[p] = l[i].toString(); });

  for (const name of argv) {
    const i = names.indexOf(name);
    if (i < 0) continue;
    const t = pls[i].tracks;
    const ids = t.persistentID(), nm = t.name(), ar = t.artist(), du = t.duration();
    out.tracks[name] = ids.map((p, j) => ({name: nm[j], artist: ar[j], duration: du[j], path: loc[p] || ''}));
  }
  return JSON.stringify(out);
}
"""

# PowerShell: the same from iTunes for Windows over COM. Playlist names to
# read come in $env:EXPORT_PLAYLISTS as a JSON array.
ITUNES_PS = """
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$itunes = New-Object -ComObject iTunes.Application
$wanted = @(ConvertFrom-Json $env:EXPORT_PLAYLISTS)
$playlists = [Collections.Generic.List[object]]::new()
$tracks = [ordered]@{}
foreach ($p in $itunes.LibrarySource.Playlists) {
    if ($p.Kind -ne 2) { continue }   # 2 = user playlist
    $playlists.Add([ordered]@{ name = $p.Name; smart = [bool]$p.Smart;
                               special = ($p.SpecialKind -ne 0); count = $p.Tracks.Count })
    if ($wanted -notcontains $p.Name) { continue }
    $list = [Collections.Generic.List[object]]::new()
    foreach ($t in $p.Tracks) {
        $loc = ''
        try { if ($t.Location) { $loc = [string]$t.Location } } catch { }
        $list.Add([ordered]@{ name = [string]$t.Name; artist = [string]$t.Artist;
                              duration = $t.Duration; path = $loc })
    }
    $tracks[$p.Name] = $list.ToArray()
}
ConvertTo-Json -Compress -Depth 5 -InputObject ([ordered]@{ playlists = $playlists.ToArray(); tracks = $tracks })
"""


def music(names: list[str]) -> dict:
    if sys.platform == "win32":
        app = "iTunes"
        cmd = ["powershell", "-NoProfile", "-NonInteractive", "-Command", ITUNES_PS]
        env = {**os.environ, "EXPORT_PLAYLISTS": json.dumps(names)}
    else:
        app = "Music.app"
        cmd = ["osascript", "-l", "JavaScript", "-e", JXA, *names]
        env = None
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8", env=env)
    if res.returncode != 0:
        sys.exit(f"Could not read {app}: {res.stderr.strip()}")
    return json.loads(res.stdout)


def apple_dir() -> Path:
    apple = player_config.load_config().get("paths", {}).get("apple_dir")
    return Path(apple).expanduser() if apple else SCRIPT_DIR / "Apple"


def file_name(playlist: str) -> str:
    return playlist.replace("/", "_").replace(":", "_") + ".m3u8"


def key(s: str) -> str:
    return unicodedata.normalize("NFC", s)


def write_m3u8(path: Path, tracks: list[dict]) -> int:
    lines = ["#EXTM3U"]
    for t in tracks:
        if t["path"]:
            lines += [f"#EXTINF:{int(t['duration'] or 0)},{t['name']} - {t['artist']}", t["path"]]
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    with os.fdopen(fd, "w", encoding="utf-8", newline="") as f:
        f.write("\r".join(lines) + "\r")
    os.chmod(tmp, 0o644)
    os.replace(tmp, path)
    return (len(lines) - 1) // 2


def main() -> None:
    parser = argparse.ArgumentParser(description="Export Music.app / iTunes playlists to .m3u8 files.")
    parser.add_argument("names", nargs="*", help="Playlists to export (default: those already in Apple/).")
    parser.add_argument("--all", action="store_true", help="Export every playlist you made.")
    parser.add_argument("--list", action="store_true", help="List playlists and exit.")
    args = parser.parse_args()
    if args.all and args.names:
        parser.error("use either --all or playlist names, not both")

    out_dir = apple_dir()
    info = music([])
    playlists = [p for p in info["playlists"] if not p["special"] and not p["smart"]]
    by_key = {key(p["name"]): p["name"] for p in playlists}

    if args.list:
        for p in playlists:
            print(f"{p['count']:>7}  {p['name']}")
        return

    if args.all:
        wanted = [p["name"] for p in playlists]
    elif args.names:
        wanted = []
        for n in args.names:
            if key(n) in by_key:
                wanted.append(by_key[key(n)])
            else:
                print(f"⚠️  No playlist named {n!r}")
    else:
        existing = sorted(out_dir.glob("*.m3u8"))
        names_by_file = {key(file_name(p["name"])): p["name"] for p in playlists}
        wanted = []
        for f in existing:
            if key(f.name) in names_by_file:
                wanted.append(names_by_file[key(f.name)])
            else:
                print(f"⚠️  {f.name}: no playlist with that name, left as is")
        if not existing:
            sys.exit(f"No .m3u8 files in {out_dir}. Name playlists or use --all.")

    if not wanted:
        sys.exit("Nothing to export.")

    out_dir.mkdir(parents=True, exist_ok=True)
    data = music(wanted)["tracks"]
    for name in wanted:
        tracks = data.get(name, [])
        written = write_m3u8(out_dir / file_name(name), tracks)
        skipped = len(tracks) - written
        note = f"  ({skipped} cloud-only skipped)" if skipped else ""
        print(f"✅ {file_name(name)}: {written} tracks{note}")


if __name__ == "__main__":
    main()
