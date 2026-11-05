"""
player_config.py

Load the shared config: sync_apple_to_android_config.toml, or the older
sync_apple_to_android_config.json when there is no .toml file.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_TOML = SCRIPT_DIR / "sync_apple_to_android_config.toml"
CONFIG_JSON = SCRIPT_DIR / "sync_apple_to_android_config.json"


def config_file() -> Path | None:
    """The config file in use, or None if there is none."""
    for path in (CONFIG_TOML, CONFIG_JSON):
        if path.exists():
            return path
    return None


def load_config(required: bool = False) -> dict:
    """Read the config. Missing → {} (or exit if required); invalid → exit."""
    path = config_file()
    if path is None:
        if required:
            sys.exit(f"Config file not found: {CONFIG_TOML}")
        return {}

    if path.suffix == ".toml":
        try:
            import tomllib
        except ImportError:
            sys.exit(f"Reading {path.name} needs Python 3.11 or newer.")
        try:
            with path.open("rb") as f:
                return tomllib.load(f)
        except tomllib.TOMLDecodeError as exc:
            sys.exit(f"Invalid TOML in {path}: {exc}")

    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        sys.exit(f"Invalid JSON in {path}: {exc}")
