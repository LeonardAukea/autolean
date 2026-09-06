"""Focused source-selection invariants for supplementary CLI workflows."""

from pathlib import Path

import click
import pytest

from autolean.cli_workflows import _proof_slice


@pytest.mark.parametrize("newline", ["\n", "\r\n"])
def test_proof_slice_matches_the_declared_name(tmp_path: Path, newline: str) -> None:
    source = tmp_path / "Target.lean"
    source.write_text(
        """\
theorem other (target : Prop) (proof : target) : target := by
  exact proof

/- theorem target : False := by
  contradiction -/
theorem target : True := by
  trivial

theorem after : True := by
  trivial
""",
        encoding="utf-8",
        newline=newline,
    )

    selected = _proof_slice(source, "target")

    assert selected.source.encode("utf-8") == source.read_bytes()
    assert selected.lines[selected.theorem_line].startswith("theorem target ")
    assert selected.proof.rstrip().splitlines() == ["  trivial"]
    assert selected.lines[selected.proof_end].startswith("theorem after ")


def test_proof_slice_rejects_an_inline_tactic_proof(tmp_path: Path) -> None:
    source = tmp_path / "Target.lean"
    source.write_text("theorem target : True := by trivial\n", encoding="utf-8")

    with pytest.raises(click.ClickException, match="No multiline tactic proof"):
        _proof_slice(source, "target")


def test_proof_slice_rejects_a_missing_tactic_body(tmp_path: Path) -> None:
    source = tmp_path / "Target.lean"
    source.write_text(
        "theorem target : True := by\ntheorem after : True := by\n  trivial\n",
        encoding="utf-8",
    )

    with pytest.raises(click.ClickException, match="No multiline tactic proof"):
        _proof_slice(source, "target")


def test_proof_slice_rejects_an_unknown_name(tmp_path: Path) -> None:
    source = tmp_path / "Target.lean"
    source.write_text("theorem present : True := by\n  trivial\n", encoding="utf-8")

    with pytest.raises(click.ClickException, match="No theorem named 'missing'"):
        _proof_slice(source, "missing")
