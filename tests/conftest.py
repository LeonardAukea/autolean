"""Shared fixtures for the AutoLean test suite."""

from __future__ import annotations

from pathlib import Path

import pytest


@pytest.fixture()
def lean_file_no_sorry(tmp_path: Path) -> Path:
    """A minimal Lean file with no sorry."""
    p = tmp_path / "Clean.lean"
    p.write_text(
        "theorem add_comm (a b : Nat) : a + b = b + a := by\n  omega\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture()
def lean_file_one_sorry(tmp_path: Path) -> Path:
    """A Lean file with exactly one sorry in tactic mode."""
    p = tmp_path / "One.lean"
    p.write_text(
        "theorem foo : 1 + 1 = 2 := by\n  sorry\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture()
def lean_file_term_sorry(tmp_path: Path) -> Path:
    """A Lean file with a sorry in term mode."""
    p = tmp_path / "Term.lean"
    p.write_text(
        "def bar : Nat := sorry\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture()
def lean_file_sorry_in_comment(tmp_path: Path) -> Path:
    """A Lean file where sorry only appears inside a comment."""
    p = tmp_path / "Commented.lean"
    p.write_text(
        "-- sorry this is just a comment\ntheorem clean : True := by\n  trivial\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture()
def lean_file_sorry_in_string(tmp_path: Path) -> Path:
    """A Lean file where sorry only appears inside a string literal."""
    p = tmp_path / "Stringy.lean"
    p.write_text(
        'def msg : String := "sorry not sorry"\n',
        encoding="utf-8",
    )
    return p


@pytest.fixture()
def lean_file_multi_sorry(tmp_path: Path) -> Path:
    """A Lean file with multiple sorries across declarations."""
    p = tmp_path / "Multi.lean"
    p.write_text(
        "theorem t1 : 1 = 1 := by\n"
        "  sorry\n"
        "\n"
        "theorem t2 : 2 = 2 := by\n"
        "  sorry\n"
        "\n"
        "theorem t3 : 3 = 3 := by\n"
        "  sorry\n",
        encoding="utf-8",
    )
    return p


@pytest.fixture()
def program_md(tmp_path: Path) -> Path:
    """A valid program.md fixture."""
    p = tmp_path / "program.md"
    p.write_text(
        "# AutoLean Program\n"
        "\n"
        "> Preamble text.\n"
        "\n"
        "## Mode\n"
        "\n"
        "<!-- comment -->\n"
        "sorry-elimination\n"
        "\n"
        "## Lean Project Path\n"
        "\n"
        "<!-- comment -->\n"
        "workspace\n"
        "\n"
        "## Goals\n"
        "\n"
        "<!-- comment -->\n"
        "\n"
        "1. Fill all sorries.\n"
        "2. Keep proofs short.\n"
        "\n"
        "## Constraints\n"
        "\n"
        "<!-- comment -->\n"
        "\n"
        "1. Do NOT modify imports.\n"
        "2. Do NOT change statements.\n"
        "\n"
        "## Strategy Hints\n"
        "\n"
        "<!-- comment -->\n"
        "\n"
        "- Try simp first.\n"
        "- Try omega for nats.\n"
        "\n"
        "## LLM Configuration\n"
        "\n"
        "model: gemma4:26b\n"
        "temperature: 0.4\n"
        "max_retries_per_sorry: 5\n"
        "cycle_timeout_seconds: 120\n"
        "\n"
        "## Experiment Budget\n"
        "\n"
        "max_cycles: 0\n",
        encoding="utf-8",
    )
    return p
