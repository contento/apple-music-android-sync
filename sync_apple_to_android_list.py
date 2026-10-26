#!/usr/bin/env python3
"""
sync_apple_to_android_list.py

Version : v3.0
Created : 2026-05-03

Full pipeline: Mac → Android over USB (adb) for Poweramp / Sony Music Player

  --player
    1. Read playlists in
         → ./Apple/*.m3u8

    2. Push every referenced track from the iCloud Music folder
         → <android_root>/<Artist>/<Album>/<Track>

    3. Write an Android-ready playlist with absolute paths
         → ./Android/_playlists/<device>/<name>.m3u8   (local copy)
         → <android_root>/_playlists/<device>/<name>.m3u8

  --flac
    Mirror the FLAC folder (Artist/Album/Track) next to android_root
         → <flac_root>/<Artist>/<Album>/<Track>
           (default flac_root: <parent of android_root>/_flac,
            e.g. /storage/emulated/0/music/_flac)
    --delete also removes device files that no longer exist locally.

  --all (default): both of the above.

Files are compared by size against what is already on the device, so
re-runs only push what changed.

Usage:
    python3 sync_apple_to_android_list.py                   # --all, default device (internal)
    python3 sync_apple_to_android_list.py --player          # playlists + their tracks
    python3 sync_apple_to_android_list.py --flac            # FLAC folder only
    python3 sync_apple_to_android_list.py --flac --delete   # also remove stale FLAC files
    python3 sync_apple_to_android_list.py --device a105     # use named device root
    python3 sync_apple_to_android_list.py --dry-run         # preview only

Requires: adb on PATH, USB debugging enabled, exactly one device attached
(or ANDROID_SERIAL set to pick one).
"""

from __future__ import annotations

import json
import argparse
import os
import shlex
import shutil
import subprocess
import sys
import unicodedata
import urllib.parse
from pathlib import Path

# ─── CONFIG ────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "sync_apple_to_android_config.json"

PLAYER_ROOT = SCRIPT_DIR
APPLE_DIR = SCRIPT_DIR / "Apple"
ANDROID_DIR = SCRIPT_DIR / "Android"
SOURCE_ROOT = SCRIPT_DIR
ANDROID_ROOT = "/storage/emulated/0/music/_player"
FLAC_ROOT = "/storage/emulated/0/music/_flac"
DEVICE_ROOTS: dict[str, str] = {}
DEVICE_FLAC_ROOTS: dict[str, str] = {}
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
# Leave some headroom on the device after an adb push.
FREE_SPACE_MARGIN = 500 * 1024 * 1024
# ───────────────────────────────────────────────────────────────────────────


def load_config() -> None:
    """Load all paths and playlist names from JSON config file."""
    global PLAYER_ROOT, APPLE_DIR, ANDROID_DIR
    global SOURCE_ROOT, ANDROID_ROOT, PLAYLIST_NAMES
    global DEVICE_ROOTS, DEVICE_FLAC_ROOTS, DEFAULT_DEVICE, FLAC_SOURCE, FLAC_SUBDIR

    if not CONFIG_FILE.exists():
        sys.exit(f"Config file not found: {CONFIG_FILE}")

    try:
        raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"Invalid JSON in {CONFIG_FILE}: {exc}")

    paths = raw.get("paths", {})
    PLAYER_ROOT = Path(paths.get("player_root", str(SCRIPT_DIR))).expanduser()
    APPLE_DIR = Path(paths.get("apple_dir", str(PLAYER_ROOT / "Apple"))).expanduser()
    ANDROID_DIR = Path(paths.get("android_dir", str(PLAYER_ROOT / "Android"))).expanduser()
    SOURCE_ROOT = Path(paths.get("source_root", "")).expanduser()
    ANDROID_ROOT = raw.get("android_root", ANDROID_ROOT)
    DEVICE_ROOTS = {}
    DEVICE_FLAC_ROOTS = {}
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


def human(n: int) -> str:
    size = float(n)
    for unit in ("B", "KB", "MB", "GB"):
        if size < 1024 or unit == "GB":
            return f"{size:.1f} {unit}"
        size /= 1024
    return f"{size:.1f} GB"


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
        print(f"  ⚠ {msg}\n  ⚠ dry-run continues as if the device were empty.")
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
        print(f"    ✗ push failed: {res.stderr.strip() or res.stdout.strip()}")
    return res.returncode == 0


def media_scan() -> None:
    res = adb_shell(
        "content call --uri content://media --method scan_volume --arg external_primary",
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
    pushes: dict[str, tuple[str, Path]],
    device_name: str,
    dry_run: bool,
) -> dict:
    """Queue missing tracks in `pushes` and write the Android playlist locally."""
    name = apple_path.stem
    print(f"\n━━━ {name} {'(dry-run) ' if dry_run else ''}━━━")

    playlist_rel = f"_playlists/{device_name}/{name}.m3u8"
    android_path = ANDROID_DIR / playlist_rel

    tracks = parse_m3u8(apple_path)
    print(f"  {len(tracks)} tracks in Apple/{apple_path.name}")

    new_lines: list[str] = ["#EXTM3U", f"#PLAYLIST:{name}"]
    counts: dict[str, int] = {
        "queued": 0, "already": 0, "drm": 0,
        "evicted": 0, "missing": 0, "outside": 0,
        "total": len(tracks),
    }

    for src in tracks:
        try:
            rel = src.relative_to(SOURCE_ROOT).as_posix()
        except ValueError:
            print(f"    ⊘ outside source root: {src}")
            counts["outside"] += 1
            continue

        if src.suffix.lower() in DRM_EXTENSIONS:
            counts["drm"] += 1
            continue

        if is_icloud_placeholder(src):
            print(f"    ⏬ not downloaded: {rel}")
            counts["evicted"] += 1
            continue

        if not src.exists():
            print(f"    ✗ missing: {rel}")
            counts["missing"] += 1
            continue

        if device_files.get(key(rel), ("", -1))[1] == src.stat().st_size:
            counts["already"] += 1
        else:
            if dry_run:
                print(f"    [dry-run] would push → {rel}")
            dst = f"{ANDROID_ROOT}/{rel}"
            pushes[key(dst)] = (dst, src)
            counts["queued"] += 1

        new_lines.append(f"{ANDROID_ROOT}/{rel}")

    if dry_run:
        print(f"  [dry-run] would write Android/{playlist_rel} and push it")
    else:
        android_path.parent.mkdir(parents=True, exist_ok=True)
        android_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        dst = f"{ANDROID_ROOT}/{playlist_rel}"
        pushes[key(dst)] = (dst, android_path)
        print(f"  wrote Android/{playlist_rel}")

    print(
        f"  to push={counts['queued']}  already={counts['already']}  "
        f"drm={counts['drm']}  evicted={counts['evicted']}  "
        f"missing={counts['missing']}  outside={counts['outside']}"
    )
    return {"name": name, **counts}


def plan_player(
    device_files: dict[str, tuple[str, int]],
    pushes: dict[str, tuple[str, Path]],
    device_name: str,
    dry_run: bool,
) -> list[dict]:
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

    return [plan_playlist(p, device_files, pushes, device_name, dry_run) for p in playlist_files]


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
    print(f"\n━━━ {FLAC_SUBDIR} {'(dry-run) ' if dry_run else ''}━━━")
    print(f"  source: {FLAC_SOURCE}")
    print(f"  target: {FLAC_ROOT}")

    device_files = list_device_files(FLAC_ROOT) if attached else {}
    counts = {"queued": 0, "already": 0, "evicted": 0, "total": 0}
    local: set[str] = set()
    for p in sorted(FLAC_SOURCE.rglob("*")):
        if not p.is_file():
            continue
        if p.name.startswith("."):
            if p.name.endswith(".icloud"):
                print(f"    ⏬ not downloaded: {p.relative_to(FLAC_SOURCE)}")
                counts["evicted"] += 1
            continue
        rel = p.relative_to(FLAC_SOURCE).as_posix()
        local.add(key(rel))
        counts["total"] += 1
        if device_files.get(key(rel), ("", -1))[1] == p.stat().st_size:
            counts["already"] += 1
        else:
            if dry_run:
                print(f"    [dry-run] would push → {rel}")
            dst = f"{FLAC_ROOT}/{rel}"
            pushes[key(dst)] = (dst, p)
            counts["queued"] += 1

    stale = (
        sorted(f"{FLAC_ROOT}/{rel}" for k, (rel, _) in device_files.items() if k not in local)
        if delete
        else []
    )
    if dry_run:
        for path in stale:
            print(f"    [dry-run] would delete → {path}")

    print(
        f"  to push={counts['queued']}  already={counts['already']}  "
        f"to delete={len(stale)}  evicted={counts['evicted']}"
    )
    return {"name": FLAC_SUBDIR, **counts, "stale": stale}


# ─── ENTRY POINT ───────────────────────────────────────────────────────────

def main() -> None:
    global ANDROID_ROOT, FLAC_ROOT
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
        help="Remove FLAC files on the device that no longer exist locally.",
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
    FLAC_ROOT = DEVICE_FLAC_ROOTS.get(
        selected_device, f"{ANDROID_ROOT.rsplit('/', 1)[0]}/{FLAC_SUBDIR}"
    )
    print(
        f"Using device '{selected_device}' with android_root={ANDROID_ROOT}  "
        f"flac_root={FLAC_ROOT}  mode={args.mode}"
    )

    attached = device_attached(required=not dry_run)
    device_files = list_device_files(ANDROID_ROOT) if attached and args.mode != "flac" else {}

    pushes: dict[str, tuple[str, Path]] = {}
    results: list[dict] = []
    stale: list[str] = []
    if args.mode in ("all", "player"):
        results += plan_player(device_files, pushes, selected_device, dry_run)
    if args.mode in ("all", "flac"):
        flac = plan_flac(pushes, attached, args.delete, dry_run)
        stale = flac.pop("stale")
        results.append(flac)

    push_bytes = sum(src.stat().st_size for _, src in pushes.values())
    print(f"\n  total to push: {len(pushes)} files ({human(push_bytes)})  to delete: {len(stale)}")
    if attached:
        free = device_free_bytes(ANDROID_ROOT)
        if free >= 0:
            print(f"  device free space: {human(free)}")
            if push_bytes + FREE_SPACE_MARGIN > free:
                sys.exit("Not enough free space on the device.")

    failed = 0
    if not dry_run:
        for i, (dst, src) in enumerate(pushes.values(), 1):
            print(f"  [{i}/{len(pushes)}] {dst}  ({human(src.stat().st_size)})")
            if not push(src, dst):
                failed += 1
        for path in stale:
            print(f"  ✗ delete {path}")
            adb_shell(f"rm -f {shlex.quote(path)}", check=False)
        if stale:
            adb_shell(f"find {shlex.quote(FLAC_ROOT)} -mindepth 1 -type d -empty -delete", check=False)
        if pushes or stale:
            media_scan()

    print("\n═══ overall ═══")
    width = max(len(r["name"]) for r in results)
    for r in results:
        line = (
            f"  {r['name']:<{width}}  "
            f"files={r['total']:>4}  "
            f"to push={r['queued']:>4}  already={r['already']:>4}  "
            f"evicted={r['evicted']:>3}"
        )
        if "missing" in r:
            line += f"  missing={r['missing']:>3}  drm={r['drm']:>3}"
        print(line)
    if failed:
        print(f"  ✗ {failed} push(es) failed")
        sys.exit(1)


if __name__ == "__main__":
    main()
