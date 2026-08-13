from __future__ import annotations

import json
from pathlib import Path

import pytest

from scripts.record_paper_demo import _assert_export, recording_environment


def _write_export(root: Path) -> Path:
    export = root / "paper-artifact"
    project = export / "project"
    paper = project / "AutoLean" / "Paper_arxiv_2506_18616v5.lean"
    paper.parent.mkdir(parents=True)
    (export / "manifest.json").write_text(
        json.dumps({"schema": "autolean.project-export.v1"}),
        encoding="utf-8",
    )
    (project / "lakefile.lean").write_text("import Lake\n", encoding="utf-8")
    (project / "AutoLean.lean").write_text(
        "import AutoLean.Paper_arxiv_2506_18616v5\n",
        encoding="utf-8",
    )
    paper.write_text(
        "import Mathlib.Probability.ProductMeasure\n\ntheorem paper_evidence : True := by\n  trivial\n",
        encoding="utf-8",
    )
    return project


def test_demo_export_counts_lakefile_as_project_configuration(tmp_path: Path) -> None:
    _write_export(tmp_path)

    _assert_export(tmp_path)


def test_demo_export_rejects_an_unrelated_lean_source(tmp_path: Path) -> None:
    project = _write_export(tmp_path)
    (project / "AutoLean" / "UserTheorems.lean").write_text(
        "theorem private_result : True := by\n  trivial\n",
        encoding="utf-8",
    )

    with pytest.raises(SystemExit, match="unexpected Lean source set"):
        _assert_export(tmp_path)


def test_recording_environment_keeps_terminal_color(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("CLICOLOR", "0")
    monkeypatch.setenv("CLICOLOR_FORCE", "0")
    environment = recording_environment(
        AUTOLEAN_DEMO_ROOT=str(tmp_path),
        AUTOLEAN_DEMO_PDF=str(tmp_path / "paper.pdf"),
    )

    assert "NO_COLOR" not in environment
    assert "CLICOLOR" not in environment
    assert "CLICOLOR_FORCE" not in environment
    assert environment["AUTOLEAN_DEMO_ROOT"] == str(tmp_path)
    assert environment["AUTOLEAN_DEMO_PDF"] == str(tmp_path / "paper.pdf")
