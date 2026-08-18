from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from scripts.record_paper_demo import _clear_runtime_state, recording_environment
from scripts.record_prove_demo import _assert_compiles, _assert_export, _assert_generated


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


def test_generated_source_is_compiled_in_its_project(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    source = _write_generated(
        tmp_path,
        "import Mathlib\n\ntheorem pythagorean : True := by\n  trivial\n",
    )
    observed: dict[str, object] = {}

    def run(command: list[str], **options: object) -> subprocess.CompletedProcess[str]:
        observed["command"] = command
        observed.update(options)
        return subprocess.CompletedProcess(command, 0, "", "")

    monkeypatch.setattr("scripts.record_prove_demo.subprocess.run", run)

    _assert_compiles(tmp_path)

    assert observed["command"] == [
        "lake",
        "env",
        "lean",
        str(source.relative_to(tmp_path / "workspace")),
    ]
    assert observed["cwd"] == tmp_path / "workspace"
    assert observed["timeout"] == 300


def test_lean_diagnostics_are_reported(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _write_generated(
        tmp_path,
        "import Mathlib\n\ntheorem pythagorean : True := by\n  trivial\n",
    )

    def run(command: list[str], **_options: object) -> subprocess.CompletedProcess[str]:
        return subprocess.CompletedProcess(command, 1, "", "unknown identifier")

    monkeypatch.setattr("scripts.record_prove_demo.subprocess.run", run)

    with pytest.raises(SystemExit, match="unknown identifier"):
        _assert_compiles(tmp_path)


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


def test_a_term_mode_placeholder_is_rejected(tmp_path: Path) -> None:
    _write_generated(
        tmp_path,
        "import Mathlib\n\ndef helper : Nat := sorry\n\ntheorem pythagorean : True := by\n  trivial\n",
    )

    with pytest.raises(SystemExit, match="proof placeholder"):
        _assert_generated(tmp_path)


def test_a_placeholder_after_a_tactic_is_rejected(tmp_path: Path) -> None:
    _write_generated(
        tmp_path,
        "import Mathlib\n\ntheorem pythagorean : True := by\n  simp; sorry\n",
    )

    with pytest.raises(SystemExit, match="proof placeholder"):
        _assert_generated(tmp_path)


def test_the_word_sorry_in_a_comment_is_not_a_placeholder(tmp_path: Path) -> None:
    _write_generated(
        tmp_path,
        "import Mathlib\n\n-- sorry, this needed a lemma\ntheorem pythagorean : True := by\n  trivial\n",
    )

    assert _assert_generated(tmp_path).name == "PythagoreanTheorem.lean"


def test_the_recording_environment_keeps_terminal_color(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("NO_COLOR", "1")
    monkeypatch.setenv("CLICOLOR", "0")

    environment = recording_environment(AUTOLEAN_DEMO_ROOT="/demo")

    assert "NO_COLOR" not in environment
    assert "CLICOLOR" not in environment
    assert environment["AUTOLEAN_DEMO_ROOT"] == "/demo"


def test_demo_workspace_removes_generated_lean_sources(tmp_path: Path) -> None:
    autolean = tmp_path / "AutoLean"
    autolean.mkdir()
    curated = autolean / "Curated.lean"
    curated.write_text("theorem curated : True := by trivial\n", encoding="utf-8")
    generated = [
        autolean / "PaperTopic.lean",
        autolean / "Paper_arxiv_1.lean",
        autolean / "Challenge_Test.lean",
        autolean / "LibTopic.lean",
        autolean / "UserTheorems.lean",
    ]
    for path in generated:
        path.write_text("theorem generated : True := by trivial\n", encoding="utf-8")

    _clear_runtime_state(tmp_path)

    assert curated.is_file()
    assert all(not path.exists() for path in generated)
