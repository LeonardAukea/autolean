"""Whole-agent invariants that span storage, providers, and Lean."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autolean.agent import MAX_REPEATED_ERRORS, AutoLeanAgent
from autolean.error_classifier import ErrorCategory
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
from autolean.tracker import FAILURE_OUTCOMES, TSV_FIELDS, Outcome


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


def test_a_skipped_attempt_records_no_prompt_it_never_sent(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An attempt that returns before the model must not inherit evidence."""
    agent, _ = _prepare_agent(tmp_path, monkeypatch)
    source = agent.project.root / "AutoLean" / "Target.lean"
    fields = {
        "file": source,
        "line": 2,
        "col": 2,
        "decl_name": "target",
        "decl_line": 1,
        "context_before": "theorem target : True := by",
        "context_after": "",
        "rel_path": "AutoLean/Target.lean",
    }

    reached = agent._try_fill_sorry(1, SorryTarget(**fields, qualified_decl_name="target"), 1)
    assert reached.prompt_sha256
    assert reached.llm_input_tokens > 0

    unnamed = agent._try_fill_sorry(2, SorryTarget(**fields, qualified_decl_name=""), 2)

    assert unnamed.outcome is Outcome.SKIPPED
    assert unnamed.prompt_sha256 == ""
    assert unnamed.strategy_sha256 == ""
    assert unnamed.structural_context_sha256 == ""
    assert unnamed.llm_input_tokens == 0


def test_resuming_remembers_which_targets_are_already_proved(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A resumed run must not re-attempt a target a previous run proved."""
    agent, _ = _prepare_agent(tmp_path, monkeypatch)
    proved = "AutoLean/Target.lean:2:target"
    attempted = "AutoLean/Target.lean:9:other"
    rows = [
        "\t".join(TSV_FIELDS),
        "\t".join(
            {"cycle": "3", "target_id": proved, "outcome": "success", "attempt": "2"}.get(f, "")
            for f in TSV_FIELDS
        ),
        "\t".join(
            {"cycle": "4", "target_id": attempted, "outcome": "fail_build", "attempt": "5"}.get(f, "")
            for f in TSV_FIELDS
        ),
    ]
    agent.tracker.results_file.write_text("\n".join(rows) + "\n", encoding="utf-8")

    agent._proved_ids.clear()
    agent._attempts.clear()
    agent._load_resume_state()

    assert proved in agent._proved_ids, "a proved target must not be attempted again"
    assert attempted not in agent._proved_ids
    assert agent._attempts[attempted] == 5, "the retry budget must survive a resume"
    assert agent.tracker._cycle == 4


def test_an_overnight_run_stops_when_every_target_is_unattemptable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An epoch that only skipped would reset into the same skips forever."""
    agent, backend = _prepare_agent(tmp_path, monkeypatch)
    source = agent.project.root / "AutoLean" / "Target.lean"
    unattemptable = SorryTarget(
        file=source,
        line=2,
        col=2,
        decl_name="target",
        decl_line=1,
        context_before="theorem target : True := by",
        context_after="",
        rel_path="AutoLean/Target.lean",
        qualified_decl_name="",  # no name to bind an axiom audit to
    )
    monkeypatch.setattr("autolean.agent.scan_project", lambda root: [unattemptable])
    agent.config.max_cycles = 0  # overnight: no cycle budget to stop the loop
    agent.config.max_retries_per_sorry = 2
    logged: list[object] = []
    ceiling = agent.config.max_retries_per_sorry * 4

    def bounded_log(record: object) -> None:
        logged.append(record)
        if len(logged) > ceiling:
            # Without the gate this loop never ends; stop it so the failure
            # reads as an assertion rather than a hung job.
            agent._interrupted = True

    monkeypatch.setattr(agent.tracker, "log", bounded_log)

    agent.run()

    assert backend.proof_calls == 0, "the target never reaches a provider"
    assert logged, "the loop must actually run"
    assert len(logged) <= agent.config.max_retries_per_sorry, (
        f"the run reset into the same skips: {len(logged)} records"
    )


class TestBoundedWork:
    """Every loop the agent runs has to be provably finite."""

    def test_the_same_error_three_times_ends_the_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Repeating one diagnostic teaches nothing; the budget must not fund it."""
        agent, _ = _prepare_agent(tmp_path, monkeypatch)
        target_id = "AutoLean/Target.lean:2:target"

        for seen in range(1, MAX_REPEATED_ERRORS + 1):
            assert agent._should_bail_repeated_error(target_id) is (seen > MAX_REPEATED_ERRORS)
            agent._record_error_category(target_id, ErrorCategory.TYPE_MISMATCH)

        assert agent._should_bail_repeated_error(target_id)

    def test_a_changing_error_keeps_the_budget(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        """A different diagnostic is new evidence, so the target continues."""
        agent, _ = _prepare_agent(tmp_path, monkeypatch)
        target_id = "AutoLean/Target.lean:2:target"

        for category in (
            ErrorCategory.TYPE_MISMATCH,
            ErrorCategory.UNKNOWN_IDENTIFIER,
            ErrorCategory.TYPE_MISMATCH,
        ):
            agent._record_error_category(target_id, category)

        assert not agent._should_bail_repeated_error(target_id)

    def test_a_statement_that_never_compiles_stops_repairing(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Formalization repair is bounded even when nothing ever compiles."""
        from autolean.theorem import FormalizationError, formalize_theorem

        del tmp_path, monkeypatch
        calls = {"n": 0}

        def never_compiles(system: str, user: str, **kwargs: object) -> LLMResponse:
            calls["n"] += 1
            return LLMResponse(
                text="theorem broken : Nonsense := by sorry",
                model="test",
                input_tokens=1,
                output_tokens=1,
            )

        class _StubPlan:
            sha256 = "a" * 64

            def render(self) -> str:
                return "objective: prove a claim"

        class AlwaysRejects:
            root = Path("/nonexistent")

            def validate_candidate(self, *args: object, **kwargs: object) -> BuildResult:
                return BuildResult(success=False, stderr="unknown identifier 'Nonsense'")

        with pytest.raises(FormalizationError):
            formalize_theorem(
                "a claim",
                _StubPlan(),  # type: ignore[arg-type]
                never_compiles,
                AlwaysRejects(),  # type: ignore[arg-type]
                max_repairs=2,
            )

        assert calls["n"] <= 3, f"repair was unbounded: {calls['n']} model requests"


class TestRunScope:
    """A filtered run must touch only what it was pointed at."""

    def test_a_rescan_does_not_re_admit_excluded_targets(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Proving one declaration must not admit its file's other holes."""
        agent, _ = _prepare_agent(tmp_path, monkeypatch)
        agent.target_filter = "foo"
        source = agent.project.root / "AutoLean" / "Target.lean"

        def target(name: str, line: int) -> SorryTarget:
            return SorryTarget(
                file=source,
                line=line,
                col=2,
                decl_name=name,
                decl_line=line - 1,
                context_before=f"theorem {name} : True := by",
                context_after="",
                rel_path="AutoLean/Target.lean",
                qualified_decl_name=name,
            )

        assert agent._in_scope(target("foo", 2))
        assert not agent._in_scope(target("bar", 9))

    def test_an_unfiltered_run_admits_everything(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent, _ = _prepare_agent(tmp_path, monkeypatch)
        agent.target_filter = None
        source = agent.project.root / "AutoLean" / "Target.lean"
        anything = SorryTarget(
            file=source,
            line=9,
            col=2,
            decl_name="bar",
            decl_line=8,
            context_before="theorem bar : True := by",
            context_after="",
            rel_path="AutoLean/Target.lean",
            qualified_decl_name="bar",
        )

        assert agent._in_scope(anything)

    def test_a_target_id_also_selects(self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
        agent, _ = _prepare_agent(tmp_path, monkeypatch)
        source = agent.project.root / "AutoLean" / "Target.lean"
        one = SorryTarget(
            file=source,
            line=2,
            col=2,
            decl_name="foo",
            decl_line=1,
            context_before="theorem foo : True := by",
            context_after="",
            rel_path="AutoLean/Target.lean",
            qualified_decl_name="foo",
        )
        agent.target_filter = one.id

        assert agent._in_scope(one)


def test_a_rejected_candidate_does_not_condemn_the_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The verdict belongs to the bytes Lean read, not to the ones it refused."""
    agent, _ = _prepare_agent(tmp_path, monkeypatch)
    source = agent.project.root / "AutoLean" / "Target.lean"
    content = source.read_text(encoding="utf-8")

    assert agent._check_file_health(source, content) is None

    agent._file_health[source] = (sha256_text(content), None)
    monkeypatch.setattr(
        agent.project,
        "validate_candidate",
        lambda *a, **k: BuildResult(
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
        ),
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

    agent._try_fill_sorry(1, target, 1)

    assert source.read_text(encoding="utf-8") == content, "the file was not modified"
    assert agent._check_file_health(source, content) is None, (
        "a candidate's failure was recorded as the file's verdict"
    )


class TestTimeoutOutcome:
    """A spent budget is not a verdict about the proof."""

    def test_a_timed_out_check_is_not_recorded_as_a_surviving_sorry(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        agent, _ = _prepare_agent(tmp_path, monkeypatch)
        monkeypatch.setattr(
            agent.project,
            "validate_candidate",
            lambda *args, **kwargs: BuildResult(
                success=False,
                stderr="Build timed out after 120s",
                duration_seconds=120.0,
                timed_out=True,
            ),
        )

        agent.run()

        failures = [record for record in agent.tracker.records if record.outcome in FAILURE_OUTCOMES]
        assert failures, "the run recorded no failure"
        assert {record.outcome for record in failures} == {Outcome.FAIL_TIMEOUT}
        assert {record.error_category for record in failures} == {ErrorCategory.TIMEOUT.value}


class TestEpochMemory:
    """A new epoch renews the budget, not the agent's memory of failure."""

    def test_a_second_epoch_still_knows_the_rejected_candidates(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent, backend = _prepare_agent(tmp_path, monkeypatch)
        backend.text = "exact rejected_by_lean"
        agent.config.max_cycles = 0
        agent.config.max_retries_per_sorry = 2
        monkeypatch.setattr(
            agent.project,
            "validate_candidate",
            lambda *a, **k: BuildResult(
                success=False,
                diagnostics=[Diagnostic("Target.lean", 2, 2, "error", "unknown identifier")],
            ),
        )
        proof_prompts: list[str] = []
        original = agent.llm.generate

        def record(system: str, user: str, **kwargs: object) -> object:
            if "mathematical research planner" not in system:
                proof_prompts.append(user)
                if len(proof_prompts) >= 3:
                    agent._interrupted = True
            return original(system, user, **kwargs)

        monkeypatch.setattr(agent.llm, "generate", record)

        agent.run()

        # Two attempts exhaust the budget; the third opens the next epoch.
        assert len(proof_prompts) == 3, f"the run never opened a second epoch: {len(proof_prompts)}"
        assert "rejected_by_lean" in proof_prompts[2], (
            "the epoch reset dropped the candidates Lean had already rejected"
        )


class TestAuditBinding:
    """The audit is only sound if it is aimed at the target."""

    def test_the_agent_binds_every_audit_to_its_target(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        agent, _ = _prepare_agent(tmp_path, monkeypatch)
        audited: list[dict[str, object]] = []

        def recording_validate(*args: object, **kwargs: object) -> BuildResult:
            del args
            audited.append(kwargs)
            return BuildResult(success=True, axioms=(), duration_seconds=0.1)

        monkeypatch.setattr(agent.project, "validate_candidate", recording_validate)

        agent.run()

        assert audited, "the run validated no candidate"
        for call in audited:
            # Without a name and line the audit is skipped altogether, and a
            # candidate reaching `native_decide` would be accepted with
            # `Lean.ofReduceBool` in its closure.
            assert call["declaration"] == "target"
            assert call["declaration_line"] == 2
            assert call["expected_environment"] == "a" * 64
