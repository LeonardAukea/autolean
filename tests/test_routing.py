"""Model routing is explicit, bounded, and evidence-backed."""

from __future__ import annotations

import pytest

from autolean.routing import (
    EscalationPolicy,
    FailureEvidence,
    decide_escalation,
    profile_for_model,
)


def test_known_profile_routes_to_its_same_backend_sibling() -> None:
    decision = decide_escalation(
        policy=EscalationPolicy.ASK,
        current_model="gpt-5.6-luna",
        current_backend="codex_cli",
        failures=(
            FailureEvidence("fail_build", "type_mismatch"),
            FailureEvidence("fail_build", "unknown_identifier"),
        ),
        difficulty=5,
    )

    assert decision is not None
    assert decision.to_profile == "codex-terra"
    assert decision.to_model == "gpt-5.6-terra"
    assert decision.to_backend == "codex_cli"
    assert decision.failure_count == 2


def test_research_target_still_requires_one_kernel_failure() -> None:
    before_failure = decide_escalation(
        policy=EscalationPolicy.AUTO,
        current_model="sonnet",
        current_backend="claude_cli",
        failures=(),
        difficulty=9,
    )
    after_failure = decide_escalation(
        policy=EscalationPolicy.AUTO,
        current_model="sonnet",
        current_backend="claude_cli",
        failures=(FailureEvidence("fail_build", "unsolved_goals"),),
        difficulty=9,
    )

    assert before_failure is None
    assert after_failure is not None
    assert after_failure.to_profile == "opus"


@pytest.mark.parametrize(
    "evidence",
    [
        FailureEvidence("fail_provider", "llm_rate_limit"),
        FailureEvidence("fail_build", "file_structure_error"),
        FailureEvidence("skipped", "duplicate_declaration"),
    ],
)
def test_provider_and_source_failures_do_not_authorize_escalation(
    evidence: FailureEvidence,
) -> None:
    decision = decide_escalation(
        policy=EscalationPolicy.AUTO,
        current_model="sonnet",
        current_backend="claude_cli",
        failures=(evidence, evidence),
        difficulty=5,
    )

    assert decision is None


def test_never_policy_ignores_eligible_failures() -> None:
    assert (
        decide_escalation(
            policy=EscalationPolicy.NEVER,
            current_model="sonnet",
            current_backend="claude_cli",
            failures=(FailureEvidence("fail_build", "type_mismatch"),) * 3,
            difficulty=9,
        )
        is None
    )


def test_explicit_target_authorizes_a_cross_backend_route() -> None:
    decision = decide_escalation(
        policy=EscalationPolicy.ASK,
        current_model="gemma4:26b",
        current_backend="ollama",
        failures=(FailureEvidence("fail_build", "type_mismatch"),) * 2,
        difficulty=4,
        explicit_target="opus",
    )

    assert decision is not None
    assert decision.to_profile == "opus"
    assert decision.to_backend == "claude_cli"


def test_route_rejects_the_active_model_as_its_target() -> None:
    with pytest.raises(ValueError, match="different model"):
        decide_escalation(
            policy=EscalationPolicy.AUTO,
            current_model="sonnet",
            current_backend="claude_cli",
            failures=(FailureEvidence("fail_build", "type_mismatch"),),
            difficulty=9,
            explicit_target="sonnet",
        )


def test_profile_lookup_uses_both_model_and_backend() -> None:
    profile = profile_for_model("gpt-5.6-luna", "openai")

    assert profile is not None
    assert profile.name == "gpt-luna-api"
