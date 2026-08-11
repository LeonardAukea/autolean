"""Security policy tests for model-produced Lean source."""

from __future__ import annotations

import pytest

from autolean.generated_code import (
    GeneratedCodeError,
    safe_lean_comment_text,
    validate_generated_closed_declarations,
    validate_generated_declarations,
    validate_generated_proof,
)


def test_nested_tactic_proof_is_allowed() -> None:
    proof = "have h : True := by\n  trivial\nexact h"
    assert validate_generated_proof(proof) == proof


@pytest.mark.parametrize(
    "proof",
    [
        "exact (by sorry)",
        "admit",
        'run_tac do IO.getEnv "TOKEN"',
        "end\n#eval System.Platform.numBits",
        'exact include_str ".env"',
        "theorem injected : False := by contradiction",
        "exact True.intro\u202e",
    ],
)
def test_proof_escape_forms_are_rejected(proof: str) -> None:
    with pytest.raises(GeneratedCodeError):
        validate_generated_proof(proof)


def test_sorry_declaration_is_allowed_for_formalization() -> None:
    code = "theorem generated : True := by\n  sorry"
    assert validate_generated_declarations(code) == code


def test_closed_declaration_rejects_a_placeholder() -> None:
    with pytest.raises(GeneratedCodeError, match="proof placeholder"):
        validate_generated_closed_declarations("theorem generated : True := by sorry")


@pytest.mark.parametrize(
    "code",
    [
        "axiom generated : False",
        "theorem generated : True := by trivial\nconstant contradiction : False",
        "theorem generated : True := by run_tac do pure ()",
        '#eval IO.getEnv "TOKEN"',
        "import AutoLean.Secrets\ntheorem generated : True := by sorry",
        "This is an explanation.",
    ],
)
def test_declaration_escape_forms_are_rejected(code: str) -> None:
    with pytest.raises(GeneratedCodeError):
        validate_generated_declarations(code)


def test_comment_text_cannot_open_source_or_hide_direction() -> None:
    text = 'title -/\n#eval IO.getEnv "TOKEN" /-\u202e'
    assert safe_lean_comment_text(text) == ('title - / #eval IO.getEnv "TOKEN" / -')
