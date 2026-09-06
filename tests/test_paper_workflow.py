"""Paper orchestration preserves review evidence and owns model lifetime."""

from __future__ import annotations

import io
import json
from pathlib import Path

import click
import pytest
from rich.console import Console

from autolean.lean_interface import BuildResult, LeanProject
from autolean.llm import LLMConfig, LLMResponse
from autolean.llm.base import BaseBackend
from autolean.paper import Claim, PaperDocument
from autolean.paper_workflow import PaperServices, prepare_paper
from autolean.provenance import ProofEnvironment, sha256_text
from autolean.strategy import ProofPlan, generate_proof_plan


class ScriptedPaperModel(BaseBackend):
    def __init__(self) -> None:
        super().__init__(LLMConfig(model="gpt-6-astra", backend="codex_cli", effort="max"))
        self.responses = iter(
            (
                ProofPlan(objective="Check the claim.").to_json(),
                ProofPlan(objective="Check the exact stated claim.").to_json(),
                "theorem fixture : True := by\n  sorry",
            )
        )
        self.closed = False

    def ping(self) -> bool:
        return True

    def generate(self, _system: str, _user: str, **_options: object) -> LLMResponse:
        return LLMResponse(text=next(self.responses), model=self.config.model)

    def close(self) -> None:
        assert not self.closed
        self.closed = True


@pytest.mark.parametrize("reject_source", [False, True])
def test_review_formalization_and_acceptance_share_one_owned_model(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, reject_source: bool
) -> None:
    program = tmp_path / "program.md"
    program.write_text("## Lean Project Path\nworkspace\n")
    project = tmp_path / "workspace"
    project.mkdir()
    (project / "lakefile.lean").write_text("import Lake\n")
    document = PaperDocument(
        title="Fixture",
        text="Theorem 1. True holds.",
        claims=[Claim(label="Theorem 1", statement="True holds.", kind="theorem")],
        input_ref="fixture",
        input_sha256="a" * 64,
    )
    monkeypatch.setattr("autolean.paper.read_paper", lambda *_args, **_options: document)
    environment = ProofEnvironment("b" * 64, "Lean fixture", "leanprover/lean4:fixture", "c" * 64, 1, ())
    monkeypatch.setattr(LeanProject, "proof_environment", lambda *_args, **_options: environment)
    decisions = iter((False, True))
    monkeypatch.setattr(click, "confirm", lambda *_args, **_options: next(decisions))
    monkeypatch.setattr(click, "prompt", lambda *_args, **_options: "Preserve the exact statement.")
    model = ScriptedPaperModel()
    connections: list[object] = []
    accepted: list[Path] = []

    def connect(*_args: object, **_options: object) -> ScriptedPaperModel:
        connections.append(model)
        return model

    def plan(
        statement: str, llm: ScriptedPaperModel, guidance: tuple[str, ...], **options: object
    ) -> ProofPlan:
        return generate_proof_plan(statement, llm.generate, guidance=guidance, **options)

    def accept(root: Path, output: Path, content: str, **_options: object) -> tuple[Path, BuildResult]:
        assert output.is_relative_to(root)
        assert "theorem fixture : True" in content
        if reject_source:
            raise click.ClickException("Lean rejected the candidate")
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(content)
        accepted.append(output)
        return output, BuildResult(success=True)

    services = PaperServices(Console(file=io.StringIO()), connect, plan, lambda _plan: None, accept)
    arguments = dict(
        source="fixture",
        pages=None,
        pdf_engine="hybrid",
        paddleocr_url=None,
        extract_only=False,
        output=None,
        model=model.config.model,
        backend=model.config.backend,
        guide=(),
        review_plan=True,
        program=program,
        services=services,
    )
    if reject_source:
        with pytest.raises(click.ClickException, match="Lean rejected"):
            prepare_paper(**arguments)
        assert not accepted
        assert not list((tmp_path / "workspace").rglob("*_coverage_*.json"))
    else:
        prepared, _config = prepare_paper(**arguments)
        assert prepared is not None
        assert prepared.title == document.title
        assert prepared.effort == "max"
        assert accepted == [prepared.lean_path]
        assert prepared.coverage_path is not None
        coverage = json.loads(prepared.coverage_path.read_text())
        assert coverage["lean_evidence"]["source_sha256"] == sha256_text(prepared.lean_path.read_text())
        assert coverage["lean_evidence"]["success"] is True
        assert coverage["elaborated_items"] == 0
    assert connections == [model]
    assert model.closed
    plan_path = next((tmp_path / "workspace").rglob("*_plan_*.json"))
    trace = json.loads(plan_path.read_text())
    assert [response["guidance"] for response in trace["responses"]] == [
        [],
        ["Preserve the exact statement."],
    ]
    assert trace["plan"]["objective"] == "Check the exact stated claim."
