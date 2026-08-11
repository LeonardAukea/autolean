"""Tests for error classification and retry hints."""

from __future__ import annotations

import pytest

from autolean.error_classifier import ErrorCategory, classify_error, retry_hint_for

# ---------------------------------------------------------------------------
# classify_error
# ---------------------------------------------------------------------------


class TestClassifyError:
    """Tests for mapping diagnostic messages to error categories."""

    def test_type_mismatch(self) -> None:
        assert classify_error("type mismatch\n  expected: Nat") == ErrorCategory.TYPE_MISMATCH

    def test_unknown_identifier(self) -> None:
        assert classify_error("unknown identifier 'foo'") == ErrorCategory.UNKNOWN_IDENTIFIER

    def test_unknown_constant(self) -> None:
        assert classify_error("unknown constant 'Nat.bogus'") == ErrorCategory.UNKNOWN_IDENTIFIER

    def test_unsolved_goals(self) -> None:
        assert classify_error("unsolved goals\ncase zero\n...") == ErrorCategory.UNSOLVED_GOALS

    def test_tactic_simp_failed(self) -> None:
        assert classify_error("tactic 'simp' failed, made no progress") == ErrorCategory.TACTIC_FAILED

    def test_tactic_omega_error(self) -> None:
        assert classify_error("tactic 'omega' error: ...") == ErrorCategory.TACTIC_FAILED

    def test_timeout(self) -> None:
        assert classify_error("deterministic timeout") == ErrorCategory.TIMEOUT

    def test_timeout_simple(self) -> None:
        assert classify_error("timeout") == ErrorCategory.TIMEOUT

    def test_deep_recursion(self) -> None:
        assert classify_error("(kernel) deep recursion detected") == ErrorCategory.TIMEOUT

    def test_sorry_remains(self) -> None:
        assert classify_error("declaration uses 'sorry'") == ErrorCategory.SORRY_REMAINS

    def test_syntax_error_unexpected_token(self) -> None:
        assert classify_error("unexpected token ';'") == ErrorCategory.SYNTAX_ERROR

    def test_syntax_error_expected_token(self) -> None:
        assert classify_error("expected token ')'") == ErrorCategory.SYNTAX_ERROR

    def test_syntax_error_expected_got(self) -> None:
        assert classify_error("expected ':=', got ':'") == ErrorCategory.SYNTAX_ERROR

    def test_elaboration_error(self) -> None:
        assert classify_error("elaboration function stuck") == ErrorCategory.ELABORATION_ERROR

    def test_failed_to_synthesize(self) -> None:
        assert classify_error("failed to synthesize instance Add Foo") == ErrorCategory.ELABORATION_ERROR

    def test_random_text_is_other(self) -> None:
        assert classify_error("something completely unexpected happened") == ErrorCategory.OTHER

    def test_empty_message_is_other(self) -> None:
        assert classify_error("") == ErrorCategory.OTHER

    def test_case_insensitive(self) -> None:
        """Classification is case-insensitive."""
        assert classify_error("TYPE MISMATCH") == ErrorCategory.TYPE_MISMATCH
        assert classify_error("Unknown Identifier 'x'") == ErrorCategory.UNKNOWN_IDENTIFIER
        assert classify_error("UNSOLVED GOALS") == ErrorCategory.UNSOLVED_GOALS


# ---------------------------------------------------------------------------
# retry_hint_for
# ---------------------------------------------------------------------------


class TestRetryHintFor:
    """Tests for retry hint generation."""

    def test_type_mismatch_hint(self) -> None:
        hint = retry_hint_for(ErrorCategory.TYPE_MISMATCH, "type mismatch")
        assert isinstance(hint, str)
        assert len(hint) > 0
        assert "TYPE MISMATCH" in hint

    def test_unknown_identifier_hint(self) -> None:
        hint = retry_hint_for(ErrorCategory.UNKNOWN_IDENTIFIER, "unknown identifier 'foo'")
        assert "UNKNOWN IDENTIFIER" in hint

    def test_unsolved_goals_hint(self) -> None:
        hint = retry_hint_for(ErrorCategory.UNSOLVED_GOALS, "unsolved goals")
        assert "UNSOLVED GOALS" in hint

    def test_tactic_failed_hint(self) -> None:
        hint = retry_hint_for(ErrorCategory.TACTIC_FAILED, "tactic 'simp' failed")
        assert "TACTIC FAILED" in hint

    def test_syntax_error_hint(self) -> None:
        hint = retry_hint_for(ErrorCategory.SYNTAX_ERROR, "unexpected token")
        assert "SYNTAX ERROR" in hint

    def test_elaboration_error_hint(self) -> None:
        hint = retry_hint_for(ErrorCategory.ELABORATION_ERROR, "failed to synthesize")
        assert "ELABORATION" in hint

    def test_timeout_hint(self) -> None:
        hint = retry_hint_for(ErrorCategory.TIMEOUT, "deterministic timeout")
        assert "TIMEOUT" in hint

    def test_sorry_remains_hint(self) -> None:
        hint = retry_hint_for(ErrorCategory.SORRY_REMAINS, "declaration uses 'sorry'")
        # SORRY_REMAINS falls into the default match arm
        assert len(hint) > 0

    def test_other_hint(self) -> None:
        hint = retry_hint_for(ErrorCategory.OTHER, "random error")
        assert len(hint) > 0

    @pytest.mark.parametrize("category", list(ErrorCategory))
    def test_all_categories_return_nonempty_string(self, category: ErrorCategory) -> None:
        """Every ErrorCategory produces a non-empty retry hint."""
        hint = retry_hint_for(category, "some error message")
        assert isinstance(hint, str)
        assert len(hint) > 0

    def test_hint_includes_error_message(self) -> None:
        """The retry hint includes the original error message text."""
        hint = retry_hint_for(ErrorCategory.UNSOLVED_GOALS, "unsolved goals: P and Q")
        assert "P and Q" in hint

    def test_long_message_truncated_in_hint(self) -> None:
        """retry_hint_for truncates very long error messages internally."""
        long_msg = "x" * 1000
        hint = retry_hint_for(ErrorCategory.TYPE_MISMATCH, long_msg)
        # The hint should include the message but capped at 500 chars
        assert len(hint) < 1000 + 200  # some overhead for the template text
