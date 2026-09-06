from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.record_paper_demo import _assert_compiles, _assert_export, recording_environment


def _write_export(root: Path) -> Path:
    export = root / "paper-artifact"
    project = export / "project"
    paper = project / "AutoLean" / "Paper_arxiv_2506_18616v5.lean"
    paper.parent.mkdir(parents=True)
    (project / ".lake").mkdir()
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


def test_demo_export_ignores_the_lean_build_cache(tmp_path: Path) -> None:
    project = _write_export(tmp_path)
    dependency = project / ".lake" / "packages" / "mathlib" / "Mathlib" / "Cache.lean"
    dependency.parent.mkdir(parents=True)
    dependency.write_text("theorem cached : True := by trivial\n", encoding="utf-8")

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


def test_paper_source_is_compiled_in_its_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _write_export(tmp_path)
    source = project / "AutoLean" / "Paper_arxiv_2506_18616v5.lean"
    observed: list[tuple[list[str], dict[str, object]]] = []

    def run(command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        observed.append((command, options))
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("scripts.record_paper_demo.subprocess.run", run)

    _assert_compiles(tmp_path)

    assert [command for command, _options in observed] == [
        [
            "lake",
            "env",
            "lean",
            str(source.relative_to(project)),
        ],
    ]
    assert observed[0][1]["cwd"] == project
    assert observed[0][1]["timeout"] == 300


def test_paper_export_receives_an_independent_dependency_cache(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    project = _write_export(tmp_path)
    (project / ".lake").rmdir()
    observed: list[tuple[Path, Path]] = []

    def copy(source: Path, destination: Path) -> None:
        observed.append((source, destination))

    def run(command: list[str], **_options: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("scripts.record_paper_demo.copy_workspace", copy)
    monkeypatch.setattr("scripts.record_paper_demo.subprocess.run", run)

    _assert_compiles(tmp_path)
    assert observed == [(tmp_path / "workspace" / ".lake", project / ".lake")]


def test_paper_lean_diagnostics_are_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_export(tmp_path)

    def run(command: list[str], **_options: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "unknown declaration")

    monkeypatch.setattr("scripts.record_paper_demo.subprocess.run", run)

    with pytest.raises(SystemExit, match="unknown declaration"):
        _assert_compiles(tmp_path)
