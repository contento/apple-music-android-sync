"""
pretty.py

Terminal output helpers shared by the scripts: boxed tables, section
titles, numbers and sizes. Standard library only.

Icons are emoji that terminals draw two columns wide (East Asian Width
"W"), so tables stay aligned; avoid ones that need U+FE0F (⚠️, 🗑️, ⏭️).
Colour is used only on a terminal and never when NO_COLOR is set.
"""

from __future__ import annotations

import os
import sys
import unicodedata

COLOR = sys.stdout.isatty() and not os.environ.get("NO_COLOR")


def _sgr(code: str, s: str) -> str:
    return f"\033[{code}m{s}\033[0m" if COLOR else s


def bold(s: str) -> str:
    return _sgr("1", s)


def dim(s: str) -> str:
    return _sgr("2", s)


def green(s: str) -> str:
    return _sgr("32", s)


def yellow(s: str) -> str:
    return _sgr("33", s)


def red(s: str) -> str:
    return _sgr("31", s)


def width(s: str) -> int:
    """Columns `s` takes in a terminal (wide emoji count 2, ANSI codes 0)."""
    w, esc = 0, False
    for c in s:
        if esc:
            esc = c != "m"
            continue
        if c == "\033":
            esc = True
        elif unicodedata.combining(c) or c in "‍︎️":
            continue
        else:
            w += 2 if unicodedata.east_asian_width(c) in "WF" else 1
    return w


def pad(s: str, n: int, align: str = "<") -> str:
    gap = " " * max(0, n - width(s))
    return s + gap if align == "<" else gap + s


def num(n: int) -> str:
    return f"{n:,}"


def size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024 or unit == "GB":
            return f"{n:.1f} {unit}" if unit != "B" else f"{int(n)} B"
        n /= 1024
    return f"{n:.1f} GB"


def title(icon: str, text: str, note: str = "") -> None:
    print(f"\n{icon} {bold(text)}" + (f"  {dim(note)}" if note else ""))


def table(headers: list[str] | None, rows: list[list[str]], align: str = "", indent: int = 2) -> None:
    """Print a boxed table. `align` has one '<' or '>' per column (default '<')."""
    cols = len(headers) if headers else len(rows[0])
    align = (align or "").ljust(cols, "<")
    cells = ([headers] if headers else []) + rows
    w = [max(width(str(r[i])) for r in cells) for i in range(cols)]
    sp = " " * indent

    def line(l: str, m: str, r: str) -> str:
        return sp + l + m.join("─" * (x + 2) for x in w) + r

    def row(r: list[str], head: bool = False) -> str:
        parts = [pad(bold(str(c)) if head else str(c), w[i], align[i]) for i, c in enumerate(r)]
        return sp + "│ " + " │ ".join(parts) + " │"

    print(line("┌", "┬", "┐"))
    if headers:
        print(row(headers, head=True))
        print(line("├", "┼", "┤"))
    for r in rows:
        print(row(r))
    print(line("└", "┴", "┘"))


def items(icon: str, lines: list[str], limit: int | None = None, more: str = "") -> None:
    """Print `lines` with an icon, at most `limit` of them, then '… N more'."""
    shown = lines if limit is None else lines[:limit]
    for l in shown:
        print(f"     {icon} {l}")
    if len(lines) > len(shown):
        print(dim(f"     … {num(len(lines) - len(shown))} more{more}"))
