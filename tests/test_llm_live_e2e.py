"""Opt-in qualification against a real model and the Lean sandbox."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from autolean.agent import clean_llm_proof
from autolean.generated_code import validate_generated_proof
from autolean.lean_interface import LeanProject
from autolean.llm import create_llm_client
from autolean.program import parse_program
from autolean.strategy import PlanAttempt, generate_proof_plan

pytestmark = pytest.mark.skipif(
    os.environ.get("AUTOLEAN_RUN_LLM_E2E") != "1",
    reason="set AUTOLEAN_RUN_LLM_E2E=1 to call the configured model",
)


def test_real_model_plans_and_proves_inside_sandbox() -> None:
    """Require real planning text and a kernel-accepted proof from one backend."""
    program = Path(os.environ.get("AUTOLEAN_LLM_E2E_PROGRAM", "program.md")).resolve()
    config = parse_program(program)
    project = LeanProject(program.parent / config.lean_project_path)
    responses: list[PlanAttempt] = []

    with create_llm_client(config.llm_config()) as llm:
        assert llm.ping()
        plan = generate_proof_plan(
            "Close `theorem AutoLeanLiveModelSmoke : True := by sorry`.",
            llm.generate,
            context="Mathlib is imported. Lean elaboration and the kernel decide acceptance.",
            on_response=responses.append,
        )
        proof_response = llm.generate(
            system=(
                "You are a Lean 4 proof assistant. Return only a tactic proof body "
                "with no Markdown or explanation."
            ),
            user=(
                "Use this accepted research plan:\n"
                f"{plan.render()}\n\n"
                "Fill `theorem AutoLeanLiveModelSmoke : True := by sorry`."
            ),
        )

    assert responses
    accepted_plan_response = responses[-1]
    assert accepted_plan_response.validation_error == ""
    assert accepted_plan_response.response.strip()
    assert len(accepted_plan_response.response_sha256) == 64
    assert accepted_plan_response.model

    proof = validate_generated_proof(clean_llm_proof(proof_response.text))
    indented = "\n".join(f"  {line}" for line in proof.splitlines())
    source = f"import Mathlib\n\ntheorem AutoLeanLiveModelSmoke : True := by\n{indented}\n"
    environment = project.proof_environment()
    result = project.validate_candidate(
        project.root / "AutoLeanLiveModelSmoke.lean",
        source,
        timeout=120,
        declaration="AutoLeanLiveModelSmoke",
        declaration_line=3,
        expected_environment=environment.sha256,
    )

    assert result.success, result.stderr
    assert result.errors == []
