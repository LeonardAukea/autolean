"""Classify Lean build errors into categories for smarter retry strategies."""

from __future__ import annotations

import re
from enum import Enum


class ErrorCategory(str, Enum):
    """Categories of Lean build errors.

    Each category suggests a different retry strategy for the LLM.
    """

    TYPE_MISMATCH = "type_mismatch"
    UNKNOWN_IDENTIFIER = "unknown_identifier"
    UNSOLVED_GOALS = "unsolved_goals"
    TACTIC_FAILED = "tactic_failed"
    ELABORATION_ERROR = "elaboration_error"
    TIMEOUT = "timeout"
    SORRY_REMAINS = "sorry_remains"
    SYNTAX_ERROR = "syntax_error"
    OTHER = "other"


def classify_error(message: str) -> ErrorCategory:
    """Classify a Lean diagnostic message into an error category.

    Uses pattern matching on the diagnostic text. Returns the most
    specific category that matches.
    """
    msg = message.lower()

    if "type mismatch" in msg:
        return ErrorCategory.TYPE_MISMATCH
    if "unknown identifier" in msg or "unknown constant" in msg:
        return ErrorCategory.UNKNOWN_IDENTIFIER
    if "unsolved goals" in msg:
        return ErrorCategory.UNSOLVED_GOALS
    if "tactic" in msg and ("failed" in msg or "error" in msg):
        return ErrorCategory.TACTIC_FAILED
    if "declaration uses 'sorry'" in msg:
        return ErrorCategory.SORRY_REMAINS
    if "expected" in msg and "got" in msg:
        return ErrorCategory.SYNTAX_ERROR
    if "timeout" in msg or "deterministic timeout" in msg or "(kernel) deep recursion" in msg:
        return ErrorCategory.TIMEOUT
    if "elaboration" in msg or "failed to synthesize" in msg:
        return ErrorCategory.ELABORATION_ERROR
    if "unexpected token" in msg or "expected token" in msg:
        return ErrorCategory.SYNTAX_ERROR

    return ErrorCategory.OTHER


def retry_hint_for(category: ErrorCategory, error_message: str) -> str:
    """Generate a retry hint for the LLM based on the error category.

    Returns a string to append to the LLM prompt on retry.
    """
    match category:
        case ErrorCategory.TYPE_MISMATCH:
            # Try to extract expected vs actual types
            return (
                f"Your previous attempt had a TYPE MISMATCH error. "
                f"The Lean compiler said:\n{error_message[:500]}\n"
                f"Make sure the types align. Check the goal state carefully."
            )
        case ErrorCategory.UNKNOWN_IDENTIFIER:
            return (
                f"Your previous attempt used an UNKNOWN IDENTIFIER. "
                f"The Lean compiler said:\n{error_message[:300]}\n"
                f"Only use identifiers available in the current scope and imports."
            )
        case ErrorCategory.UNSOLVED_GOALS:
            return (
                f"Your previous attempt left UNSOLVED GOALS:\n{error_message[:500]}\n"
                f"Make sure your tactic block closes ALL goals."
            )
        case ErrorCategory.TACTIC_FAILED:
            return (
                f"A TACTIC FAILED in your previous attempt:\n{error_message[:300]}\n"
                f"Try a different approach or decompose the goal first."
            )
        case ErrorCategory.SYNTAX_ERROR:
            return (
                f"Your previous attempt had a SYNTAX ERROR:\n{error_message[:300]}\n"
                f"Output only valid Lean 4 tactic syntax."
            )
        case ErrorCategory.ELABORATION_ERROR:
            return (
                f"ELABORATION failed:\n{error_message[:300]}\n"
                f"A typeclass instance may be missing, or types could not be inferred."
            )
        case ErrorCategory.TIMEOUT:
            return (
                "Your previous proof caused a TIMEOUT. Try a simpler, more direct approach. "
                "Avoid `simp` on large goals; use targeted rewrites instead."
            )
        case _:
            return f"Previous attempt failed:\n{error_message[:200]}"
