"""Shared terminal output: one console, one theme, one glyph vocabulary."""

from __future__ import annotations

from contextlib import AbstractContextManager, nullcontext

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
