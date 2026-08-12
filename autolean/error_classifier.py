"""Classify Lean build errors into categories for retry policy."""

from __future__ import annotations

import re
from enum import StrEnum


class ErrorCategory(StrEnum):
    """Categories of Lean build errors.

    Each category suggests a different retry strategy for the LLM.
    Structural errors (DUPLICATE_DECLARATION, FILE_STRUCTURE_ERROR,
    LAKE_CONFIG_ERROR) indicate problems the LLM cannot fix — the agent
    should skip or fix the file first.
    """

    TYPE_MISMATCH = "type_mismatch"
    UNKNOWN_IDENTIFIER = "unknown_identifier"
    UNSOLVED_GOALS = "unsolved_goals"
    TACTIC_FAILED = "tactic_failed"
    ELABORATION_ERROR = "elaboration_error"
    TIMEOUT = "timeout"
    SORRY_REMAINS = "sorry_remains"
    SYNTAX_ERROR = "syntax_error"
    APPLICATION_ERROR = "application_error"
    # Structural errors — LLM retries cannot fix these
    DUPLICATE_DECLARATION = "duplicate_declaration"
    FILE_STRUCTURE_ERROR = "file_structure_error"
    LAKE_CONFIG_ERROR = "lake_config_error"
    OTHER = "other"


# Structural errors arise in imports, syntax, or declaration shape and cannot
# be repaired by changing one proof body.
STRUCTURAL_ERRORS: frozenset[ErrorCategory] = frozenset(
    {
        ErrorCategory.DUPLICATE_DECLARATION,
        ErrorCategory.FILE_STRUCTURE_ERROR,
        ErrorCategory.LAKE_CONFIG_ERROR,
    }
)


def classify_error(message: str) -> ErrorCategory:
    """Classify a Lean diagnostic message into an error category.

    Uses pattern matching on the diagnostic text. Returns the most
    specific category that matches.

    Structural errors are detected first — these indicate problems the LLM
    cannot fix (duplicate names, corrupted file structure, bad imports).
    """
    msg = message.lower()

    # --- Structural errors (check first — no point retrying with LLM) ---
    if "has already been declared" in msg:
        return ErrorCategory.DUPLICATE_DECLARATION
    if "already declared" in msg and "in the current" in msg:
        return ErrorCategory.DUPLICATE_DECLARATION
    if "invalid 'import' command" in msg:
        return ErrorCategory.FILE_STRUCTURE_ERROR
    if "it must be used in the beginning of the file" in msg:
        return ErrorCategory.FILE_STRUCTURE_ERROR
    if "invalid 'open' command" in msg and "beginning" in msg:
        return ErrorCategory.FILE_STRUCTURE_ERROR
    # Lake/build configuration errors
    if "unknown target" in msg:
        return ErrorCategory.LAKE_CONFIG_ERROR
    if "unknown package" in msg or "unknown module" in msg:
        return ErrorCategory.LAKE_CONFIG_ERROR
    if "build failed" in msg and "lake" in msg:
        return ErrorCategory.LAKE_CONFIG_ERROR

    # Unknown tactic = LLM hallucinated a tactic name
    if "unknown tactic" in msg:
        return ErrorCategory.TACTIC_FAILED

    # --- Proof errors (LLM retries may help) ---
    if "type mismatch" in msg:
        return ErrorCategory.TYPE_MISMATCH
    if "unknown identifier" in msg or "unknown constant" in msg:
        return ErrorCategory.UNKNOWN_IDENTIFIER
    if "unknown namespace" in msg or "unknown declaration" in msg:
        return ErrorCategory.UNKNOWN_IDENTIFIER
    if "unsolved goals" in msg:
        return ErrorCategory.UNSOLVED_GOALS
    if "tactic" in msg and ("failed" in msg or "error" in msg):
        return ErrorCategory.TACTIC_FAILED
    if "omega" in msg and "failed" in msg:
        return ErrorCategory.TACTIC_FAILED
    if "simp made no progress" in msg:
        return ErrorCategory.TACTIC_FAILED
    if "ring_nf failed" in msg or "ring failed" in msg:
        return ErrorCategory.TACTIC_FAILED
    if "declaration uses 'sorry'" in msg:
        return ErrorCategory.SORRY_REMAINS
    if "'sorry'" in msg and "uses" in msg:
        return ErrorCategory.SORRY_REMAINS
    if "expected" in msg and "got" in msg:
        return ErrorCategory.SYNTAX_ERROR
    if "timeout" in msg or "deterministic timeout" in msg or "(kernel) deep recursion" in msg:
        return ErrorCategory.TIMEOUT
    if "maximum recursion depth" in msg or "max heartbeats" in msg:
        return ErrorCategory.TIMEOUT
    if "elaboration" in msg or "failed to synthesize" in msg:
        return ErrorCategory.ELABORATION_ERROR
    if "failed to synthesize instance" in msg:
        return ErrorCategory.ELABORATION_ERROR
    if "unexpected token" in msg or "expected token" in msg:
        return ErrorCategory.SYNTAX_ERROR
    if "unexpected end of input" in msg:
        return ErrorCategory.SYNTAX_ERROR
    # Function application errors
    if "function expected" in msg:
        return ErrorCategory.APPLICATION_ERROR
    if "application type mismatch" in msg:
        return ErrorCategory.TYPE_MISMATCH
    if "incorrect number of arguments" in msg:
        return ErrorCategory.APPLICATION_ERROR
    if "too many arguments" in msg:
        return ErrorCategory.APPLICATION_ERROR

    # More unknown identifier patterns
    if "not found" in msg and ("field" in msg or "member" in msg):
        return ErrorCategory.UNKNOWN_IDENTIFIER
    if "has not been defined" in msg:
        return ErrorCategory.UNKNOWN_IDENTIFIER
    if "invalid field" in msg:
        return ErrorCategory.UNKNOWN_IDENTIFIER

    # Catch remaining tactic failures
    if "failed" in msg and any(kw in msg for kw in ["apply", "exact", "rewrite", "rw", "intro"]):
        return ErrorCategory.TACTIC_FAILED
    if "no goals" in msg and "to be solved" in msg:
        return ErrorCategory.TACTIC_FAILED

    return ErrorCategory.OTHER


def retry_hint_for(category: ErrorCategory, error_message: str) -> str:
    """Generate a retry hint for the LLM based on the error category.

    Returns a string to append to the LLM prompt on retry.
    """
    match category:
        case ErrorCategory.TYPE_MISMATCH:
            return (
                f"Your previous attempt had a TYPE MISMATCH error. "
                f"The Lean compiler said:\n{error_message[:500]}\n"
                f"Make sure the types align. Check the goal state carefully."
            )
        case ErrorCategory.UNKNOWN_IDENTIFIER:
            return (
                f"Your previous attempt used an UNKNOWN IDENTIFIER. "
                f"The Lean compiler said:\n{error_message[:300]}\n"
                f"Only use identifiers available in the current scope and imports. "
                f"Do NOT invent lemma names — only use names you can see in the context."
            )
        case ErrorCategory.UNSOLVED_GOALS:
            return (
                f"Your previous attempt left UNSOLVED GOALS:\n{error_message[:500]}\n"
                f"Make sure your tactic block closes ALL goals."
            )
        case ErrorCategory.TACTIC_FAILED:
            # Extract the tactic name that failed for a more targeted hint
            tactic_name = ""
            m = re.search(r"unknown tactic '(\w+)'", error_message)
            if m:
                tactic_name = m.group(1)
                return (
                    f"CRITICAL: You used `{tactic_name}` which DOES NOT EXIST in Lean 4. "
                    f"Only use real Lean 4 tactics: simp, ring, omega, rfl, exact, apply, "
                    f"intro, cases, induction, rw, constructor, trivial, decide, norm_num, "
                    f"contradiction, assumption, tauto, aesop, linarith, positivity, ext, funext, "
                    f"use, obtain, refine, by_contra, by_cases, split, left, right, have, let, calc."
                )
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
        case ErrorCategory.DUPLICATE_DECLARATION:
            return (
                f"STRUCTURAL ERROR: {error_message[:300]}\n"
                f"This theorem name already exists in the file. "
                f"This is NOT a proof error — the agent should skip this target."
            )
        case ErrorCategory.FILE_STRUCTURE_ERROR:
            return (
                f"STRUCTURAL ERROR: {error_message[:300]}\n"
                f"The file has a structural problem (e.g., import in wrong position). "
                f"This is NOT a proof error — the file needs manual repair."
            )
        case ErrorCategory.LAKE_CONFIG_ERROR:
            return (
                f"BUILD CONFIG ERROR: {error_message[:300]}\n"
                f"The build system cannot find this module. "
                f"This is NOT a proof error — the project config needs fixing."
            )
        case ErrorCategory.APPLICATION_ERROR:
            return (
                f"FUNCTION APPLICATION ERROR:\n{error_message[:400]}\n"
                f"You applied a function/constructor with wrong arguments. "
                f"Check the type signature and number of arguments."
            )
        case _:
            return f"Previous attempt failed:\n{error_message[:200]}"
