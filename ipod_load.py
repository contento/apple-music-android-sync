#!/usr/bin/env python3
"""
ipod_load.py

Version : v1.2
Created : 2026-11-21

Copy the music folder onto an iPod Video (5th gen, Apple firmware) over USB
disk mode, then write its iTunesDB with the playlists from Apple/*.m3u8.
No iTunes / Finder sync needed (or wanted: a Finder sync would replace it).

  ./ipod.sh --dry-run          # counts and playlists, copies nothing
  ./ipod.sh --limit 10         # test with 10 songs
  ./ipod.sh                    # everything
  ./ipod.sh --src DIR --ipod /Volumes/NAME --name "My iPod"
  .\\ipod.ps1 --dry-run         # Windows (iPod as a drive letter, e.g. E:\\)

Defaults come from sync_apple_to_android_config.toml: --src is
paths.source_root, playlists are read from paths.apple_dir, and the iPod
is [ipod] mount (default /Volumes/iPod) with display name [ipod] name;
when that has no iPod, the only mounted iPod (/Volumes/* or a Windows
drive letter) is used. On Windows the iPod must be FAT32 ("Windows
format"); a Mac-formatted (HFS+) iPod cannot be read there.

Only .mp3 and .m4a (AAC / ALAC) are copied; FLAC, WAV, .m4p etc. are
skipped. Files get iPod-style names (iPod_Control/Music/Fnn/ABCD.mp3);
title, artist, album... are read with ffprobe and stored in the database.

Resumable: progress is kept in iPod_Control/iTunes/loader-manifest.json on
the iPod, so a re-run only copies songs that are not there yet. The
database (all songs + every playlist) is rebuilt from the manifest at the
end of every run, so re-exporting Apple/ and running again just updates
the playlists. Playlist entries are matched to songs by Mac path (NFC +
casefold); entries that are not on the iPod are counted and left out.

Requires ffprobe (brew install ffmpeg).
"""
import argparse
import json
import os
import random
import shutil
import string
import struct
import subprocess
import sys
import time
import unicodedata
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import player_config
import pretty as P

SCRIPT_DIR = Path(__file__).resolve().parent
MAC_EPOCH = 2082844800
KINDS = {  # ext -> (filetype marker, description, type1, type2, unk126, unk144)
    ".mp3": (b"MP3 ", "MPEG audio file", 0, 1, 0xFFFF, 0x000C),
    ".m4a": (b"M4A ", "AAC audio file", 0, 0, 0x0001, 0x0033),
}
UNSUPPORTED = (".m4p", ".flac", ".wav", ".aiff", ".aif", ".ogg", ".wma")
MHOD_TITLE, MHOD_PATH, MHOD_ALBUM, MHOD_ARTIST, MHOD_GENRE, MHOD_FILETYPE = 1, 2, 3, 4, 5, 6
MHOD_COMPOSER, MHOD_ALBUMARTIST = 12, 22


def mac(t):
    return int(t) + MAC_EPOCH


def key(path):
    """Compare paths regardless of accent encoding (NFD/NFC), case and / vs \\."""
    return unicodedata.normalize("NFC", os.path.normpath(str(path))).casefold()


def find_ipod(wanted):
    """`wanted` if it holds an iPod, else the only mounted iPod (/Volumes/* or a drive letter)."""
    has_ctl = lambda p: os.path.isdir(os.path.join(p, "iPod_Control", "Music"))
    if has_ctl(wanted):
        return wanted
    if os.name == "nt":
        roots = ["%s:\\" % c for c in string.ascii_uppercase]
    else:
        roots = [os.path.join("/Volumes", v) for v in sorted(os.listdir("/Volumes"))] if os.path.isdir("/Volumes") else []
    found = [r for r in roots if has_ctl(r)]
    if len(found) == 1:
        return found[0]
    if found:
        sys.exit("Several iPods mounted (%s); pick one with --ipod." % ", ".join(found))
    sys.exit("No iPod_Control/Music at %s and no other iPod found (is it mounted in disk mode?)" % wanted)


# ---------------------------------------------------------------- tags

def _num(s):
    try:
        return int(str(s).split("/")[0].strip() or 0)
    except ValueError:
        return 0


def _total(s):
    parts = str(s or "").split("/")
    return _num(parts[1]) if len(parts) > 1 else 0


def probe(path):
    r = subprocess.run(
        ["ffprobe", "-v", "error", "-print_format", "json", "-show_format", "-show_streams", path],
        capture_output=True, text=True)
    if r.returncode:
        return None
    j = json.loads(r.stdout)
    fmt = j.get("format", {})
    audio = next((s for s in j.get("streams", []) if s.get("codec_type") == "audio"), None)
    if not audio:
        return None
    tags = {k.lower(): v for k, v in (fmt.get("tags") or {}).items()}
    for k, v in (audio.get("tags") or {}).items():
        tags.setdefault(k.lower(), v)
    year = 0
    for k in ("date", "year", "tdrc", "tyer", "tdor"):
        try:
            year = int(str(tags.get(k, ""))[:4])
            break
        except ValueError:
            pass
    track = tags.get("track", "")
    disc = tags.get("disc", "")
    st = os.stat(path)
    return {
        "title": tags.get("title") or os.path.splitext(os.path.basename(path))[0],
        "artist": tags.get("artist", ""),
        "album": tags.get("album", ""),
        "album_artist": tags.get("album_artist") or tags.get("albumartist", ""),
        "genre": tags.get("genre", ""),
        "composer": tags.get("composer", ""),
        "track": _num(track), "tracks": _total(track) or _num(tags.get("tracktotal", "")),
        "disc": _num(disc), "discs": _total(disc) or _num(tags.get("disctotal", "")),
        "year": year if 0 < year < 3000 else 0,
        "compilation": 1 if str(tags.get("compilation", "0")).strip() in ("1", "true") else 0,
        "ms": int(float(fmt.get("duration") or audio.get("duration") or 0) * 1000),
        "kbps": int(int(fmt.get("bit_rate") or audio.get("bit_rate") or 0) / 1000),
        "rate": int(audio.get("sample_rate") or 44100),
        "codec": audio.get("codec_name", ""),
        "size": st.st_size,
        "mtime": int(st.st_mtime),
    }


# ---------------------------------------------------------------- playlists

def parse_m3u8(path):
    """Track paths in a Music.app export (CR line endings, absolute paths)."""
    tracks = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("file://"):
            line = urllib.parse.unquote(urllib.parse.urlparse(line).path)
        tracks.append(line)
    return tracks


def read_playlists(apple_dir, ids_by_key):
    """[(name, [track id...], missing paths)] for every Apple/*.m3u8."""
    out = []
    for f in sorted(apple_dir.glob("*.m3u8"), key=lambda p: key(p.stem)):
        ids, missing = [], []
        for src in parse_m3u8(f):
            tid = ids_by_key.get(key(src))
            if tid is None:
                missing.append(src)
            else:
                ids.append(tid)
        out.append((unicodedata.normalize("NFC", f.stem), ids, missing))
    return out


# ---------------------------------------------------------------- iTunesDB records

def mhod_str(kind, text):
    data = text.encode("utf-16-le")
    return (b"mhod" + struct.pack("<IIIII", 0x18, 0x28 + len(data), kind, 0, 0)
            + struct.pack("<IIII", 1, len(data), 0, 0) + data)


def mhod100(pos):
    return b"mhod" + struct.pack("<IIIII", 0x18, 0x2C, 100, 0, 0) + struct.pack("<I", pos) + bytes(16)


def mhod52(sort_type, idx):
    return (b"mhod" + struct.pack("<IIIII", 0x18, 72 + 4 * len(idx), 52, 0, 0)
            + struct.pack("<II", sort_type, len(idx)) + bytes(40)
            + struct.pack("<%dI" % len(idx), *idx))


def mhit(t):
    m = t["meta"]
    marker, desc, type1, type2, unk126, unk144 = KINDS[os.path.splitext(t["ipod"])[1].lower()]
    mhods = [mhod_str(MHOD_TITLE, m["title"]),
             mhod_str(MHOD_PATH, ":" + t["ipod"].replace("/", ":")),
             mhod_str(MHOD_FILETYPE, desc)]
    for kind, k in ((MHOD_ALBUM, "album"), (MHOD_ARTIST, "artist"), (MHOD_GENRE, "genre"),
                    (MHOD_COMPOSER, "composer"), (MHOD_ALBUMARTIST, "album_artist")):
        if m.get(k):
            mhods.append(mhod_str(kind, m[k]))
    body = b"".join(mhods)
    h = bytearray(0x184)
    h[0:4] = b"mhit"
    pk = lambda fmt, off, *v: struct.pack_into("<" + fmt, h, off, *v)
    pk("III", 0x04, 0x184, 0x184 + len(body), len(mhods))
    pk("II", 0x10, t["id"], 1)                      # id, visible
    h[0x18:0x1C] = marker[::-1]                     # filetype marker, stored little-endian
    h[0x1C], h[0x1D], h[0x1E] = type1, type2, m["compilation"]
    pk("IIIIII", 0x20, mac(m["mtime"]), m["size"], m["ms"], m["track"], m["tracks"], m["year"])
    pk("II", 0x38, m["kbps"], (m["rate"] & 0xFFFF) << 16)
    pk("II", 0x5C, m["disc"], m["discs"])
    pk("I", 0x68, mac(t["added"]))
    pk("Q", 0x70, t["dbid"])
    pk("H", 0x7E, unk126)
    pk("f", 0x88, float(m["rate"]))
    pk("H", 0x90, unk144)
    h[0xA4] = 2                                     # no artwork
    pk("Q", 0xA8, t["dbid"])
    pk("I", 0xD0, 1)                                # media type: audio
    return bytes(h) + body


def mhip(track_id, pos, group_id, now):
    sub = mhod100(pos)
    h = bytearray(0x4C)
    h[0:4] = b"mhip"
    struct.pack_into("<IIIIIII", h, 4, 0x4C, 0x4C + len(sub), 1, 0, group_id, track_id, now)
    return bytes(h) + sub


class Ids:
    """Unique ids for playlist items across the whole database."""
    def __init__(self, start):
        self.n = start

    def next(self):
        self.n += 1
        return self.n


def mhyp(title, track_ids, master, extra_mhods, now, item_ids):
    mhods = [mhod_str(MHOD_TITLE, title)] + extra_mhods
    items = [mhip(tid, i, item_ids.next(), now) for i, tid in enumerate(track_ids)]
    body = b"".join(mhods) + b"".join(items)
    h = bytearray(0x6C)
    h[0:4] = b"mhyp"
    struct.pack_into("<IIII", h, 4, 0x6C, 0x6C + len(body), len(mhods), len(items))
    h[0x14] = 1 if master else 0
    struct.pack_into("<IQ", h, 0x18, now, random.getrandbits(64))
    struct.pack_into("<HHI", h, 0x28, 1, 0, 1)      # 1 string mhod, not podcast, manual order
    return bytes(h) + body


def mhsd(kind, body):
    return b"mhsd" + struct.pack("<III", 0x60, 0x60 + len(body), kind) + bytes(0x60 - 16) + body


def sort_key(s):
    s = unicodedata.normalize("NFKD", s or "").casefold().strip()
    return s[4:] if s.startswith("the ") else s


def build_db(tracks, name, playlists):
    now = mac(time.time())
    tracks = sorted(tracks, key=lambda t: (sort_key(t["meta"]["artist"]), sort_key(t["meta"]["album"]),
                                           t["meta"]["disc"], t["meta"]["track"], sort_key(t["meta"]["title"])))
    n = range(len(tracks))
    M = lambda i: tracks[i]["meta"]
    order = {
        3: sorted(n, key=lambda i: sort_key(M(i)["title"])),
        4: sorted(n, key=lambda i: (sort_key(M(i)["album"]), M(i)["disc"], M(i)["track"], sort_key(M(i)["title"]))),
        5: sorted(n, key=lambda i: (sort_key(M(i)["artist"]), sort_key(M(i)["album"]), M(i)["disc"], M(i)["track"])),
        7: sorted(n, key=lambda i: (sort_key(M(i)["genre"]), sort_key(M(i)["artist"]), sort_key(M(i)["album"]), M(i)["track"])),
        0x12: sorted(n, key=lambda i: (sort_key(M(i)["composer"]), sort_key(M(i)["title"]))),
    }
    mhlt = b"mhlt" + struct.pack("<II", 0x5C, len(tracks)) + bytes(0x5C - 12)
    mhlt += b"".join(mhit(t) for t in tracks)
    item_ids = Ids(max([t["id"] for t in tracks] + [0]) + 1000)

    def mhlp():
        # The iPod reads the playlist list from mhsd 3 (newer firmware) or 2; write both.
        lists = [mhyp(name, [t["id"] for t in tracks], True,
                      [mhod52(k, v) for k, v in order.items()], now, item_ids)]
        lists += [mhyp(pl_name, ids, False, [], now, item_ids) for pl_name, ids, _ in playlists]
        return b"mhlp" + struct.pack("<II", 0x5C, len(lists)) + bytes(0x5C - 12) + b"".join(lists)

    body = mhsd(1, mhlt) + mhsd(3, mhlp()) + mhsd(2, mhlp())
    h = bytearray(0xBC)
    h[0:4] = b"mhbd"
    struct.pack_into("<IIIII", h, 4, 0xBC, 0xBC + len(body), 1, 0x13, 3)
    struct.pack_into("<QH", h, 0x18, random.getrandbits(64), 2)
    return bytes(h) + body


# ---------------------------------------------------------------- main

def main():
    cfg = player_config.load_config()
    paths = cfg.get("paths", {})
    ipod_cfg = cfg.get("ipod", {})
    player_root = Path(paths.get("player_root", str(SCRIPT_DIR))).expanduser()

    ap = argparse.ArgumentParser(description="Copy music and Apple/ playlists onto an iPod Video.")
    ap.add_argument("--src", default=paths.get("source_root"),
                    help="music folder to copy (default: paths.source_root)")
    ap.add_argument("--apple-dir", default=paths.get("apple_dir", str(player_root / "Apple")),
                    help="folder with the exported .m3u8 playlists (default: paths.apple_dir)")
    ap.add_argument("--ipod", default=ipod_cfg.get("mount", "/Volumes/iPod"),
                    help="iPod mount point, e.g. /Volumes/iPod or E:\\ (default: [ipod] mount, "
                         "else the only iPod found)")
    ap.add_argument("--name", default=ipod_cfg.get("name", "iPod"), help="iPod name in the menus")
    ap.add_argument("--limit", type=int, default=0, help="copy at most N new songs (for testing)")
    ap.add_argument("--dry-run", action="store_true", help="show what would happen, change nothing")
    a = ap.parse_args()
    if not a.src:
        sys.exit("No music folder: pass --src or set paths.source_root in the config.")

    src = os.path.expanduser(a.src)
    if not os.path.isdir(src):
        sys.exit("Music folder not found: %s (pass --src or set paths.source_root)" % src)
    ipod = find_ipod(os.path.expanduser(a.ipod))
    ctl = os.path.join(ipod, "iPod_Control")
    man_path = os.path.join(ctl, "iTunes", "loader-manifest.json")
    manifest = json.load(open(man_path)) if os.path.exists(man_path) else {"tracks": []}
    done = {t["src"] for t in manifest["tracks"]
            if os.path.exists(os.path.join(ipod, t["ipod"]))}
    manifest["tracks"] = [t for t in manifest["tracks"] if t["src"] in done]
    # Songs are keyed by computer path: loading from another folder (e.g. the Mac's
    # iPod on a PC) would copy everything again and list each song twice.
    root_key = os.path.join(key(src), "")
    if done and not any(key(s).startswith(root_key) for s in done):
        sys.exit("This iPod was loaded from another music folder (e.g. %s), not %s.\n"
                 "Run it from that computer/folder, or delete %s to start over."
                 % (os.path.dirname(next(iter(done))), src, man_path))

    todo, skipped = [], []
    for root, _, files in os.walk(src):
        for f in sorted(files):
            p = os.path.join(root, f)
            ext = os.path.splitext(f)[1].lower()
            if f.startswith("."):
                continue
            if ext in KINDS:
                if p not in done:
                    todo.append(p)
            elif ext in UNSUPPORTED:
                skipped.append(p)
    todo.sort()
    if a.limit:
        todo = todo[:a.limit]

    P.title("🎵", "iPod", ipod)
    P.table(None, [["Music folder", src],
                   ["Playlists", os.path.expanduser(a.apple_dir)],
                   ["Already on iPod", P.num(len(done))],
                   ["To copy", P.num(len(todo))],
                   ["Unsupported, skipped", P.num(len(skipped))]])

    free = shutil.disk_usage(ipod).free
    need = sum(os.path.getsize(p) for p in todo)
    if need > free - 200 * 2**20:
        sys.exit("Not enough space: need %.1f GB, free %.1f GB" % (need / 2**30, free / 2**30))

    failed = []
    if a.dry_run:
        # Pretend every file still to copy makes it, to preview the playlists.
        fake = [{"src": p, "id": -1 - i} for i, p in enumerate(todo)]
        ids_by_key = {key(t["src"]): t["id"] for t in manifest["tracks"] + fake}
    else:
        failed = copy_tracks(ipod, todo, need, manifest, man_path)
        ids_by_key = {key(t["src"]): t["id"] for t in manifest["tracks"]}

    playlists = read_playlists(Path(os.path.expanduser(a.apple_dir)), ids_by_key)
    P.title("📋", "Playlists", "Apple/*.m3u8 → iTunesDB")
    if playlists:
        P.table(["Playlist", "Songs", "Not on iPod"],
                [[n, P.num(len(ids)), P.num(len(miss)) if miss else "—"] for n, ids, miss in playlists],
                "<>>")
        for n, _, miss in playlists:
            if miss:
                P.items("❓", ["%s: %s" % (n, os.path.relpath(m, src)) for m in miss], limit=5)
    else:
        print("  (no .m3u8 files found)")

    if a.dry_run:
        print("\n🧪 Dry run: nothing copied, iTunesDB not written.")
        return

    db = build_db(manifest["tracks"], a.name, playlists)
    db_path = os.path.join(ctl, "iTunes", "iTunesDB")
    with open(db_path + ".tmp", "wb") as fh:
        fh.write(db)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(db_path + ".tmp", db_path)
    print("\n✅ iTunesDB written: %s songs, %d playlists (%s)"
          % (P.num(len(manifest["tracks"])), len(playlists), P.size(len(db))))
    if failed:
        print("\n⚠️  Could not add %d files (not MP3/AAC/ALAC inside, or copy failed):" % len(failed))
        P.items("•", [os.path.relpath(p, src) for p in failed], limit=50)
    print("\n⏏️  Eject the iPod before unplugging it.")


def copy_tracks(ipod, todo, need, manifest, man_path):
    """Copy `todo` to the iPod, adding each to the manifest. Returns the failures."""
    next_id = max([t["id"] for t in manifest["tracks"]] + [99]) + 1
    used = {t["ipod"] for t in manifest["tracks"]}
    failed = []

    def save():
        tmp = man_path + ".tmp"
        with open(tmp, "w") as fh:
            json.dump(manifest, fh)
        os.replace(tmp, man_path)

    if todo:
        P.title("📤", "Copying", "%s songs, %s" % (P.num(len(todo)), P.size(need)))
    with ThreadPoolExecutor(8) as pool:
        metas = pool.map(probe, todo)
        t0, copied_bytes = time.time(), 0
        for i, (p, meta) in enumerate(zip(todo, metas), 1):
            if meta is None or meta["codec"] not in ("mp3", "aac", "alac"):
                failed.append(p)
                continue
            ext = os.path.splitext(p)[1].lower()
            while True:
                rel = "iPod_Control/Music/F%02d/%s%s" % (
                    random.randrange(50), "".join(random.choices(string.ascii_uppercase, k=4)), ext)
                if rel not in used and not os.path.exists(os.path.join(ipod, rel)):
                    break
            try:
                os.makedirs(os.path.dirname(os.path.join(ipod, rel)), exist_ok=True)
                shutil.copyfile(p, os.path.join(ipod, rel))
            except OSError as e:
                print("  copy failed: %s (%s)" % (p, e), flush=True)
                failed.append(p)
                continue
            used.add(rel)
            copied_bytes += meta["size"]
            manifest["tracks"].append({"src": p, "ipod": rel, "id": next_id,
                                       "dbid": random.getrandbits(64), "added": int(time.time()),
                                       "meta": meta})
            next_id += 1
            if i % 100 == 0 or i == len(todo):
                save()
                el = time.time() - t0
                rate = copied_bytes / el / 2**20 if el else 0
                eta = (need - copied_bytes) / (copied_bytes / el) / 60 if copied_bytes else 0
                print("  %d/%d copied  %.1f MB/s  ~%.0f min left" % (i, len(todo), rate, eta), flush=True)
    save()
    return failed


if __name__ == "__main__":
    main()
