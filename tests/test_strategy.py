from __future__ import annotations

import json

import pytest

from autolean.llm import LLMResponse
from autolean.strategy import ProofStrategyError, generate_proof_plan, parse_proof_plan


def _plan_payload() -> dict[str, object]:
    return {
        "objective": "Prove the exact arithmetic equality in Nat.",
        "formalization": ["Fix both numerals in Nat."],
        "observations": ["The expression reduces by computation."],
        "invariants": ["Keep the statement in Nat."],
        "obstructions": ["Reject an interpretation over an unspecified type."],
        "reductions": ["Normalize the left-hand side."],
        "premises": ["Check the Mathlib numeral normalizer."],
        "methods": ["Try norm_num."],
        "partial_results": ["Record normalization of the left-hand side."],
        "risks": ["Numeral types must be explicit."],
        "completion_criteria": ["Lean accepts the theorem without placeholders."],
        "checkpoints": ["Compile the theorem scaffold.", "Kernel-check the final proof."],
        "revision_triggers": ["The inferred numeral type differs from Nat."],
    }


def test_generate_plan_includes_user_guidance_and_is_stable() -> None:
    seen: dict[str, str] = {}

    def generate(system: str, user: str) -> LLMResponse:
        seen.update(system=system, user=user)
        return LLMResponse(text=json.dumps(_plan_payload()), model="fixture")

    plan = generate_proof_plan(
        "1 + 1 = 2",
        generate,
        guidance=("Use natural numbers.",),
        context="Mathlib is imported.",
    )

    assert "Use natural numbers" in seen["user"]
    assert "Mathlib is imported" in seen["user"]
    assert "hidden chain-of-thought" in seen["system"]
    assert plan.methods == ("Try norm_num.",)
    assert "Obstructions to test" in plan.render()
    assert "Completion criteria" in plan.render()
    assert plan.sha256 == parse_proof_plan(plan.to_json()).sha256


def test_plan_parser_accepts_json_fence_and_bounds_arrays() -> None:
    payload = _plan_payload()
    plan = parse_proof_plan(f"```json\n{json.dumps(payload)}\n```")
    assert plan.objective.startswith("Prove")

    payload["methods"] = [str(index) for index in range(9)]
    with pytest.raises(ProofStrategyError, match="exceeds 8"):
        parse_proof_plan(json.dumps(payload))


def test_plan_parser_rejects_unstructured_prose() -> None:
    with pytest.raises(ProofStrategyError, match="valid JSON"):
        parse_proof_plan("First, think about the theorem.")
