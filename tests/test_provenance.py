"""Content-identity tests for installed Lean proof environments."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autolean import provenance
from autolean.provenance import ProofEnvironmentError, capture_proof_environment


def _environment(root: Path, *, revision: str = "a" * 40) -> tuple[Path, Path]:
    project = root / "project"
    lean = root / "toolchain" / "bin" / "lean"
    lean.parent.mkdir(parents=True)
    lean.write_bytes(b"lean-kernel")
    core = lean.parent.parent / "lib" / "lean"
    core.mkdir(parents=True)
    (core / "Init.olean").write_bytes(b"core-olean")

    project.mkdir()
    (project / "lean-toolchain").write_text("leanprover/lean4:v4.33.0\n")
    (project / "lakefile.lean").write_text("import Lake\n")
    (project / "lake-manifest.json").write_text(
        json.dumps(
            {
                "packages": [
                    {
                        "name": "mathlib",
                        "scope": "leanprover-community",
                        "rev": revision,
                    }
                ]
            },
            sort_keys=True,
        )
    )
    modules = project / ".lake" / "packages" / "mathlib" / ".lake" / "build" / "lib" / "lean"
    modules.mkdir(parents=True)
    (modules / "Mathlib.olean").write_bytes(b"mathlib-olean")
    return project, lean


@pytest.fixture(autouse=True)
def _stable_lean_version(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        provenance,
        "_read_lean_version",
        lambda lean: "Lean (version 4.33.0)",
    )


def test_identical_closures_have_the_same_identity_across_roots(tmp_path: Path) -> None:
    first_project, first_lean = _environment(tmp_path / "first")
    second_project, second_lean = _environment(tmp_path / "second")

    first = capture_proof_environment(first_project, first_lean)
    second = capture_proof_environment(second_project, second_lean)

    assert first.sha256 == second.sha256
    assert first.artifact_count == 3
    assert first.dependencies == (f"leanprover-community/mathlib@{'a' * 40}",)


def test_compiled_artifact_change_changes_environment_identity(tmp_path: Path) -> None:
    project, lean = _environment(tmp_path)
    before = capture_proof_environment(project, lean)
    artifact = (
        project / ".lake" / "packages" / "mathlib" / ".lake" / "build" / "lib" / "lean" / "Mathlib.olean"
    )

    artifact.write_bytes(b"different-olean")
    after = capture_proof_environment(project, lean)

    assert before.sha256 != after.sha256


def test_project_configuration_change_changes_environment_identity(tmp_path: Path) -> None:
    project, lean = _environment(tmp_path)
    before = capture_proof_environment(project, lean)

    (project / "lakefile.lean").write_text("import Lake\n-- changed\n")
    after = capture_proof_environment(project, lean)

    assert before.sha256 != after.sha256


def test_manifest_requires_content_revisions(tmp_path: Path) -> None:
    project, lean = _environment(tmp_path, revision="main")

    with pytest.raises(ProofEnvironmentError, match="non-content revision"):
        capture_proof_environment(project, lean)


def test_manifest_is_required(tmp_path: Path) -> None:
    project, lean = _environment(tmp_path)
    (project / "lake-manifest.json").unlink()

    with pytest.raises(ProofEnvironmentError, match=r"lake-manifest\.json is required"):
        capture_proof_environment(project, lean)


def test_fingerprint_is_stable_for_an_unchanged_tree(tmp_path: Path) -> None:
    project, lean = _environment(tmp_path)

    first = provenance.environment_fingerprint(project, lean)
    second = provenance.environment_fingerprint(project, lean)

    assert first == second


def test_fingerprint_tracks_artifact_and_configuration_changes(tmp_path: Path) -> None:
    project, lean = _environment(tmp_path)
    artifact = (
        project / ".lake" / "packages" / "mathlib" / ".lake" / "build" / "lib" / "lean" / "Mathlib.olean"
    )
    before = provenance.environment_fingerprint(project, lean)

    artifact.write_bytes(b"different-olean-content")
    after_artifact = provenance.environment_fingerprint(project, lean)
    (project / "lakefile.lean").write_text("import Lake\n-- changed\n")
    after_config = provenance.environment_fingerprint(project, lean)

    assert before != after_artifact
    assert after_artifact != after_config
