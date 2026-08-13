from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.record_prove_demo import _assert_export, _assert_generated


def _write_generated(root: Path, body: str) -> Path:
    generated = root / "workspace" / "AutoLean" / "Generated"
    generated.mkdir(parents=True)
    source = generated / "PythagoreanTheorem.lean"
    source.write_text(body, encoding="utf-8")
    return source


def _write_export(root: Path) -> Path:
    export = root / "pythagorean-artifact"
    project = export / "project"
    source = project / "AutoLean" / "Generated" / "PythagoreanTheorem.lean"
    source.parent.mkdir(parents=True)
    (export / "manifest.json").write_text(
        json.dumps({"schema": "autolean.project-export.v1"}),
        encoding="utf-8",
    )
    (project / "lakefile.lean").write_text("import Lake\n", encoding="utf-8")
    source.write_text(
        "import Mathlib\n\ntheorem pythagorean : True := by\n  trivial\n",
        encoding="utf-8",
    )
    return source


def test_generated_source_with_a_proof_is_accepted(tmp_path: Path) -> None:
    _write_generated(
        tmp_path,
        "import Mathlib\n\ntheorem pythagorean : True := by\n  trivial\n",
    )

    assert _assert_generated(tmp_path).name == "PythagoreanTheorem.lean"


def test_generated_source_with_a_placeholder_is_rejected(tmp_path: Path) -> None:
    _write_generated(
        tmp_path,
        "import Mathlib\n\ntheorem pythagorean : True := by\n  sorry\n",
    )

    with pytest.raises(SystemExit, match="proof placeholder"):
        _assert_generated(tmp_path)


def test_export_with_one_generated_source_is_accepted(tmp_path: Path) -> None:
    _write_export(tmp_path)

    _assert_export(tmp_path)


def test_export_with_a_placeholder_is_rejected(tmp_path: Path) -> None:
    source = _write_export(tmp_path)
    source.write_text(
        "import Mathlib\n\ntheorem pythagorean : True := by\n  sorry\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="proof placeholder"):
        _assert_export(tmp_path)
