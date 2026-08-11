"""Portable exports bind a clean Lean project to companion paper source."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from autolean.export import ExportError, export_project


def _project(tmp_path: Path) -> Path:
    root = tmp_path / "workspace"
    source = root / "AutoLean" / "Proof.lean"
    source.parent.mkdir(parents=True)
    source.write_text(
        "theorem proof (P : Prop) (h : P) : P ∧ True := by\n  exact ⟨h, trivial⟩\n", encoding="utf-8"
    )
    (root / "lakefile.lean").write_text("import Lake\nopen Lake DSL\n", encoding="utf-8")
    (root / "lean-toolchain").write_text("leanprover/lean4:v4.33.0\n", encoding="utf-8")
    (root / "lake-manifest.json").write_text("{}\n", encoding="utf-8")
    (root / ".lake").mkdir()
    (root / ".lake" / "private.bin").write_bytes(b"private")
    (root / "training_data").mkdir()
    (root / "training_data" / "examples.jsonl").write_text("secret\n", encoding="utf-8")
    return root


def test_export_contains_exact_sources_manifest_and_latex(tmp_path: Path) -> None:
    root = _project(tmp_path)
    destination = tmp_path / "artifact"

    result = export_project(
        root,
        destination,
        title="A proof_1",
        environment_sha256="a" * 64,
        session={"id": "session-1", "model": "opus"},
    )

    proof = destination / "project" / "AutoLean" / "Proof.lean"
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    proof_record = next(item for item in manifest["files"] if item["path"].endswith("Proof.lean"))
    assert proof_record["sha256"] == hashlib.sha256(proof.read_bytes()).hexdigest()
    assert not (destination / "project" / ".lake").exists()
    assert not (destination / "project" / "training_data").exists()
    assert (destination / "session.json").is_file()
    paper = (destination / "paper" / "main.tex").read_text(encoding="utf-8")
    readme = (destination / "README.md").read_text(encoding="utf-8")
    assert "\\VerbatimInput" in paper
    assert "\\usepackage{fontspec}" in paper
    assert "\\usepackage{ucharclasses}" in paper
    assert "DejaVuSansMono.ttf" in paper
    assert "\\setTransitionsForMathematics" in paper
    assert "AutoLean/Proof.lean" in paper
    assert "lakefile.lean" not in paper
    assert "latexmk -xelatex main.tex" in readme
    assert result.source_count == 1
    assert len(result.manifest_sha256) == 64


def test_export_refuses_to_replace_an_existing_destination(tmp_path: Path) -> None:
    root = _project(tmp_path)
    destination = tmp_path / "artifact"
    destination.mkdir()

    with pytest.raises(ExportError, match="already exists"):
        export_project(root, destination, title="Proof")


def test_export_refuses_source_symlinks(tmp_path: Path) -> None:
    root = _project(tmp_path)
    (root / "Linked.lean").symlink_to(root / "AutoLean" / "Proof.lean")

    with pytest.raises(ExportError, match="symbolic links"):
        export_project(root, tmp_path / "artifact", title="Proof")
