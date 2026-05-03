#!/usr/bin/env python3
"""
sync_apple_to_android_list.py

Version : v2.0
Created : 2026-05-03

Full pipeline: Apple Music → Android (Poweramp / Sony Music Player)

    1. Read playlists in
         → ./Apple/*.m3u8

    2. Copy every referenced track from the iCloud Music folder
     → Google Drive: …/My Drive/Music/_player/<relative path>
     (preserves Artist/Album/Track structure; idempotent)

  3. Write an Android-ready playlist with absolute paths for
     Poweramp and Sony Music Player
     → ./Android/<name>.m3u8          (local copy)
     → Google Drive: …/_player/<name>.m3u8  (syncs to phone)

Usage:
    python3 sync_apple_to_android_list.py                   # default device (internal)
    python3 sync_apple_to_android_list.py --device a105     # use named device root
    python3 sync_apple_to_android_list.py --dry-run         # preview only
"""

from __future__ import annotations

import json
import argparse
import shutil
import sys
import urllib.parse
from pathlib import Path

# ─── CONFIG ────────────────────────────────────────────────────────────────
SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "sync_apple_to_android_config.json"

PLAYER_ROOT = SCRIPT_DIR
APPLE_DIR = SCRIPT_DIR / "Apple"
ANDROID_DIR = SCRIPT_DIR / "Android"
SOURCE_ROOT = SCRIPT_DIR
DRIVE_TARGET = SCRIPT_DIR
ANDROID_ROOT = "/storage/emulated/0/music/_player"
DEVICE_ROOTS: dict[str, str] = {}
DEFAULT_DEVICE = "internal"
PLAYLIST_NAMES: list[str] = []


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
# ───────────────────────────────────────────────────────────────────────────


def load_config() -> None:
    """Load all paths and playlist names from JSON config file."""
    global PLAYER_ROOT, APPLE_DIR, ANDROID_DIR
    global SOURCE_ROOT, DRIVE_TARGET, ANDROID_ROOT, PLAYLIST_NAMES
    global DEVICE_ROOTS, DEFAULT_DEVICE

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
    DRIVE_TARGET = Path(paths.get("drive_target", "")).expanduser()
    ANDROID_ROOT = raw.get("android_root", ANDROID_ROOT)
    DEVICE_ROOTS = {}
    for name, entry in raw.get("devices", {}).items():
        if not isinstance(name, str):
            continue
        if isinstance(entry, str):
            root = entry.strip()
        elif isinstance(entry, dict):
            root = str(entry.get("android_root", "")).strip()
        else:
            root = ""
        if root:
            DEVICE_ROOTS[name.strip()] = root.rstrip("/")
    if "internal" not in DEVICE_ROOTS and ANDROID_ROOT:
        DEVICE_ROOTS["internal"] = str(ANDROID_ROOT).rstrip("/")
    DEFAULT_DEVICE = str(raw.get("default_device", "internal")).strip() or "internal"
    PLAYLIST_NAMES = extract_enabled_playlists(raw.get("playlists", []))


# ─── Parse, mirror to Drive, write Android playlist ─────────────────────────

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


def process_playlist(apple_path: Path, dry_run: bool) -> dict:
    name = apple_path.stem
    print(f"\n━━━ {name} {'(dry-run) ' if dry_run else ''}━━━")

    android_path = ANDROID_DIR / f"{name}.m3u8"
    drive_playlist = DRIVE_TARGET / f"{name}.m3u8"

    tracks = parse_m3u8(apple_path)
    print(f"  {len(tracks)} tracks in Apple/{apple_path.name}")

    # ── 2 & 3. Mirror tracks + build Android playlist ──────────────────────
    new_lines: list[str] = ["#EXTM3U"]
    counts: dict[str, int] = {
        "copied": 0, "already": 0, "drm": 0,
        "evicted": 0, "missing": 0, "outside": 0,
        "total": len(tracks),
    }

    for src in tracks:
        try:
            rel = src.relative_to(SOURCE_ROOT)
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

        dst = DRIVE_TARGET / rel
        if dry_run:
            if dst.exists() and dst.stat().st_size == src.stat().st_size:
                counts["already"] += 1
            else:
                print(f"    [dry-run] would copy → {rel}")
                counts["copied"] += 1
        else:
            dst.parent.mkdir(parents=True, exist_ok=True)
            if dst.exists() and dst.stat().st_size == src.stat().st_size:
                counts["already"] += 1
            else:
                shutil.copy2(src, dst)
                counts["copied"] += 1

        new_lines.append(f"{ANDROID_ROOT}/{rel.as_posix()}")

    # ── Write Android playlist ─────────────────────────────────────────────
    if dry_run:
        print(f"  [dry-run] would write Android/{android_path.name}")
        print(f"  [dry-run] would copy  → Drive/{drive_playlist.name}")
    else:
        ANDROID_DIR.mkdir(parents=True, exist_ok=True)
        android_path.write_text("\n".join(new_lines) + "\n", encoding="utf-8")
        shutil.copy2(android_path, drive_playlist)
        print(f"  wrote Android/{android_path.name} + Drive copy")

    print(
        f"  copied={counts['copied']}  already={counts['already']}  "
        f"drm={counts['drm']}  evicted={counts['evicted']}  "
        f"missing={counts['missing']}  outside={counts['outside']}"
    )
    return {"name": name, **counts}


# ─── PREFLIGHT ─────────────────────────────────────────────────────────────

def preflight(dry_run: bool) -> None:
    if not SOURCE_ROOT.exists():
        sys.exit(f"Source music folder not found: {SOURCE_ROOT}")
    if not APPLE_DIR.exists():
        sys.exit(f"Apple folder not found: {APPLE_DIR}")
    if not dry_run and not DRIVE_TARGET.parent.exists():
        sys.exit(
            f"Google Drive not mounted at: {DRIVE_TARGET.parent}\n"
            "Make sure Google Drive for desktop is running."
        )
    if not dry_run:
        for d in (ANDROID_DIR, DRIVE_TARGET):
            d.mkdir(parents=True, exist_ok=True)


# ─── ENTRY POINT ───────────────────────────────────────────────────────────

def main() -> None:
    load_config()

    parser = argparse.ArgumentParser(
        description="Sync Apple playlists to Android/Drive targets."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview actions without copying files.",
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
        global ANDROID_ROOT
        ANDROID_ROOT = DEVICE_ROOTS[selected_device]
    print(f"Using device '{selected_device}' with android_root={ANDROID_ROOT}")

    preflight(dry_run)
    if PLAYLIST_NAMES:
        playlist_files = [APPLE_DIR / f"{name}.m3u8" for name in PLAYLIST_NAMES]
    else:
        playlist_files = sorted(APPLE_DIR.glob("*.m3u8"))

    if not playlist_files:
        sys.exit(f"No playlists found in: {APPLE_DIR}")

    results = [process_playlist(p, dry_run) for p in playlist_files]

    print("\n═══ overall ═══")
    width = max(len(r["name"]) for r in results)
    for r in results:
        if "error" in r:
            print(f"  {r['name']:<{width}}  ERROR: {r['error']}")
        else:
            print(
                f"  {r['name']:<{width}}  "
                f"tracks={r['total']:>4}  "
                f"copied={r['copied']:>4}  already={r['already']:>4}  "
                f"missing={r['missing']:>3}  drm={r['drm']:>3}  "
                f"evicted={r['evicted']:>3}"
            )


if __name__ == "__main__":
    main()
