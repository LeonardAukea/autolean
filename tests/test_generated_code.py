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


def test_noncomputable_definition_is_allowed_as_a_closed_declaration() -> None:
    code = "noncomputable def generated := Classical.choice"
    assert validate_generated_closed_declarations(code) == code


def test_noncomputable_declaration_cannot_escape_a_proof_body() -> None:
    with pytest.raises(GeneratedCodeError, match="command-level escape"):
        validate_generated_proof("exact True.intro\nnoncomputable def injected := 1")


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


class TestDeclarationPrefixEscape:
    """A command is still a command behind an attribute or a modifier."""

    @pytest.mark.parametrize(
        "injected",
        [
            "@[simp] axiom Backdoor : False",
            "@[simp, norm_cast] axiom Backdoor : False",
            "private axiom Backdoor : False",
            "protected theorem sneak : True := trivial",
            "scoped instance bad : Inhabited Nat := ⟨0⟩",
            'local notation "x" => 1',
            "@[inline] def evil : Nat := 0",
            "noncomputable def evil : Nat := 0",
            "nonrec theorem loop : True := trivial",
            "@[simp] opaque hidden : Nat",
            "example : True := trivial",
            "attribute [simp] Nat.add_comm",
            'infixl " ⊕ " => Nat.add',
        ],
    )
    def test_a_prefixed_command_is_rejected_in_a_proof(self, injected: str) -> None:
        with pytest.raises(GeneratedCodeError):
            validate_generated_proof(f"trivial\n{injected}")

    def test_an_axiom_is_rejected_wherever_it_appears(self) -> None:
        """The word carries the risk; no prefix or indentation excuses it."""
        for proof in (
            "trivial\n    axiom Indented : False",
            "exact absurd h (by axiom Inline : False)",
            "trivial -- axiom in a trailing comment",
        ):
            with pytest.raises(GeneratedCodeError):
                validate_generated_proof(proof)

    @pytest.mark.parametrize(
        "proof",
        [
            "trivial",
            "simp [Nat.add_comm]",
            "intro h\nexact h",
            "have h : True := trivial\nexact h",
            "rw [dist_comm p2 p3]\nexact key",
            "constructor <;> simp",
            "induction n with\n| zero => rfl\n| succ k ih => simp [ih]",
        ],
    )
    def test_an_ordinary_proof_still_passes(self, proof: str) -> None:
        assert validate_generated_proof(proof) == proof.strip()


class TestElaborationOptions:
    """The sandbox chooses how Lean elaborates a candidate, not the model."""

    def test_a_file_level_option_is_rejected(self) -> None:
        with pytest.raises(GeneratedCodeError, match="elaboration options"):
            validate_generated_declarations("set_option autoImplicit true\ntheorem t : True := trivial")

    def test_an_option_reaching_the_kernel_is_rejected(self) -> None:
        with pytest.raises(GeneratedCodeError, match="elaboration options"):
            validate_generated_declarations("set_option debug.skipKernelTC true\ntheorem t : True := trivial")

    def test_binders_and_namespaces_a_statement_needs_still_pass(self) -> None:
        """A real formalization opens namespaces and binds variables."""
        accepted = validate_generated_declarations(
            "variable {V : Type*} [NormedAddCommGroup V]\n\n"
            "open EuclideanGeometry\n\n"
            "theorem t (x : V) : x = x := rfl"
        )

        assert "theorem t" in accepted
