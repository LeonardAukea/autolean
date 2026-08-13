"""Whole-agent invariants that span storage, providers, and Lean."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autolean.agent import AutoLeanAgent
from autolean.lean_interface import BuildResult, Diagnostic
from autolean.llm import (
    BaseBackend,
    LLMConfig,
    LLMRateLimitError,
    LLMResponse,
)
from autolean.provenance import ProofEnvironment, sha256_text
from autolean.routing import EscalationPolicy
from autolean.scanner import SorryTarget
from autolean.tracker import Outcome


class FakeBackend(BaseBackend):
    calls: int = 0
    planning_calls: int = 0
    proof_calls: int = 0
    error: Exception | None = None
    last_user: str = ""
    text: str = "trivial"

    def ping(self) -> bool:
        return True

    def generate(
        self,
        system: str,
        user: str,
        *,
        temperature: float | None = None,
        stop: list[str] | None = None,
    ) -> LLMResponse:
        del temperature, stop
        self.calls += 1
        self.last_user = user
        if self.error is not None:
            raise self.error
        if "mathematical research planner" in system:
            self.planning_calls += 1
            return LLMResponse(
                text=json.dumps(_strategy_payload()),
                model=self.config.model,
                input_tokens=60,
                output_tokens=120,
            )
        self.proof_calls += 1
        return LLMResponse(
            text=self.text,
            model=self.config.model,
            input_tokens=42,
            output_tokens=1,
        )


def _strategy_payload() -> dict[str, object]:
    return {
        "objective": "Close the exact Lean goal without changing its statement.",
        "formalization": ["Work in the declaration's current local context."],
        "observations": ["The goal is propositionally true."],
        "invariants": ["Preserve the declaration statement."],
        "obstructions": ["Reject terms that do not inhabit the exact goal."],
        "reductions": ["Construct a term of the target proposition."],
        "premises": ["Use declarations present in the pinned environment."],
        "methods": ["Try the smallest constructor proof first."],
        "partial_results": [],
        "risks": ["The extracted goal may omit relevant local context."],
        "completion_criteria": ["Lean accepts the declaration without placeholders."],
        "checkpoints": ["Elaborate the candidate in the sandbox."],
        "revision_triggers": ["A kernel diagnostic contradicts the proposed method."],
    }


def _project(tmp_path: Path) -> tuple[Path, Path, SorryTarget]:
    workspace = tmp_path / "workspace"
    source = workspace / "AutoLean" / "Target.lean"
    source.parent.mkdir(parents=True)
    (workspace / "lakefile.lean").write_text("import Lake\nopen Lake DSL\npackage test\n")
    source.write_text("theorem target : True := by\n  sorry\n")
    program = tmp_path / "program.md"
    program.write_text(
        "## Mode\n\nsorry-elimination\n\n"
        "## Lean Project Path\n\nworkspace\n\n"
        "## LLM Configuration\nmodel: gemma4:26b\n"
        "max_retries_per_sorry: 1\nmax_cycles: 1\n"
    )
    target = SorryTarget(
        file=source,
        line=2,
        col=2,
        decl_name="target",
        decl_line=1,
        context_before="theorem target : True := by",
        context_after="",
        rel_path="AutoLean/Target.lean",
        qualified_decl_name="target",
    )
    return program, source, target


def _snapshot(root: Path) -> dict[str, bytes]:
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def _prepare_agent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> tuple[AutoLeanAgent, FakeBackend]:
    program, source, target = _project(tmp_path)
    backend = FakeBackend(LLMConfig(model="test", backend="ollama"))
    monkeypatch.setattr("autolean.agent.create_llm_client", lambda config: backend)
    monkeypatch.setattr("autolean.agent.scan_project", lambda root: [target])
    monkeypatch.setattr("autolean.agent.prioritize_targets", lambda targets: targets)
    monkeypatch.setattr("autolean.search.search_relevant_lemmas", lambda goal, name: [])
    agent = AutoLeanAgent(program, dry_run=True)
    monkeypatch.setattr(
        agent.project,
        "check_file",
        lambda *args, **kwargs: BuildResult(success=True),
    )
    monkeypatch.setattr(
        agent.project,
        "get_goal_via_hole_punch",
        lambda *args, **kwargs: "⊢ True",
    )
    monkeypatch.setattr(
        agent.project,
        "proof_environment",
        lambda **kwargs: ProofEnvironment(
            sha256="a" * 64,
            lean_version="Lean 4.33.0",
            lean_toolchain="leanprover/lean4:v4.33.0",
            manifest_sha256="b" * 64,
            artifact_count=1,
            dependencies=(),
        ),
    )
    monkeypatch.setattr(
        agent.project,
        "validate_candidate",
        lambda *args, **kwargs: BuildResult(
            success=True,
            duration_seconds=0.1,
            axioms=(),
        ),
    )
    assert source.exists()
    return agent, backend


def test_dry_run_preserves_the_complete_project_tree(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, _ = _prepare_agent(tmp_path, monkeypatch)
    before = _snapshot(tmp_path)
    result = agent.run()
    assert result.successful
    assert _snapshot(tmp_path) == before
    assert agent.tracker.cycle == 1
    assert agent.tracker.records[-1].outcome == Outcome.VALIDATED
    assert agent.tracker.records[-1].environment_sha256 == "a" * 64


def test_cycle_budget_is_fresh_when_an_experiment_resumes(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, backend = _prepare_agent(tmp_path, monkeypatch)
    agent.tracker._cycle = 10

    result = agent.run()

    assert result.successful
    assert backend.calls == 2
    assert backend.planning_calls == 1
    assert backend.proof_calls == 1
    assert agent.tracker.cycle == 11


def test_terminal_provider_error_stops_after_one_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, backend = _prepare_agent(tmp_path, monkeypatch)
    backend.error = LLMRateLimitError("weekly quota exhausted")
    agent.config.max_cycles = 0
    result = agent.run()
    assert not result.successful
    assert "weekly quota exhausted" in result.message
    assert backend.calls == 1


def test_dry_run_rechecks_a_prefix_after_redundant_tail(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, backend = _prepare_agent(tmp_path, monkeypatch)
    backend.text = "trivial\nexact True.intro"
    candidates: list[str] = []

    def validate_candidate(_path: Path, content: str, **_kwargs: object) -> BuildResult:
        candidates.append(content)
        if len(candidates) == 1:
            return BuildResult(
                success=False,
                diagnostics=[
                    Diagnostic(
                        file="AutoLean/Target.lean",
                        line=3,
                        col=2,
                        severity="error",
                        message="No goals to be solved",
                    )
                ],
            )
        return BuildResult(success=True, duration_seconds=0.1, axioms=())

    monkeypatch.setattr(agent.project, "validate_candidate", validate_candidate)
    result = agent.run()

    assert result.successful
    assert len(candidates) == 2
    assert "exact True.intro" in candidates[0]
    assert "exact True.intro" not in candidates[1]
    record = agent.tracker.records[-1]
    assert record.outcome == Outcome.VALIDATED
    assert record.proof_sha256 == sha256_text("trivial")


def test_program_guidance_reaches_the_model_request(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, backend = _prepare_agent(tmp_path, monkeypatch)
    agent.config.goals = ["Expose the main mathematical step."]
    agent.config.constraints = ["Use only declarations already in scope."]
    result = agent.run()
    assert result.successful
    assert "## Program Goals" in backend.last_user
    assert "Expose the main mathematical step." in backend.last_user
    assert "## Program Constraints" in backend.last_user
    assert "Use only declarations already in scope." in backend.last_user


def test_auto_escalation_switches_once_without_expanding_the_attempt_budget(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, original_backend = _prepare_agent(tmp_path, monkeypatch)
    original_backend.close()
    initial = FakeBackend(LLMConfig(model="gpt-5.6-luna", backend="codex_cli"))
    initial.text = "exact False.elim (by contradiction)"
    stronger = FakeBackend(LLMConfig(model="gpt-5.6-terra", backend="codex_cli"))
    stronger.text = "trivial"
    agent.llm = initial
    agent.config.max_cycles = 2
    agent.config.max_retries_per_sorry = 2
    agent.config.escalation_policy = EscalationPolicy.AUTO
    agent.config.escalation_after_failures = 1

    created: list[LLMConfig] = []

    def create_backend(config: LLMConfig) -> FakeBackend:
        created.append(config)
        return stronger

    monkeypatch.setattr("autolean.agent.create_llm_client", create_backend)
    validations = 0

    def validate_candidate(*args: object, **kwargs: object) -> BuildResult:
        nonlocal validations
        del args, kwargs
        validations += 1
        if validations == 1:
            return BuildResult(
                success=False,
                diagnostics=[
                    Diagnostic(
                        file="AutoLean/Target.lean",
                        line=2,
                        col=2,
                        severity="error",
                        message="type mismatch: False has type Prop but True was expected",
                    )
                ],
            )
        return BuildResult(success=True, duration_seconds=0.1, axioms=())

    monkeypatch.setattr(agent.project, "validate_candidate", validate_candidate)

    result = agent.run()

    assert result.successful
    assert initial.calls == 2
    assert stronger.calls == 2
    assert initial.planning_calls == 1
    assert stronger.planning_calls == 1
    assert len(created) == 1
    assert created[0].model == "gpt-5.6-terra"
    assert agent.tracker.cycle == 2
    assert [record.model for record in agent.tracker.records] == [
        "gpt-5.6-luna",
        "gpt-5.6-terra",
    ]
    assert len(agent.model_transitions) == 1
    assert agent.model_transitions[0].failure_count == 1


def test_structural_context_and_prompt_identity_reach_the_experiment(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, backend = _prepare_agent(tmp_path, monkeypatch)

    result = agent.run()

    assert result.successful
    assert "## Lean source structure (advisory)" in backend.last_user
    assert "target: theorem target" in backend.last_user
    assert "syntax_path: declaration > theorem > by > sorry" in backend.last_user
    record = agent.tracker.records[-1]
    assert record.llm_input_tokens == 42
    assert len(record.prompt_sha256) == 64
    assert len(record.structural_context_sha256) == 64


def test_healthy_file_checks_compile_once_per_content(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, _ = _prepare_agent(tmp_path, monkeypatch)
    source = agent.project.root / "AutoLean" / "Target.lean"
    checks = 0

    def counting_check(*args: object, **kwargs: object) -> BuildResult:
        nonlocal checks
        checks += 1
        return BuildResult(success=True)

    monkeypatch.setattr(agent.project, "check_file", counting_check)
    content = source.read_text(encoding="utf-8")

    assert agent._check_file_health(source, content) is None
    assert agent._check_file_health(source, content) is None
    assert checks == 1

    changed = content + "\n-- edited\n"
    assert agent._check_file_health(source, changed) is None
    assert checks == 2


def test_a_structural_verdict_is_re_examined_after_an_edit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    agent, _ = _prepare_agent(tmp_path, monkeypatch)
    source = agent.project.root / "AutoLean" / "Target.lean"
    broken = BuildResult(
        success=False,
        diagnostics=[
            Diagnostic(
                file=str(source),
                line=1,
                col=0,
                severity="error",
                message="'target' has already been declared",
            )
        ],
    )
    checks = 0
    verdict = broken

    def counting_check(*args: object, **kwargs: object) -> BuildResult:
        nonlocal checks
        checks += 1
        return verdict

    monkeypatch.setattr(agent.project, "check_file", counting_check)
    content = source.read_text(encoding="utf-8")

    assert agent._check_file_health(source, content) is not None
    assert agent._check_file_health(source, content) is not None
    assert checks == 1

    verdict = BuildResult(success=True)
    repaired = content + "\n-- repaired\n"

    assert agent._check_file_health(source, repaired) is None
    assert checks == 2
