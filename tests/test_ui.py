"""Shared console behavior: glyphs, helpers, and non-TTY narration."""

from __future__ import annotations

from contextlib import nullcontext
from io import StringIO

import pytest
from rich.console import Console
from rich.status import Status

from autolean import ui


def _console(*, terminal: bool) -> tuple[Console, StringIO]:
    buffer = StringIO()
    console = Console(
        theme=ui.THEME,
        emoji=False,
        highlight=False,
        file=buffer,
        force_terminal=terminal,
        width=100,
    )
    return console, buffer


def test_glyph_constants() -> None:
    assert ui.GLYPH_OK == "✓"
    assert ui.GLYPH_FAIL == "✗"
    assert ui.GLYPH_SKIP == "→"


def test_console_prints_verbatim() -> None:
    assert ui.console._emoji is False
    assert ui.console._highlight is False
    console, buffer = _console(terminal=False)
    console.print(":rocket: at line 42")
    assert buffer.getvalue() == ":rocket: at line 42\n"


def test_status_narrates_when_piped(monkeypatch: pytest.MonkeyPatch) -> None:
    console, buffer = _console(terminal=False)
    monkeypatch.setattr(ui, "console", console)
    context = ui.status("Waiting for the model...")
    assert isinstance(context, nullcontext)
    assert "Waiting for the model..." in buffer.getvalue()
    with context:
        pass


def test_status_spins_on_terminal(monkeypatch: pytest.MonkeyPatch) -> None:
    console, buffer = _console(terminal=True)
    monkeypatch.setattr(ui, "console", console)
    context = ui.status("Building...")
    assert isinstance(context, Status)
    assert buffer.getvalue() == ""


def test_phase_prints_left_rule(monkeypatch: pytest.MonkeyPatch) -> None:
    console, buffer = _console(terminal=False)
    monkeypatch.setattr(ui, "console", console)
    ui.phase("Formalize")
    output = buffer.getvalue()
    assert "Formalize" in output
    assert "─" in output


def test_line_helpers(monkeypatch: pytest.MonkeyPatch) -> None:
    console, buffer = _console(terminal=False)
    monkeypatch.setattr(ui, "console", console)
    ui.ok("proof accepted")
    ui.fail("build failed")
    ui.warn("cache miss")
    ui.kv("Model", "auto")
    lines = buffer.getvalue().splitlines()
    assert lines[0] == f"  {ui.GLYPH_OK} proof accepted"
    assert lines[1] == f"  {ui.GLYPH_FAIL} build failed"
    assert lines[2] == "  ! cache miss"
    assert lines[3].startswith("  Model")
    assert lines[3].endswith(" auto")


class TestCommandName:
    def test_an_installed_console_script_names_itself(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """A pip or Homebrew install runs `autolean`, not `uv run autolean`."""
        monkeypatch.setattr(ui.sys, "argv", ["/opt/homebrew/bin/autolean", "solve"])

        assert ui.command() == "autolean"

    def test_the_module_form_names_the_module(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ui.sys, "argv", ["/src/autolean/__main__.py"])

        assert ui.command() == "python -m autolean"

    def test_a_missing_argv_still_names_something_runnable(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(ui.sys, "argv", [])

        assert ui.command() == "python -m autolean"
