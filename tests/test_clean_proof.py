"""Tests for clean_llm_proof in autolean.agent."""

from __future__ import annotations

from autolean.agent import clean_llm_proof


class TestCleanLlmProof:
    """Tests for stripping markdown fences and LLM artifacts."""

    def test_plain_tactic_returned_unchanged(self) -> None:
        """A simple tactic block with no wrapping comes back as-is."""
        raw = "simp [Nat.add_comm]"
        assert clean_llm_proof(raw) == "simp [Nat.add_comm]"

    def test_multiline_plain_tactic(self) -> None:
        """Multiple tactic lines with no wrapping are returned unchanged."""
        raw = "intro h\nexact h"
        assert clean_llm_proof(raw) == "intro h\nexact h"

    def test_lean_fence_stripped(self) -> None:
        """Markdown ```lean fences are stripped."""
        raw = "```lean\nsimp\n```"
        assert clean_llm_proof(raw) == "simp"

    def test_lean4_fence_stripped(self) -> None:
        """Markdown ```lean4 fences are stripped."""
        raw = "```lean4\nring\n```"
        assert clean_llm_proof(raw) == "ring"

    def test_bare_fence_stripped(self) -> None:
        """Markdown ``` fences with no language tag are stripped."""
        raw = "```\nomega\n```"
        assert clean_llm_proof(raw) == "omega"

    def test_inline_markdown_code_is_stripped(self) -> None:
        """A provider may wrap a one-word proof in inline Markdown."""
        assert clean_llm_proof("`trivial`") == "trivial"

    def test_simpa_proof_is_preserved(self) -> None:
        proof = "simpa [pow_two] using norm_add_sq_eq_norm_sq_add_norm_sq_real h"

        assert clean_llm_proof(proof) == proof

    def test_leading_by_stripped_in_tactic_mode(self) -> None:
        """A leading `by` is stripped when tactic_mode=True."""
        raw = "by\n  simp"
        result = clean_llm_proof(raw, tactic_mode=True)
        # After stripping `by\n`, the indented tactic body remains
        assert result == "  simp"

    def test_leading_by_preserved_in_term_mode(self) -> None:
        """A leading `by` is preserved when tactic_mode=False."""
        raw = "by\n  simp"
        result = clean_llm_proof(raw, tactic_mode=False)
        assert result == "by\n  simp"

    def test_empty_output(self) -> None:
        """Empty or whitespace-only input returns an empty string."""
        assert clean_llm_proof("") == ""
        assert clean_llm_proof("   ") == ""
        assert clean_llm_proof("\n\n") == ""

    def test_multiline_with_blank_lines(self) -> None:
        """Outer blank lines are removed while indentation is preserved."""
        raw = "\n\nsimp\nomega\n\n"
        result = clean_llm_proof(raw)
        assert result == "simp\nomega"

    def test_by_simp_omega_tactic_mode(self) -> None:
        """Tactic mode strips a standalone leading `by`."""
        raw = "by\n  simp\n  omega"
        result = clean_llm_proof(raw, tactic_mode=True)
        # Leading `by` removed, then blank lines removed; indented lines remain
        assert result == "  simp\n  omega"

    def test_fence_plus_by_tactic_mode(self) -> None:
        """Fence stripping and `by` stripping compose correctly."""
        raw = "```lean\nby\n  simp\n```"
        result = clean_llm_proof(raw, tactic_mode=True)
        # Fences stripped, then `by` stripped, indented body remains
        assert result == "  simp"

    def test_fence_plus_by_term_mode(self) -> None:
        """Fence stripping keeps `by` in term mode."""
        raw = "```lean\nby\n  simp\n```"
        result = clean_llm_proof(raw, tactic_mode=False)
        assert result == "by\n  simp"

    def test_tactic_mode_default_true(self) -> None:
        """tactic_mode defaults to True."""
        raw = "by\n  omega"
        result = clean_llm_proof(raw)
        # Defaults to tactic_mode=True, strips `by`, keeps indented body
        assert result == "  omega"

    def test_by_on_same_line_as_tactic_not_stripped(self) -> None:
        """An inline `by simp` remains intact."""
        raw = "by simp"
        result = clean_llm_proof(raw, tactic_mode=True)
        # `by simp` is a single line where strip() != "by", so it stays
        assert result == "by simp"

    def test_complete_theorem_wrapper_is_stripped(self) -> None:
        """A model may echo the requested declaration around its proof."""
        raw = "theorem AutoLeanBackendSmoke : True := by\n  trivial"
        assert clean_llm_proof(raw) == "  trivial"

    def test_inline_complete_theorem_wrapper_is_stripped(self) -> None:
        raw = "theorem AutoLeanBackendSmoke : True := by trivial"
        assert clean_llm_proof(raw) == "trivial"

    def test_fenced_complete_theorem_wrapper_is_stripped(self) -> None:
        raw = "```lean\nlemma smoke : True := by\n  exact True.intro\n```"
        assert clean_llm_proof(raw) == "  exact True.intro"
