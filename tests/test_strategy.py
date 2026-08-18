from __future__ import annotations

import json

import pytest

from autolean.llm import LLMResponse
from autolean.strategy import PlanAttempt, ProofStrategyError, generate_proof_plan, parse_proof_plan


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

    payload["methods"] = [str(index) for index in range(5)]
    with pytest.raises(ProofStrategyError, match="exceeds 4"):
        parse_proof_plan(json.dumps(payload))


def test_plan_parser_accepts_a_detailed_item_within_the_plan_budget() -> None:
    payload = _plan_payload()
    detailed_method = "Use the exact Mathlib theorem after checking its namespace. " * 8
    payload["methods"] = [detailed_method]

    plan = parse_proof_plan(json.dumps(payload))

    assert plan.methods == (" ".join(detailed_method.split()),)


def test_plan_parser_bounds_the_complete_normalized_plan() -> None:
    payload = _plan_payload()
    payload["methods"] = ["x" * 12_000]

    with pytest.raises(ProofStrategyError, match="strategy exceeds 12000"):
        parse_proof_plan(json.dumps(payload))


def test_plan_parser_rejects_unstructured_prose() -> None:
    with pytest.raises(ProofStrategyError, match="valid JSON"):
        parse_proof_plan("First, think about the theorem.")


def test_generate_plan_repairs_one_contract_violation_with_the_model() -> None:
    calls: list[str] = []
    responses: list[PlanAttempt] = []
    invalid = _plan_payload()
    invalid["formalization"] = [str(index) for index in range(5)]

    def generate(system: str, user: str) -> LLMResponse:
        del system
        calls.append(user)
        payload = invalid if len(calls) == 1 else _plan_payload()
        return LLMResponse(text=json.dumps(payload), model="fixture")

    plan = generate_proof_plan(
        "A theorem",
        generate,
        guidance=("Keep the exact statement.",),
        on_response=responses.append,
    )

    assert len(calls) == 2
    assert "formalization exceeds 4 items" in calls[1]
    assert "at most 4 strings" in calls[1]
    assert plan.formalization == ("Fix both numerals in Nat.",)
    assert [response.validation_error == "" for response in responses] == [False, True]
    assert responses[0].guidance == ("Keep the exact statement.",)
    assert responses[1].response_sha256 != responses[0].response_sha256
    assert responses[1].as_dict()["response"] == json.dumps(_plan_payload())


def test_plan_parser_requires_the_exact_response_fields() -> None:
    missing = _plan_payload()
    del missing["obstructions"]
    with pytest.raises(ProofStrategyError, match="missing: obstructions"):
        parse_proof_plan(json.dumps(missing))

    extra = _plan_payload()
    extra["chain_of_thought"] = "hidden"
    with pytest.raises(ProofStrategyError, match="unexpected: chain_of_thought"):
        parse_proof_plan(json.dumps(extra))


def test_generate_plan_respects_a_zero_repair_budget() -> None:
    invalid = _plan_payload()
    invalid["methods"] = [str(index) for index in range(5)]

    with pytest.raises(ProofStrategyError, match="methods exceeds 4 items"):
        generate_proof_plan(
            "A theorem",
            lambda _system, _user: LLMResponse(text=json.dumps(invalid), model="fixture"),
            max_repairs=0,
        )
