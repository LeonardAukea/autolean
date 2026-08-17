"""Portable exports bind a clean Lean project to companion paper source."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from autolean.export import (
    ExportError,
    PaperBundle,
    _latex_escape,
    _latex_identifier,
    _plan_record,
    export_project,
    paper_bundle_from_artifacts,
)
from autolean.strategy import ProofPlan, parse_proof_plan


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


def _write_plan(
    path: Path,
    *,
    source_sha256: str,
    text_sha256: str,
    pdf_sha256: str,
) -> tuple[ProofPlan, str]:
    payload: dict[str, object] = {
        "objective": "Audit every reviewed mapping.",
        "formalization": [],
        "observations": [],
        "invariants": [],
        "obstructions": [],
        "reductions": [],
        "premises": [],
        "methods": ["Compile closed aliases."],
        "partial_results": [],
        "risks": ["Mapping fidelity requires later review."],
        "completion_criteria": ["All aliases elaborate."],
        "checkpoints": ["Build the evidence module."],
        "revision_triggers": [],
    }
    plan = parse_proof_plan(json.dumps(payload))
    raw_response = json.dumps(payload, indent=2)
    response = {
        "attempt": 1,
        "duration_seconds": 2.5,
        "guidance": [],
        "input_tokens": 100,
        "model": "opus",
        "output_tokens": 200,
        "response": raw_response,
        "response_sha256": hashlib.sha256(raw_response.encode()).hexdigest(),
        "validation_error": "",
    }
    responses = [response]
    trace_payload = json.dumps(
        responses,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    )
    trace_sha256 = hashlib.sha256(trace_payload.encode()).hexdigest()
    path.write_text(
        json.dumps(
            {
                "accepted_response_model": "opus",
                "accepted_response_sha256": response["response_sha256"],
                "backend": "claude_cli",
                "model": "opus",
                "pdf_sha256": pdf_sha256,
                "plan": plan.as_dict(),
                "plan_sha256": plan.sha256,
                "responses": responses,
                "schema": "autolean.paper-plan.v2",
                "source_sha256": source_sha256,
                "text_sha256": text_sha256,
                "trace_sha256": trace_sha256,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    return plan, trace_sha256


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


def test_session_export_contains_only_the_target_import_closure(tmp_path: Path) -> None:
    root = _project(tmp_path)
    helper = root / "AutoLean" / "Helper.lean"
    helper.write_text("def helper : Nat := 1\n", encoding="utf-8")
    target = root / "AutoLean" / "Proof.lean"
    target.write_text(
        "import AutoLean.Helper\n\ntheorem proof : helper = 1 := by rfl\n",
        encoding="utf-8",
    )
    (root / "AutoLean" / "Secret.lean").write_text("def secret := 7\n", encoding="utf-8")
    (root / "AutoLean" / "UserTheorems.lean").write_text("def personal := 8\n", encoding="utf-8")
    generated = root / "AutoLean" / "Generated" / "Stale.lean"
    generated.parent.mkdir()
    generated.write_text("def stale := 9\n", encoding="utf-8")
    destination = tmp_path / "session-artifact"

    export_project(
        root,
        destination,
        title="Scoped proof",
        session={"id": "session-1", "target_file": "AutoLean/Proof.lean"},
    )

    project = destination / "project"
    assert (project / "AutoLean" / "Proof.lean").is_file()
    assert (project / "AutoLean" / "Helper.lean").is_file()
    assert (project / "AutoLean.lean").read_text(encoding="utf-8") == "import AutoLean.Proof\n"
    assert not (project / "AutoLean" / "Secret.lean").exists()
    assert not (project / "AutoLean" / "UserTheorems.lean").exists()
    assert not (project / "AutoLean" / "Generated" / "Stale.lean").exists()


def test_paper_export_preserves_inputs_and_renders_item_coverage(tmp_path: Path) -> None:
    root = _project(tmp_path)
    source = root / "AutoLean" / "Papers" / "paper.md"
    source.parent.mkdir()
    source.write_text("# Ionescu-Tulcea\n", encoding="utf-8")
    pdf = root / ".autolean" / "papers" / "paper.pdf"
    pdf.parent.mkdir(parents=True)
    pdf.write_bytes(b"%PDF exact")
    evidence = root / "AutoLean" / "PaperEvidence.lean"
    evidence.write_text(
        "import Mathlib\n\nnoncomputable abbrev paper_theorem_2_11 := @ProbabilityTheory.Kernel.traj\n",
        encoding="utf-8",
    )
    source_sha256 = "a" * 64
    text_sha256 = "d" * 64
    pdf_sha256 = hashlib.sha256(pdf.read_bytes()).hexdigest()
    plan = source.with_name("plan.json")
    parsed_plan, trace_sha256 = _write_plan(
        plan,
        source_sha256=source_sha256,
        text_sha256=text_sha256,
        pdf_sha256=pdf_sha256,
    )
    coverage = source.with_name("coverage.json")
    coverage.write_text(
        json.dumps(
            {
                "claims": [
                    {
                        "evidence_declarations": ["paper_theorem_2_11"],
                        "label": "Theorem 2.11",
                        "lean_declarations": ["ProbabilityTheory.Kernel.traj"],
                        "scope": "core",
                        "status": "elaborated",
                    }
                ],
                "elaborated_items": 1,
                "lean_evidence": {
                    "declaration_count": 1,
                    "error_count": 0,
                    "module": "AutoLean/PaperEvidence.lean",
                    "source_sha256": hashlib.sha256(evidence.read_bytes()).hexdigest(),
                    "success": True,
                },
                "pdf_sha256": pdf_sha256,
                "plan_artifact_sha256": hashlib.sha256(plan.read_bytes()).hexdigest(),
                "plan_sha256": parsed_plan.sha256,
                "plan_trace_sha256": trace_sha256,
                "profile": {
                    "arxiv_id": "2506.18616v5",
                    "source_archive_sha256": "b" * 64,
                    "title": "A Formalization of the Ionescu-Tulcea Theorem in Mathlib",
                },
                "schema": "autolean.paper-coverage.v2",
                "source_sha256": source_sha256,
                "text_sha256": text_sha256,
                "total_items": 1,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    bundle = paper_bundle_from_artifacts((coverage, pdf, source, plan))
    assert bundle == PaperBundle(source, coverage, pdf, plan)

    destination = tmp_path / "paper-artifact"
    export_project(
        root,
        destination,
        title="Ionescu-Tulcea audit",
        environment_sha256="c" * 64,
        paper_bundle=bundle,
    )

    assert (destination / "source" / "paper.md").read_bytes() == source.read_bytes()
    assert (destination / "source" / "paper.pdf").read_bytes() == pdf.read_bytes()
    assert (destination / "source" / "coverage.json").read_bytes() == coverage.read_bytes()
    assert (destination / "source" / "plan.json").read_bytes() == plan.read_bytes()
    latex = (destination / "paper" / "main.tex").read_text(encoding="utf-8")
    assert "2506.18616v5" in latex
    assert "Theorem 2.11" in latex
    assert _latex_identifier("ProbabilityTheory.Kernel.traj") in latex
    assert "Compile closed aliases" in latex
    manifest = json.loads((destination / "manifest.json").read_text(encoding="utf-8"))
    assert manifest["paper_profile"]["arxiv_id"] == "2506.18616v5"


def test_paper_plan_rejects_a_normalized_plan_that_differs_from_raw_response(
    tmp_path: Path,
) -> None:
    plan_path = tmp_path / "plan.json"
    _write_plan(
        plan_path,
        source_sha256="a" * 64,
        text_sha256="b" * 64,
        pdf_sha256="c" * 64,
    )
    record = json.loads(plan_path.read_text(encoding="utf-8"))
    record["plan"]["objective"] = "A different objective."
    plan_path.write_text(json.dumps(record) + "\n", encoding="utf-8")

    with pytest.raises(ExportError, match="differs from its accepted response"):
        _plan_record(plan_path)


def test_latex_escape_handles_control_characters_without_reprocessing() -> None:
    assert _latex_escape(r"A\B_{x}^2~") == (r"A\textbackslash{}B\_\{x\}\textasciicircum{}2\textasciitilde{}")


def test_latex_identifier_uses_unicode_monospace_and_breakpoints() -> None:
    assert _latex_identifier("Kernel.partialTraj_map₂") == (
        r"\texttt{Kernel.\allowbreak{}partialTraj\_\allowbreak{}map₂}"
    )


def test_a_whole_project_export_carries_the_generated_proofs(tmp_path: Path) -> None:
    """The proofs a run accepted are the artifact's reason to exist."""
    root = _project(tmp_path)
    generated = root / "AutoLean" / "Generated"
    generated.mkdir(parents=True)
    (generated / "Result.lean").write_text("theorem accepted : True := by\n  trivial\n", encoding="utf-8")
    (root / "AutoLean" / "UserTheorems.lean").write_text("-- scratch\n", encoding="utf-8")

    result = export_project(root, tmp_path / "artifact", title="Artifact")

    project = result.path / "project"
    assert (project / "AutoLean" / "Generated" / "Result.lean").is_file()
    assert not (project / "AutoLean" / "UserTheorems.lean").exists()
    assert "import AutoLean.Generated.Result" in (project / "AutoLean.lean").read_text(encoding="utf-8")


def test_a_nested_workspace_is_not_exported(tmp_path: Path) -> None:
    """A run leaves a nested copy under the project; it is not source."""
    root = _project(tmp_path)
    stale = root / "workspace" / "AutoLean"
    stale.mkdir(parents=True)
    (stale / "Old.lean").write_text("theorem stale : True := by\n  trivial\n", encoding="utf-8")

    result = export_project(root, tmp_path / "artifact", title="Artifact")

    assert not (result.path / "project" / "workspace").exists()


def test_a_generated_module_is_not_hidden_by_a_longer_sibling(tmp_path: Path) -> None:
    """`import A.Foo` occurs inside `import A.FooBar`; both must be imported."""
    root = _project(tmp_path)
    generated = root / "AutoLean" / "Generated"
    generated.mkdir(parents=True)
    for name in ("Pythagorean", "PythagoreanExtended"):
        (generated / f"{name}.lean").write_text(
            f"theorem {name.lower()} : True := by\n  trivial\n", encoding="utf-8"
        )
    # The longer module is already imported, so a substring test would report
    # the shorter one as present and never add it.
    (root / "AutoLean.lean").write_text(
        "import AutoLean.Proof\nimport AutoLean.Generated.PythagoreanExtended\n", encoding="utf-8"
    )

    result = export_project(root, tmp_path / "artifact", title="Artifact")

    library_root = (result.path / "project" / "AutoLean.lean").read_text(encoding="utf-8")
    imported = {line.strip() for line in library_root.splitlines()}
    assert "import AutoLean.Generated.Pythagorean" in imported
    assert "import AutoLean.Generated.PythagoreanExtended" in imported
