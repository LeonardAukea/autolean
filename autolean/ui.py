"""Shared terminal output: one console, one theme, one glyph vocabulary."""

from __future__ import annotations

import sys
from contextlib import AbstractContextManager, nullcontext
from pathlib import Path

from rich.console import Console
from rich.theme import Theme

THEME = Theme(
    {
        "ok": "green",
        "fail": "red",
        "warn": "yellow",
        "skip": "yellow",
        "accent": "cyan",
        "note": "magenta",
        "provenance": "dim",
    }
)

# emoji/highlight off: Lean goals and model output print byte-exact.
console = Console(theme=THEME, emoji=False, highlight=False)

GLYPH_OK = "✓"
GLYPH_FAIL = "✗"
GLYPH_SKIP = "→"


def command() -> str:
    """Return the command a reader types to run this program again.

    Printed guidance has to match how the reader started the program: an
    installed console script, or the module.
    """
    name = Path(sys.argv[0]).name if sys.argv and sys.argv[0] else ""
    if name in {"__main__.py", "-c", ""}:
        return "python -m autolean"
    return name


def status(message: str, *, spinner: str = "dots") -> AbstractContextManager[object]:
    """Animated spinner on a terminal; one dim narration line when piped."""
    if console.is_terminal:
        return console.status(f"[provenance]{message}[/]", spinner=spinner)
    console.print(f"[provenance]{message}[/]")
    return nullcontext()


def phase(title: str) -> None:
    """Left-aligned dim rule marking a workflow phase transition."""
    console.print()
    console.rule(f"[bold]{title}[/]", style="dim", align="left")


def ok(message: str) -> None:
    """One success line: green check, then the message."""
    console.print(f"  [ok]{GLYPH_OK}[/] {message}")


def fail(message: str) -> None:
    """One failure line: red cross, then the message."""
    console.print(f"  [fail]{GLYPH_FAIL}[/] {message}")


def warn(message: str) -> None:
    """One warning line: yellow bang, then the message."""
    console.print(f"  [warn]![/] {message}")


def kv(label: str, value: str) -> None:
    """One aligned provenance line: dim label, plain value."""
    console.print(f"  [provenance]{label:<12}[/] {value}")
