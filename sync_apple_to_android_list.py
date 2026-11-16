#!/usr/bin/env python3
"""
sync_apple_to_android_list.py

Version : v3.4
Created : 2026-05-03

Full pipeline: Mac → Android over USB (adb) for Poweramp / Sony Music Player

  --player
    1. Push the whole Music library: every track Music.app (or iTunes)
       lists under source_root, except DRM .m4p. Files in the folder that
       the library does not list are skipped.
         → <android_root>/<Artist>/<Album>/<Track>
       A timed-lyrics file beside a track (<Track>.lrc, from fetch_lyrics.py)
       is pushed with it.

    2. Read playlists in
         → ./Apple/*.m3u8

    3. Write an Android-ready playlist with absolute paths
         → ./Android/_playlists/<device>/<name>.m3u8   (local copy)
         → <android_root>/_playlists/<device>/<name>.m3u8
         → <playlist_copy_dir>/<name>.playlist.m3u8   (optional: FiiO
           Music's "Import m3u playlist" reads only *.playlist.m3u8 files
           in /storage/emulated/0/FiiOMusic/playlist)
       Paths use the device's own spelling (case and accents), because
       players such as FiiO Music match playlist entries against their
       library by exact string.

  --flac
    Mirror the FLAC folder (Artist/Album/Track) next to android_root
         → <flac_root>/<Artist>/<Album>/<Track>
           (default flac_root: <parent of android_root>/_flac,
            e.g. /storage/emulated/0/music/_flac)
    --delete (with --player, --flac or --all) also removes device files
    that are no longer in the library / FLAC folder: renamed/removed tracks with their .lrc/.jpg,
    and this device's playlists no longer in Apple/.

  --all (default): both of the above.

Files are compared by size against what is already on the device, so
re-runs only push what changed.

Output: a header table (device, folders, mode), one section each for the
library, playlists (table), FLAC and deletions, then a summary table.
Dry runs list the first 10 files per section (--verbose: all) and every
file --delete would remove.

Usage:
    python3 sync_apple_to_android_list.py                   # --all, default device
    python3 sync_apple_to_android_list.py --player          # playlists + their tracks
    python3 sync_apple_to_android_list.py --flac            # FLAC folder only
    python3 sync_apple_to_android_list.py --player --delete # also remove stale tracks
    python3 sync_apple_to_android_list.py --flac --delete   # also remove stale FLAC files
    python3 sync_apple_to_android_list.py --device a105     # use named device root
    python3 sync_apple_to_android_list.py --dry-run         # preview only
    python3 sync_apple_to_android_list.py --dry-run --verbose  # list every file

Requires: adb on PATH, USB debugging enabled, exactly one device attached
(or ANDROID_SERIAL set to pick one).
"""

from __future__ import annotations

import argparse
import os
import shlex
import shutil
import subprocess
import sys
import unicodedata
import urllib.parse
from pathlib import Path

import export_playlists
import player_config
import pretty as P
from pretty import num, size as human

# ─── CONFIG ────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent

PLAYER_ROOT = SCRIPT_DIR
APPLE_DIR = SCRIPT_DIR / "Apple"
ANDROID_DIR = SCRIPT_DIR / "Android"
SOURCE_ROOT = SCRIPT_DIR
ANDROID_ROOT = "/storage/emulated/0/music/_player"
FLAC_ROOT = "/storage/emulated/0/music/_flac"
DEVICE_ROOTS: dict[str, str] = {}
DEVICE_FLAC_ROOTS: dict[str, str] = {}
DEVICE_PLAYLIST_COPY_DIRS: dict[str, str] = {}
PLAYLIST_COPY_DIR = ""
DEFAULT_DEVICE = "internal"
PLAYLIST_NAMES: list[str] = []
FLAC_SOURCE = Path("~/Library/Mobile Documents/com~apple~CloudDocs/Media/_flac").expanduser()
FLAC_SUBDIR = "_flac"


def extract_enabled_playlists(items: list[object]) -> list[str]:
    """Return enabled playlist names from config entries.

    Supported formats:
      - "Bolero"
      - {"name": "Bolero", "enabled": true}
    """
    names: list[str] = []
    for item in items:
        if isinstance(item, str):
            name = item.strip()
            if name:
                names.append(name)
            continue

        if isinstance(item, dict):
            name = str(item.get("name", "")).strip()
            enabled = bool(item.get("enabled", True))
            if name and enabled:
                names.append(name)

    return names

# Apple Music subscription DRM tracks — cannot be played on Android.
DRM_EXTENSIONS = {".m4p"}
AUDIO_EXTENSIONS = {".m4a", ".mp3", ".flac", ".wav", ".aac", ".aif", ".aiff", ".alac", ".ogg", ".opus"}
# Leave some headroom on the device after an adb push.
FREE_SPACE_MARGIN = 500 * 1024 * 1024
# Device files removed per adb call with --delete.
DELETE_BATCH = 50
# Files/issues listed per section without --verbose.
LIST_LIMIT = 10
# ───────────────────────────────────────────────────────────────────────────


def load_config() -> None:
    """Load all paths and playlist names from the config file."""
    global PLAYER_ROOT, APPLE_DIR, ANDROID_DIR
    global SOURCE_ROOT, ANDROID_ROOT, PLAYLIST_NAMES
    global DEVICE_ROOTS, DEVICE_FLAC_ROOTS, DEVICE_PLAYLIST_COPY_DIRS, DEFAULT_DEVICE, FLAC_SOURCE, FLAC_SUBDIR

    raw = player_config.load_config(required=True)

    paths = raw.get("paths", {})
    PLAYER_ROOT = Path(paths.get("player_root", str(SCRIPT_DIR))).expanduser()
    APPLE_DIR = Path(paths.get("apple_dir", str(PLAYER_ROOT / "Apple"))).expanduser()
    ANDROID_DIR = Path(paths.get("android_dir", str(PLAYER_ROOT / "Android"))).expanduser()
    SOURCE_ROOT = Path(paths.get("source_root", "")).expanduser()
    ANDROID_ROOT = raw.get("android_root", ANDROID_ROOT)
    DEVICE_ROOTS = {}
    DEVICE_FLAC_ROOTS = {}
    DEVICE_PLAYLIST_COPY_DIRS = {}
    for name, entry in raw.get("devices", {}).items():
        if not isinstance(name, str):
            continue
        if isinstance(entry, str):
            root = entry.strip()
        elif isinstance(entry, dict):
            root = str(entry.get("android_root", "")).strip()
            flac_root = str(entry.get("flac_root", "")).strip()
            if flac_root:
                DEVICE_FLAC_ROOTS[name.strip()] = flac_root.rstrip("/")
            copy_dir = str(entry.get("playlist_copy_dir", "")).strip()
            if copy_dir:
                DEVICE_PLAYLIST_COPY_DIRS[name.strip()] = copy_dir.rstrip("/")
        else:
            root = ""
        if root:
            DEVICE_ROOTS[name.strip()] = root.rstrip("/")
    if "internal" not in DEVICE_ROOTS and ANDROID_ROOT:
        DEVICE_ROOTS["internal"] = str(ANDROID_ROOT).rstrip("/")
    DEFAULT_DEVICE = str(raw.get("default_device", "internal")).strip() or "internal"
    PLAYLIST_NAMES = extract_enabled_playlists(raw.get("playlists", []))
    flac = raw.get("flac", {})
    FLAC_SOURCE = Path(flac.get("source", str(FLAC_SOURCE))).expanduser()
    FLAC_SUBDIR = str(flac.get("target_subdir", FLAC_SUBDIR)).strip("/")


def key(rel: str) -> str:
    # macOS may hand us NFD names; compare in NFC so accents match the device.
    # Android shared storage is case-insensitive, so fold case as well.
    return unicodedata.normalize("NFC", rel).casefold()




# ─── ADB ───────────────────────────────────────────────────────────────────

def adb(*args: str, check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(["adb", *args], capture_output=True, text=True, check=check)


def adb_shell(cmd: str, check: bool = True) -> subprocess.CompletedProcess:
    return adb("shell", cmd, check=check)


def device_attached(required: bool) -> bool:
    """Check for exactly one authorized device; exit if required and missing."""
    def fail(msg: str) -> bool:
        if required:
            sys.exit(msg)
        print(f"  🔌 {P.yellow(msg)}\n  🔍 dry run continues as if the device were empty.")
        return False

    if not shutil.which("adb"):
        return fail("adb not found. Install with: brew install android-platform-tools")
    lines = adb("devices").stdout.splitlines()[1:]
    devices = [l.split("\t") for l in lines if "\t" in l]
    if not devices:
        return fail("No Android device attached. Plug in USB and enable USB debugging.")
    if any(d[1] == "unauthorized" for d in devices):
        return fail("Device not authorized — accept the 'Allow USB debugging' prompt on the phone.")
    if len(devices) > 1 and not os.environ.get("ANDROID_SERIAL"):
        serials = ", ".join(d[0] for d in devices)
        return fail(f"Multiple devices attached ({serials}). Set ANDROID_SERIAL to choose one.")
    return True


def resolve_device_case(path: str) -> str:
    """Return `path` with each existing component spelled as on the device.

    Shared storage is case-insensitive, so "/storage/emulated/0/music" opens
    the "Music" folder, but players that match playlists against their
    library need the exact spelling the media scanner recorded.
    """
    cur = ""
    parts = [p for p in path.split("/") if p]
    for i, part in enumerate(parts):
        res = adb_shell(f"ls -a {shlex.quote(cur or '/')}", check=False)
        names = {key(n): n for n in res.stdout.replace("\r", "").splitlines() if n}
        if res.returncode != 0 or not names:
            cur += "/" + part  # unreadable parent (e.g. /storage/emulated): keep as given
            continue
        match = names.get(key(part))
        if match is None:
            return "/".join([cur, *parts[i:]])  # rest does not exist yet
        cur += "/" + match
    return cur or "/"


def device_dir_map(device_files: dict[str, tuple[str, int]]) -> dict[str, str]:
    """Return {key(dir): dir} for every folder that holds a device file."""
    dirs: dict[str, str] = {}
    for rel, _ in device_files.values():
        parts = rel.split("/")[:-1]
        for i in range(1, len(parts) + 1):
            d = "/".join(parts[:i])
            dirs.setdefault(key(d), d)
    return dirs


def device_spelling(
    rel: str,
    device_files: dict[str, tuple[str, int]],
    device_dirs: dict[str, str],
) -> str:
    """Spell `rel` as the device does; new parts are written in NFC."""
    hit = device_files.get(key(rel))
    if hit:
        return hit[0]
    parts = unicodedata.normalize("NFC", rel).split("/")
    for i in range(len(parts) - 1, 0, -1):
        d = device_dirs.get(key("/".join(parts[:i])))
        if d:
            return "/".join([d, *parts[i:]])
    return "/".join(parts)


def list_device_files(root: str) -> dict[str, tuple[str, int]]:
    """Return {key: (rel, size)} for every file under root on the device."""
    q = shlex.quote(root)
    res = adb_shell(f"[ -d {q} ] && find {q} -type f -exec stat -c '%s|%n' {{}} +", check=False)
    files: dict[str, tuple[str, int]] = {}
    prefix = root.rstrip("/") + "/"
    for line in res.stdout.splitlines():
        size, _, path = line.partition("|")
        if not path.startswith(prefix) or not size.isdigit():
            continue
        rel = path[len(prefix):]
        files[key(rel)] = (rel, int(size))
    return files


def device_free_bytes(path: str) -> int:
    # Walk up until df accepts the path (target may not exist yet).
    probe = path
    while probe and probe != "/":
        res = adb_shell(f"df -k {shlex.quote(probe)}", check=False)
        rows = res.stdout.strip().splitlines()
        if res.returncode == 0 and len(rows) >= 2:
            return int(rows[-1].split()[3]) * 1024
        probe = probe.rsplit("/", 1)[0]
    return -1


def push(src: Path, dst: str) -> bool:
    res = adb("push", str(src), dst, check=False)
    if res.returncode != 0:
        print(f"     ❌ {P.red('push failed:')} {res.stderr.strip() or res.stdout.strip()}")
    return res.returncode == 0


def volume_name(path: str) -> str:
    """MediaStore volume holding `path`: external_primary or the SD card's name
    (e.g. /storage/external_sd/... → external_sd, /storage/6135-3539/... → 6135-3539)."""
    parts = path.split("/")
    if path.startswith("/storage/emulated/") or len(parts) < 3:
        return "external_primary"
    return parts[2].lower()


def media_scan(paths: list[str]) -> None:
    for vol in dict.fromkeys(volume_name(p) for p in paths if p):
        res = adb_shell(
            f"content call --uri content://media --method scan_volume --arg {shlex.quote(vol)}",
            check=False,
        )
        if res.returncode != 0:
            adb_shell(
                "am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE "
                f"-d {shlex.quote('file://' + ANDROID_ROOT.rsplit('/', 1)[0])}",
                check=False,
            )


# ─── --player: parse playlists, plan track pushes, write Android playlist ──

def parse_m3u8(path: Path) -> list[Path]:
    tracks: list[Path] = []
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        if line.startswith("file://"):
            line = urllib.parse.unquote(urllib.parse.urlparse(line).path)
        tracks.append(Path(line))
    return tracks


def is_icloud_placeholder(p: Path) -> bool:
    return (p.parent / f".{p.name}.icloud").exists()


def plan_playlist(
    apple_path: Path,
    device_files: dict[str, tuple[str, int]],
    device_dirs: dict[str, str],
    pushes: dict[str, tuple[str, Path]],
    device_name: str,
    dry_run: bool,
) -> dict:
    """Queue missing tracks in `pushes` and write the Android playlist locally."""
    name = apple_path.stem

    playlist_rel = f"_playlists/{device_name}/{name}.m3u8"
    android_path = ANDROID_DIR / playlist_rel

    tracks = parse_m3u8(apple_path)

    new_lines: list[str] = ["#EXTM3U", f"#PLAYLIST:{name}"]
    counts: dict = {
        "queued": 0, "already": 0, "drm": 0,
        "evicted": 0, "missing": 0, "outside": 0,
        "total": len(tracks), "issues": [],
    }

    for src in tracks:
        try:
            rel = src.relative_to(SOURCE_ROOT).as_posix()
        except ValueError:
            counts["issues"].append(f"🚫 outside source_root: {src}")
            counts["outside"] += 1
            continue

        if src.suffix.lower() in DRM_EXTENSIONS:
            counts["drm"] += 1
            continue

        if is_icloud_placeholder(src):
            counts["issues"].append(f"⏬ not downloaded: {rel}")
            counts["evicted"] += 1
            continue

        if not src.exists():
            counts["issues"].append(f"❓ missing on the Mac: {rel}")
            counts["missing"] += 1
            continue

        dev_rel = device_spelling(rel, device_files, device_dirs)
        if device_files.get(key(rel), ("", -1))[1] == src.stat().st_size:
            counts["already"] += 1
        else:
            dst = f"{ANDROID_ROOT}/{dev_rel}"
            pushes[key(dst)] = (dst, src)
            counts["queued"] += 1

        new_lines.append(f"{ANDROID_ROOT}/{dev_rel}")

    # FiiO Music only imports "<name>.playlist.m3u8" and names the playlist <name>.
    copy_dst = f"{PLAYLIST_COPY_DIR}/{name}.playlist.m3u8" if PLAYLIST_COPY_DIR else ""
    if not dry_run:
        android_path.parent.mkdir(parents=True, exist_ok=True)
        android_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        dst = f"{ANDROID_ROOT}/{playlist_rel}"
        pushes[key(dst)] = (dst, android_path)
        if copy_dst:
            pushes[key(copy_dst)] = (copy_dst, android_path)
    return {"name": name, "kind": "playlist", **counts}


def read_library() -> list[Path]:
    """Every track file in the Music.app (or iTunes) library, under source_root.

    Files in the music folder that the library does not list (removed from
    Music.app with "Keep File", or never added) are not synced.
    """
    paths = [Path(unicodedata.normalize("NFC", p)) for p in export_playlists.library_paths()]
    if not paths:
        sys.exit("The Music library returned no tracks; refusing to sync.")
    inside = []
    for p in paths:
        try:
            p.relative_to(SOURCE_ROOT)
            inside.append(p)
        except ValueError:
            pass
    if not inside:
        sys.exit(f"No library tracks are under source_root ({SOURCE_ROOT}); check the config.")
    return sorted(set(inside))


def queue_lyrics(
    src: Path,
    rel: str,
    device_files: dict[str, tuple[str, int]],
    device_dirs: dict[str, str],
    pushes: dict[str, tuple[str, Path]],
    counts: dict,
) -> None:
    """Queue the timed lyrics (<song>.lrc, see fetch_lyrics.py) beside a track, if new or changed."""
    lrc = src.with_suffix(".lrc")
    if not lrc.is_file():
        return
    lrc_rel = os.path.splitext(rel)[0] + ".lrc"
    if device_files.get(key(lrc_rel), ("", -1))[1] == lrc.stat().st_size:
        return
    dst = f"{ANDROID_ROOT}/{device_spelling(lrc_rel, device_files, device_dirs)}"
    pushes[key(dst)] = (dst, lrc)
    counts["lyrics"] = counts.get("lyrics", 0) + 1


def plan_library(
    library: list[Path],
    device_files: dict[str, tuple[str, int]],
    device_dirs: dict[str, str],
    pushes: dict[str, tuple[str, Path]],
    dry_run: bool,
) -> dict:
    """Queue every library track that is missing or changed on the device."""
    counts: dict = {"queued": 0, "already": 0, "drm": 0, "evicted": 0, "missing": 0, "total": 0,
                    "issues": []}
    for src in library:
        ext = src.suffix.lower()
        if ext in DRM_EXTENSIONS:
            counts["drm"] += 1
            continue
        if ext not in AUDIO_EXTENSIONS:
            continue  # e.g. music videos
        counts["total"] += 1
        rel = src.relative_to(SOURCE_ROOT).as_posix()
        if is_icloud_placeholder(src):
            counts["evicted"] += 1
            continue
        if not src.exists():
            counts["issues"].append(f"❓ missing on the Mac: {rel}")
            counts["missing"] += 1
            continue
        queue_lyrics(src, rel, device_files, device_dirs, pushes, counts)
        if device_files.get(key(rel), ("", -1))[1] == src.stat().st_size:
            counts["already"] += 1
            continue
        dst = f"{ANDROID_ROOT}/{device_spelling(rel, device_files, device_dirs)}"
        pushes[key(dst)] = (dst, src)
        counts["queued"] += 1
    return {"name": "Library", "kind": "library", **counts}


def plan_player(
    library: list[Path],
    device_files: dict[str, tuple[str, int]],
    pushes: dict[str, tuple[str, Path]],
    device_name: str,
    dry_run: bool,
) -> list[dict]:
    """Mirror the whole library, then write playlists that point into it."""
    if not SOURCE_ROOT.exists():
        sys.exit(f"Source music folder not found: {SOURCE_ROOT}")
    if not APPLE_DIR.exists():
        sys.exit(f"Apple folder not found: {APPLE_DIR}")

    if PLAYLIST_NAMES:
        playlist_files = [APPLE_DIR / f"{name}.m3u8" for name in PLAYLIST_NAMES]
    else:
        playlist_files = sorted(APPLE_DIR.glob("*.m3u8"))
    if not playlist_files:
        sys.exit(f"No playlists found in: {APPLE_DIR}")

    device_dirs = device_dir_map(device_files)
    summary = plan_library(library, device_files, device_dirs, pushes, dry_run)
    # Tracks are already queued by plan_library; pushes is keyed by
    # destination, so playlists only add their own .m3u8 files.
    return [summary] + [
        plan_playlist(p, device_files, device_dirs, pushes, device_name, dry_run)
        for p in playlist_files
    ]


def plan_player_stale(
    library: list[Path],
    device_files: dict[str, tuple[str, int]],
    playlists: list[str],
    device_name: str,
    dry_run: bool,
) -> list[str]:
    """Device paths under android_root to delete with --delete.

    - tracks that are not in the Music library (renamed, moved or removed
      on the Mac), plus the .lrc/.jpg the player saved beside them;
    - other files in folders that have no tracks left (e.g. cover.jpg);
    - this device's playlists that are no longer in Apple/.
    Other devices' playlist folders are left alone.
    """
    local = {key(p.relative_to(SOURCE_ROOT).as_posix()) for p in library}

    playlist_dir = key(f"_playlists/{device_name}/")
    keep_playlists = {key(f"_playlists/{device_name}/{n}.m3u8") for n in playlists}
    stale: list[str] = []
    tracks: dict[str, str] = {}  # key → rel, audio files only
    others: dict[str, str] = {}
    for k, (rel, _) in device_files.items():
        if k.startswith(key("_playlists/")):
            if k.startswith(playlist_dir) and k not in keep_playlists:
                stale.append(rel)
        elif os.path.basename(k).startswith("."):
            continue  # hidden files belong to the player app (e.g. .ttxfolder)
        elif os.path.splitext(k)[1] in AUDIO_EXTENSIONS:
            tracks[k] = rel
        else:
            others[k] = rel

    gone = {k for k in tracks if k not in local}
    stale += [tracks[k] for k in gone]
    stems_gone = {os.path.splitext(k)[0] for k in gone}
    dirs_kept = {os.path.dirname(k) for k in tracks if k not in gone}
    stale += [rel for k, rel in others.items()
              if os.path.splitext(k)[0] in stems_gone or os.path.dirname(k) not in dirs_kept]

    return sorted(f"{ANDROID_ROOT}/{rel}" for rel in stale)


# ─── --flac: mirror the FLAC folder ────────────────────────────────────────

def plan_flac(
    pushes: dict[str, tuple[str, Path]],
    attached: bool,
    delete: bool,
    dry_run: bool,
) -> dict:
    """Queue changed FLAC files in `pushes`; return counts and stale device paths."""
    if not FLAC_SOURCE.exists():
        sys.exit(f"FLAC folder not found: {FLAC_SOURCE}")
    device_files = list_device_files(FLAC_ROOT) if attached else {}
    counts: dict = {"queued": 0, "already": 0, "evicted": 0, "total": 0, "issues": []}
    local: set[str] = set()
    for p in sorted(FLAC_SOURCE.rglob("*")):
        if not p.is_file():
            continue
        if p.name.startswith("."):
            if p.name.endswith(".icloud"):
                counts["issues"].append(f"⏬ not downloaded: {p.relative_to(FLAC_SOURCE)}")
                counts["evicted"] += 1
            continue
        rel = p.relative_to(FLAC_SOURCE).as_posix()
        local.add(key(rel))
        counts["total"] += 1
        if device_files.get(key(rel), ("", -1))[1] == p.stat().st_size:
            counts["already"] += 1
        else:
            dst = f"{FLAC_ROOT}/{rel}"
            pushes[key(dst)] = (dst, p)
            counts["queued"] += 1

    stale = (
        sorted(f"{FLAC_ROOT}/{rel}" for k, (rel, _) in device_files.items() if k not in local)
        if delete
        else []
    )
    return {"name": "FLAC", "kind": "flac", **counts, "stale": stale}


# ─── ENTRY POINT ───────────────────────────────────────────────────────────

def main() -> None:
    global ANDROID_ROOT, FLAC_ROOT, PLAYLIST_COPY_DIR
    load_config()

    parser = argparse.ArgumentParser(
        description="Sync playlists and the FLAC folder to an Android device over USB (adb)."
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--all",
        dest="mode",
        action="store_const",
        const="all",
        help="Sync playlists and the FLAC folder (default).",
    )
    mode.add_argument(
        "--player",
        dest="mode",
        action="store_const",
        const="player",
        help="Sync Apple playlists and their tracks only.",
    )
    mode.add_argument(
        "--flac",
        dest="mode",
        action="store_const",
        const="flac",
        help="Sync the FLAC folder only.",
    )
    parser.set_defaults(mode="all")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview actions without pushing files.",
    )
    parser.add_argument(
        "--delete",
        action="store_true",
        help="Remove device files that no longer exist locally (_player and/or FLAC).",
    )
    parser.add_argument(
        "--verbose",
        action="store_true",
        help="List every file to push and every issue (default: the first few).",
    )
    parser.add_argument(
        "--device",
        default=DEFAULT_DEVICE,
        help=(
            "Device key from config.devices (for example: internal, a105). "
            "Defaults to config.default_device or 'internal'."
        ),
    )
    args = parser.parse_args()
    dry_run = args.dry_run

    selected_device = args.device.strip()
    if not selected_device:
        selected_device = DEFAULT_DEVICE
    if DEVICE_ROOTS:
        if selected_device not in DEVICE_ROOTS:
            known = ", ".join(sorted(DEVICE_ROOTS))
            sys.exit(f"Unknown device '{selected_device}'. Available: {known}")
        ANDROID_ROOT = DEVICE_ROOTS[selected_device]
    attached = device_attached(required=not dry_run)
    if attached:
        ANDROID_ROOT = resolve_device_case(ANDROID_ROOT)
    FLAC_ROOT = DEVICE_FLAC_ROOTS.get(
        selected_device, f"{ANDROID_ROOT.rsplit('/', 1)[0]}/{FLAC_SUBDIR}"
    )
    PLAYLIST_COPY_DIR = DEVICE_PLAYLIST_COPY_DIRS.get(selected_device, "")
    if attached:
        FLAC_ROOT = resolve_device_case(FLAC_ROOT)
        if PLAYLIST_COPY_DIR:
            PLAYLIST_COPY_DIR = resolve_device_case(PLAYLIST_COPY_DIR)
    limit = None if args.verbose else LIST_LIMIT
    more = "  (--verbose lists all)"
    print(f"\n🎧 {P.bold('Apple Music → Android sync')}" + (f"  🔍 {P.yellow('dry run')}" if dry_run else ""))
    header = [["📱 Device", selected_device]]
    if args.mode in ("all", "player"):
        header.append(["🎵 Music", ANDROID_ROOT])
    if args.mode in ("all", "flac"):
        header.append(["💿 FLAC", FLAC_ROOT])
    if PLAYLIST_COPY_DIR and args.mode in ("all", "player"):
        header.append(["📋 FiiO playlists", PLAYLIST_COPY_DIR])
    header.append(["🔧 Mode", args.mode + ("  + delete" if args.delete else "")])
    P.table(None, header)

    device_files = list_device_files(ANDROID_ROOT) if attached and args.mode != "flac" else {}

    pushes: dict[str, tuple[str, Path]] = {}
    results: list[dict] = []
    stale: list[str] = []
    stale_dirs: list[str] = []
    if args.mode in ("all", "player"):
        library = read_library()
        results += plan_player(library, device_files, pushes, selected_device, dry_run)
        if args.delete:
            names = [r["name"] for r in results[1:]]  # skip the library row
            stale += plan_player_stale(library, device_files, names, selected_device, dry_run)
            stale_dirs.append(ANDROID_ROOT)
    if args.mode in ("all", "flac"):
        flac = plan_flac(pushes, attached, args.delete, dry_run)
        stale += flac.pop("stale")
        stale_dirs.append(FLAC_ROOT)
        results.append(flac)

    def short(dst: str) -> str:
        """Device path relative to the folder it is in (music, FLAC or playlists)."""
        for root in (ANDROID_ROOT, FLAC_ROOT, PLAYLIST_COPY_DIR):
            if root and dst.startswith(root + "/"):
                return dst[len(root) + 1:]
        return dst

    lib = next((r for r in results if r["kind"] == "library"), None)
    playlists = [r for r in results if r["kind"] == "playlist"]
    flac_r = next((r for r in results if r["kind"] == "flac"), None)
    track_pushes = [dst for dst, src in pushes.values() if src.suffix != ".m3u8"]

    if lib:
        P.title("📚", "Library", f"{num(lib['total'])} tracks in Music.app → {ANDROID_ROOT}")
        P.items("", lib["issues"], limit, more)
        if dry_run:
            new = [d[len(ANDROID_ROOT) + 1:] for d in track_pushes if d.startswith(ANDROID_ROOT + "/")]
            if new:
                print(f"     📤 {num(len(new))} to push:")
                P.items("  ", new, limit, more)
    if playlists:
        P.title("📋", "Playlists", f"Apple/*.m3u8 → {PLAYLIST_COPY_DIR or ANDROID_ROOT + '/_playlists'}")
        for r in playlists:
            if r["issues"]:
                print(f"     {P.bold(r['name'])}")
                P.items("", r["issues"], limit, more)
        rows = []
        for r in playlists:
            issues = r["missing"] + r["evicted"] + r["outside"] + r["drm"]
            ok = r["already"] + r["queued"]
            rows.append([
                ("✅ " if issues == 0 else "❗ ") + r["name"], num(r["total"]), num(ok),
                num(r["queued"]) if r["queued"] else "·", num(issues) if issues else "·",
            ])
        P.table(["Playlist", "Tracks", "In playlist", "📤 New", "❗ Issues"], rows, "<>>>>")
    if flac_r:
        P.title("💿", "FLAC", f"{FLAC_SOURCE} → {FLAC_ROOT}")
        P.items("", flac_r["issues"], limit, more)
        if dry_run:
            new = [d[len(FLAC_ROOT) + 1:] for d in track_pushes if d.startswith(FLAC_ROOT + "/")]
            if new:
                print(f"     📤 {num(len(new))} to push:")
                P.items("  ", new, limit, more)
    if args.delete:
        P.title("🧹", "Delete from device", f"{num(len(stale))} files not in the library / FLAC folder")
        # Always list deletions in full on a dry run: they are destructive.
        if dry_run:
            for root in dict.fromkeys(stale_dirs):
                here = [short(p) for p in stale if p.startswith(root + "/")]
                if here:
                    print(f"     📁 {P.dim(root)}")
                    P.items("  ", here)

    push_bytes = sum(src.stat().st_size for _, src in pushes.values())
    free = device_free_bytes(ANDROID_ROOT) if attached else -1

    P.title("📊", "Summary")
    rows = []
    if lib:
        rows.append(["📚 Library", num(lib["total"]), num(lib["already"]), num(lib["queued"]),
                     num(lib["missing"] + lib["evicted"]), num(lib["drm"])])
    if flac_r:
        rows.append(["💿 FLAC", num(flac_r["total"]), num(flac_r["already"]), num(flac_r["queued"]),
                     num(flac_r["evicted"]), "·"])
    if rows:
        P.table(["", "Files", "✅ On device", "📤 To push", "❓ Missing", "🔒 DRM"], rows, "<>>>>>")
    if lib and lib.get("lyrics"):
        print(f"  🎤 Timed lyrics (.lrc) to push: {num(lib['lyrics'])}")
    print(f"  📦 To push: {P.bold(num(len(pushes)))} files ({human(push_bytes)})"
          + (f"   🧹 To delete: {P.bold(num(len(stale)))}" if args.delete else "")
          + (f"   💾 Free on device: {human(free)}" if free >= 0 else ""))
    if free >= 0 and push_bytes + FREE_SPACE_MARGIN > free:
        sys.exit(f"  ❌ {P.red('Not enough free space on the device.')}")

    if dry_run:
        print(f"\n🔍 {P.yellow('Dry run: nothing was changed.')}")
        return

    failed = 0
    if pushes:
        P.title("📤", "Pushing", f"{num(len(pushes))} files, {human(push_bytes)}")
    done_bytes = 0
    for i, (dst, src) in enumerate(pushes.values(), 1):
        n = src.stat().st_size
        done_bytes += n
        shown = short(dst)
        pct = 100 * done_bytes // max(push_bytes, 1)
        print(f"  {P.dim(f'[{i:>{len(str(len(pushes)))}}/{len(pushes)}] {pct:>3}%')}  {shown}  {P.dim(human(n))}")
        if not push(src, dst):
            failed += 1
    if stale:
        P.title("🧹", "Deleting", f"{num(len(stale))} files")
    for i in range(0, len(stale), DELETE_BATCH):
        batch = stale[i:i + DELETE_BATCH]
        for path in batch:
            print(f"  {P.dim('-')} {short(path)}")
        adb_shell("rm -f " + " ".join(shlex.quote(p) for p in batch), check=False)
    if stale:
        for root in stale_dirs:
            adb_shell(f"find {shlex.quote(root)} -mindepth 1 -type d -empty -delete", check=False)
    if pushes or stale:
        media_scan([ANDROID_ROOT, FLAC_ROOT, PLAYLIST_COPY_DIR])

    print()
    if failed:
        print(f"❌ {P.red(f'{failed} push(es) failed')}  ·  📤 {num(len(pushes) - failed)} pushed")
        sys.exit(1)
    if pushes or stale:
        print(f"✅ {P.green('Done')}  ·  📤 {num(len(pushes))} pushed ({human(push_bytes)})"
              + (f"  ·  🧹 {num(len(stale))} deleted" if stale else "")
              + "  ·  🔄 media scan started")
    else:
        print(f"✅ {P.green('Everything is up to date.')}")


if __name__ == "__main__":
    main()
