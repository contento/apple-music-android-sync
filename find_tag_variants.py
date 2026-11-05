#!/usr/bin/env python3
"""
find_tag_variants.py

Version : v1.1
Created : 2026-11-05

Find Music.app tag values that differ only by case, accents or spacing,
e.g. "Miguel Bose" vs "Miguel Bosé" or "toto" vs "Toto". Players group by
these tags, so each variant shows up as a separate artist/album/genre.

Reads every track's tags straight from Music.app (macOS) or iTunes
(Windows, via its COM interface — the Apple Music app for Windows has no
scripting API) and prints each group of variants with its track count,
most common first.

--fix (macOS only) rewrites every variant to one spelling per group:
  - only words the variants disagree on are touched, one word at a time
  - accents: the accented form wins ("Bose" → "Bosé")
  - case: real words capitalised, short words (de, la, the, of…) lowercase
    mid-name, picking from the spellings the variants already use
    ("toto" → "Toto", "Willie colon" → "Willie Colón", "Heal The World" → "Heal the World")
  - spacing: extra spaces are removed
  - config "tag_overrides" (list of spellings) beats all of the above when
    the rules pick wrong, e.g. "Diomedes Díaz" over "Diómedes Díaz"; it is
    applied to every matching value, even where there is only one spelling
  - Music.app re-cases other fields to spellings it already knows when it
    saves a track, so run --fix again until it reports nothing to fix
The old values are saved to tag_undo/<timestamp>.json first; --undo restores them.

Usage:
    python3 find_tag_variants.py                 # report all fields
    python3 find_tag_variants.py artist album    # only these fields
    python3 find_tag_variants.py --fix           # show the plan, ask, then apply
    python3 find_tag_variants.py --fix --yes     # apply without asking
    python3 find_tag_variants.py --undo tag_undo/20261105-143000.json

Fields: artist, albumArtist, composer, genre, album, name
"""

from __future__ import annotations

import argparse
import collections
import json
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
CONFIG_FILE = SCRIPT_DIR / "sync_apple_to_android_config.json"
UNDO_DIR = SCRIPT_DIR / "tag_undo"

# JXA: fetch each tag for all library tracks in one Apple Event per field.
JXA = """
const t = Application('Music').libraryPlaylists[0].tracks;
JSON.stringify({pid: t.persistentID(), artist: t.artist(), albumArtist: t.albumArtist(),
                album: t.album(), name: t.name(), composer: t.composer(), genre: t.genre()});
"""

# JXA: apply [index, persistentID, field, value] changes. Index lookup is
# fast; fall back to a persistentID search if the library order changed.
JXA_WRITE = """
const changes = %s;
const t = Application('Music').libraryPlaylists[0].tracks;
const failed = [];
for (const [i, pid, field, value] of changes) {
    try {
        let track = t[i];
        if (track.persistentID() !== pid) {
            const found = t.whose({persistentID: pid})();
            if (!found.length) throw new Error('track not found');
            track = found[0];
        }
        track[field] = value;
    } catch (e) {
        failed.push([pid, field, String(e)]);
    }
}
JSON.stringify(failed);
"""

# PowerShell: same tags from iTunes for Windows over COM, one track at a time.
ITUNES_PS = """
[Console]::OutputEncoding = [Text.Encoding]::UTF8
$fields = 'Artist', 'AlbumArtist', 'Album', 'Name', 'Composer', 'Genre'
$out = [ordered]@{}
foreach ($f in $fields) { $out[$f] = [Collections.Generic.List[string]]::new() }
$itunes = New-Object -ComObject iTunes.Application
foreach ($track in $itunes.LibraryPlaylist.Tracks) {
    foreach ($f in $fields) { $out[$f].Add([string]$track.$f) }
}
[ordered]@{
    artist = $out.Artist.ToArray(); albumArtist = $out.AlbumArtist.ToArray()
    album = $out.Album.ToArray(); name = $out.Name.ToArray()
    composer = $out.Composer.ToArray(); genre = $out.Genre.ToArray()
} | ConvertTo-Json -Compress -Depth 3
"""

# field → (label, key builder). Album and title are only compared within
# the same artist, otherwise unrelated "Grandes Exitos" albums would match.
# The last element of the key is the field's own value.
FIELDS = {
    "artist":      ("Artist",             lambda d, i: (d["artist"][i],)),
    "albumArtist": ("Album Artist",       lambda d, i: (d["albumArtist"][i],)),
    "composer":    ("Composer",           lambda d, i: (d["composer"][i],)),
    "genre":       ("Genre",              lambda d, i: (d["genre"][i],)),
    "album":       ("Album (per artist)", lambda d, i: (d["albumArtist"][i] or d["artist"][i], d["album"][i])),
    "name":        ("Title (per artist)", lambda d, i: (d["artist"][i], d["name"][i])),
}

# Words that stay lowercase mid-name (Spanish, English, French, Italian, Portuguese).
MINOR_WORDS = {
    "a", "al", "con", "de", "del", "e", "el", "en", "la", "las", "lo", "los", "o",
    "para", "por", "sin", "u", "un", "una", "y",
    "an", "and", "as", "at", "but", "by", "for", "from", "in", "of", "on", "or",
    "the", "to", "with",
    "des", "du", "et", "le", "les",
    "da", "di", "il", "na",
}

WRITE_BATCH = 50


def fold(s: str) -> str:
    # Drop accents, fold case and collapse whitespace.
    s = unicodedata.normalize("NFD", s)
    s = "".join(c for c in s if unicodedata.category(c) != "Mn")
    return " ".join(s.casefold().split())


def marks(s: str) -> int:
    return sum(unicodedata.category(c) == "Mn" for c in unicodedata.normalize("NFD", s))


def letters(word: str) -> str:
    return "".join(c for c in word if c.isalpha())


def well_cased(word: str, minor: bool) -> bool:
    """Minor words lowercase; others capitalised but not ALL CAPS."""
    core = letters(word)
    if not core:
        return True
    if minor:
        return core.islower()
    return core[0].isupper() and not (len(core) > 1 and core.isupper())


def choose(variants: collections.Counter, preferred: dict[str, str]) -> str:
    """Pick one spelling for a group of values that fold to the same key.

    Words are decided one at a time, and only where the variants disagree:
    the accented form wins, cased like the most common well-cased variant.
    """
    override = preferred.get(fold(next(iter(variants))))
    if override:
        return override

    columns: list[collections.Counter] = []
    for value, n in variants.items():
        for i, word in enumerate(value.split()):
            if i == len(columns):
                columns.append(collections.Counter())
            columns[i][unicodedata.normalize("NFC", word)] += n

    words = []
    for i, column in enumerate(columns):
        if len(column) == 1:
            words.append(next(iter(column)))
            continue
        # First word, last word and the word after "(" are never minor.
        minor = 0 < i < len(columns) - 1 and not next(iter(column)).startswith("(") \
            and letters(next(iter(column))).casefold() in MINOR_WORDS
        good = [w for w in column if well_cased(w, minor)]
        if good:
            # Most common, then regular case ("Tik" over "TiK").
            cased = max(good, key=lambda w: (column[w], letters(w)[1:].islower()))
        else:
            # No variant is cased the usual way: keep the most common non-CAPS one.
            pool = [w for w in column if not letters(w).isupper()] or [w.capitalize() for w in column]
            cased = max(pool, key=lambda w: column.get(w, 0))
            if i == 0:
                cased = cased[:1].upper() + cased[1:]
        accented = max(column, key=marks)
        if marks(accented) > marks(cased) and len(accented) == len(cased):
            cased = "".join(
                a.upper() if c.isupper() else a.lower() if c.islower() else a
                for a, c in zip(accented, cased)
            )
        words.append(cased)
    return " ".join(words)


def read_tags() -> dict[str, list[str]]:
    if sys.platform == "win32":
        app, cmd = "iTunes", ["powershell", "-NoProfile", "-NonInteractive", "-Command", ITUNES_PS]
    else:
        app, cmd = "Music.app", ["osascript", "-l", "JavaScript", "-e", JXA]
    res = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8")
    if res.returncode != 0:
        sys.exit(f"Could not read {app} library: {res.stderr.strip()}")
    return json.loads(res.stdout)


def load_overrides() -> dict[str, str]:
    if not CONFIG_FILE.exists():
        return {}
    raw = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    return {fold(s): unicodedata.normalize("NFC", s) for s in raw.get("tag_overrides", [])}


def variant_groups(
    tags: dict[str, list[str]], field: str, keep: set[str] = frozenset()
) -> list[dict[tuple, list[int]]]:
    """Return groups with more than one spelling: {key tuple: [track indexes]}.

    Groups whose folded value is in `keep` are returned even with one spelling.
    """
    _, key_of = FIELDS[field]
    groups: dict[tuple, dict[tuple, list[int]]] = collections.defaultdict(lambda: collections.defaultdict(list))
    for i in range(len(tags["name"])):
        value = key_of(tags, i)
        if value[-1]:
            groups[tuple(fold(v) for v in value)][value].append(i)
    hits = [g for k, g in groups.items() if len(g) > 1 or k[-1] in keep]
    return sorted(hits, key=lambda g: fold(" ".join(next(iter(g)))))


def report(tags: dict[str, list[str]], field: str) -> None:
    hits = variant_groups(tags, field)
    print(f"── {FIELDS[field][0]}: {len(hits)} groups")
    for group in hits:
        ranked = sorted(group.items(), key=lambda kv: -len(kv[1]))
        print("   " + "  |  ".join(f"{' / '.join(v)} ({len(ix)})" for v, ix in ranked))
    print()


def plan_fixes(tags: dict[str, list[str]], fields: list[str]) -> list[dict]:
    preferred = load_overrides()
    changes: list[dict] = []
    for field in fields:
        print(f"── {FIELDS[field][0]}")
        for group in variant_groups(tags, field, keep=set(preferred)):
            # Album/title groups can differ only by artist; then there is nothing to fix
            # here unless an override applies.
            counts = collections.Counter()
            for value, indexes in group.items():
                counts[value[-1]] += len(indexes)
            if len(counts) < 2 and fold(next(iter(counts))) not in preferred:
                continue
            target = choose(counts, preferred)
            touched = [(v, n) for v, n in counts.most_common() if v != target]
            if not touched:
                continue
            context = f"{next(iter(group))[0]} / " if len(next(iter(group))) > 1 else ""
            print(f"   {context}{'  |  '.join(f'{v!r} ({n})' for v, n in touched)}  →  {target!r}")
            for indexes in group.values():
                for i in indexes:
                    if tags[field][i] != target:
                        changes.append({
                            "index": i, "pid": tags["pid"][i], "field": field,
                            "old": tags[field][i], "new": target,
                        })
        print()
    return changes


def write_tags(changes: list[dict], value_key: str) -> int:
    """Write changes to Music.app in batches; return the number of failures."""
    failed = 0
    for start in range(0, len(changes), WRITE_BATCH):
        batch = changes[start:start + WRITE_BATCH]
        payload = json.dumps([[c["index"], c["pid"], c["field"], c[value_key]] for c in batch])
        res = subprocess.run(
            ["osascript", "-l", "JavaScript", "-e", JXA_WRITE % payload],
            capture_output=True, text=True, encoding="utf-8",
        )
        if res.returncode != 0:
            sys.exit(f"Could not write to Music.app: {res.stderr.strip()}")
        for pid, field, error in json.loads(res.stdout):
            print(f"    ✗ {pid} {field}: {error}")
            failed += 1
        print(f"  {min(start + WRITE_BATCH, len(changes))}/{len(changes)}")
    return failed


def fix(fields: list[str], yes: bool) -> None:
    tags = read_tags()
    print(f"{len(tags['name'])} tracks\n")
    changes = plan_fixes(tags, fields)
    if not changes:
        print("Nothing to fix.")
        return
    tracks = len({c["pid"] for c in changes})
    print(f"{len(changes)} tag changes on {tracks} tracks.")
    if not yes and input("Apply? [y/N] ").strip().lower() != "y":
        print("Nothing changed.")
        return

    UNDO_DIR.mkdir(exist_ok=True)
    undo = UNDO_DIR / f"{time.strftime('%Y%m%d-%H%M%S')}.json"
    undo.write_text(json.dumps(changes, ensure_ascii=False, indent=1), encoding="utf-8")
    print(f"Undo file: {undo.relative_to(SCRIPT_DIR)}")
    failed = write_tags(changes, "new")
    print(f"Done: {len(changes) - failed} changed, {failed} failed.")
    if failed:
        sys.exit(1)


def undo(path: Path) -> None:
    changes = json.loads(path.read_text(encoding="utf-8"))
    print(f"Restoring {len(changes)} tag values from {path}")
    failed = write_tags(changes, "old")
    print(f"Done: {len(changes) - failed} restored, {failed} failed.")
    if failed:
        sys.exit(1)


def main() -> None:
    parser = argparse.ArgumentParser(description="Find (and fix) Music.app tags that differ only by case/accents.")
    parser.add_argument("fields", nargs="*", help=f"Fields to check: {', '.join(FIELDS)} (default: all).")
    parser.add_argument("--fix", action="store_true", help="Rewrite variants to one spelling (macOS).")
    parser.add_argument("--yes", action="store_true", help="With --fix, apply without asking.")
    parser.add_argument("--undo", type=Path, metavar="FILE", help="Restore the values saved in an undo file.")
    args = parser.parse_args()

    fields = args.fields or list(FIELDS)
    unknown = [f for f in fields if f not in FIELDS]
    if unknown:
        sys.exit(f"Unknown field(s): {', '.join(unknown)}. Available: {', '.join(FIELDS)}")
    if (args.fix or args.undo) and sys.platform != "darwin":
        sys.exit("--fix and --undo only work with Music.app on macOS.")

    if args.undo:
        undo(args.undo)
    elif args.fix:
        fix(fields, args.yes)
    else:
        tags = read_tags()
        print(f"{len(tags['name'])} tracks\n")
        for field in fields:
            report(tags, field)


if __name__ == "__main__":
    main()
