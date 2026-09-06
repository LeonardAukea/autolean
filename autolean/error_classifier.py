"""Classify Lean build errors into categories for retry policy."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import TypeAlias

from autolean.prompts import LEAN_TACTICS
from autolean.validation import require_instance, require_text


class ErrorCategory(StrEnum):
    """Categories of Lean build errors, one retry strategy each."""

    TYPE_MISMATCH = "type_mismatch"
    UNKNOWN_IDENTIFIER = "unknown_identifier"
    UNSOLVED_GOALS = "unsolved_goals"
    TACTIC_FAILED = "tactic_failed"
    ELABORATION_ERROR = "elaboration_error"
    TIMEOUT = "timeout"
    SORRY_REMAINS = "sorry_remains"
    SYNTAX_ERROR = "syntax_error"
    APPLICATION_ERROR = "application_error"
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

ErrorRule: TypeAlias = tuple[ErrorCategory, tuple[str, ...], tuple[str, ...]]

# Rules are precedence ordered. Cost failures precede generic tactic failures
# because Lean reports a tactic as the outer failure when that tactic times out.
_ERROR_RULES: tuple[ErrorRule, ...] = (
    (ErrorCategory.DUPLICATE_DECLARATION, ("has already been declared",), ()),
    (ErrorCategory.DUPLICATE_DECLARATION, ("already declared", "in the current"), ()),
    (ErrorCategory.FILE_STRUCTURE_ERROR, ("invalid 'import' command",), ()),
    (ErrorCategory.FILE_STRUCTURE_ERROR, ("it must be used in the beginning of the file",), ()),
    (ErrorCategory.FILE_STRUCTURE_ERROR, ("invalid 'open' command", "beginning"), ()),
    (ErrorCategory.LAKE_CONFIG_ERROR, ("unknown target",), ()),
    (ErrorCategory.LAKE_CONFIG_ERROR, (), ("unknown package", "unknown module")),
    (ErrorCategory.LAKE_CONFIG_ERROR, ("build failed", "lake"), ()),
    (ErrorCategory.TACTIC_FAILED, ("unknown tactic",), ()),
    (ErrorCategory.TYPE_MISMATCH, ("type mismatch",), ()),
    (ErrorCategory.UNKNOWN_IDENTIFIER, (), ("unknown identifier", "unknown constant")),
    (ErrorCategory.UNKNOWN_IDENTIFIER, (), ("unknown namespace", "unknown declaration")),
    (ErrorCategory.UNSOLVED_GOALS, ("unsolved goals",), ()),
    (ErrorCategory.TIMEOUT, (), ("timeout", "(kernel) deep recursion")),
    (ErrorCategory.TIMEOUT, (), ("maximum recursion depth", "heartbeats")),
    (ErrorCategory.TACTIC_FAILED, ("tactic",), ("failed", "error")),
    (ErrorCategory.TACTIC_FAILED, ("omega", "failed"), ()),
    (ErrorCategory.TACTIC_FAILED, ("simp made no progress",), ()),
    (ErrorCategory.TACTIC_FAILED, (), ("ring_nf failed", "ring failed")),
    (ErrorCategory.SORRY_REMAINS, ("declaration uses 'sorry'",), ()),
    (ErrorCategory.SORRY_REMAINS, ("'sorry'", "uses"), ()),
    (ErrorCategory.SYNTAX_ERROR, ("expected", "got"), ()),
    (ErrorCategory.ELABORATION_ERROR, (), ("elaboration", "failed to synthesize")),
    (ErrorCategory.SYNTAX_ERROR, (), ("unexpected token", "expected token")),
    (ErrorCategory.SYNTAX_ERROR, ("unexpected end of input",), ()),
    (ErrorCategory.APPLICATION_ERROR, (), ("function expected", "incorrect number of arguments")),
    (ErrorCategory.APPLICATION_ERROR, ("too many arguments",), ()),
    (ErrorCategory.UNKNOWN_IDENTIFIER, ("not found",), ("field", "member")),
    (ErrorCategory.UNKNOWN_IDENTIFIER, ("has not been defined",), ()),
    (ErrorCategory.UNKNOWN_IDENTIFIER, ("invalid field",), ()),
    (ErrorCategory.TACTIC_FAILED, ("failed",), ("apply", "exact", "rewrite", "rw", "intro")),
    (ErrorCategory.TACTIC_FAILED, ("no goals", "to be solved"), ()),
)


_RETRY_HINTS: dict[ErrorCategory, tuple[int, str]] = {
    ErrorCategory.TYPE_MISMATCH: (
        500,
        "Your previous attempt had a TYPE MISMATCH error. "
        "The Lean compiler said:\n{error}\n"
        "Make sure the types align. Check the goal state carefully.",
    ),
    ErrorCategory.UNKNOWN_IDENTIFIER: (
        300,
        "Your previous attempt used an UNKNOWN IDENTIFIER. "
        "The Lean compiler said:\n{error}\n"
        "Use only identifiers available in the current scope and imports.",
    ),
    ErrorCategory.UNSOLVED_GOALS: (
        500,
        "Your previous attempt left UNSOLVED GOALS:\n{error}\nMake sure your tactic block closes every goal.",
    ),
    ErrorCategory.SYNTAX_ERROR: (
        300,
        "Your previous attempt had a SYNTAX ERROR:\n{error}\nOutput only valid Lean 4 tactic syntax.",
    ),
    ErrorCategory.ELABORATION_ERROR: (
        300,
        "ELABORATION failed:\n{error}\nCheck type inference and the required typeclass instances.",
    ),
    ErrorCategory.TIMEOUT: (
        0,
        "Your previous proof caused a TIMEOUT. Try a simpler, more direct approach. "
        "Use targeted rewrites on large goals.",
    ),
    ErrorCategory.DUPLICATE_DECLARATION: (
        300,
        "STRUCTURAL ERROR: {error}\n"
        "This theorem name already exists in the file. The agent must skip this target.",
    ),
    ErrorCategory.FILE_STRUCTURE_ERROR: (
        300,
        "STRUCTURAL ERROR: {error}\nThe file requires structural repair.",
    ),
    ErrorCategory.LAKE_CONFIG_ERROR: (
        300,
        "BUILD CONFIG ERROR: {error}\nThe project configuration requires repair.",
    ),
    ErrorCategory.APPLICATION_ERROR: (
        400,
        "FUNCTION APPLICATION ERROR:\n{error}\nCheck the function signature and argument count.",
    ),
}


def classify_error(message: str) -> ErrorCategory:
    """Return the most specific category matching a Lean diagnostic."""
    require_text(message, "Lean diagnostic must be text", allow_empty=True)
    msg = message.lower()
    for category, required, alternatives in _ERROR_RULES:
        if all(term in msg for term in required) and (
            not alternatives or any(term in msg for term in alternatives)
        ):
            return category
    return ErrorCategory.OTHER


def retry_hint_for(category: ErrorCategory, error_message: str) -> str:
    """Return the bounded retry instruction for one diagnostic category."""
    require_instance(category, ErrorCategory, "retry category must use ErrorCategory")
    require_text(error_message, "retry diagnostic must be text", allow_empty=True)
    if category is ErrorCategory.TACTIC_FAILED:
        return _tactic_retry_hint(error_message)
    spec = _RETRY_HINTS.get(category)
    if spec is None:
        return f"Previous attempt failed:\n{error_message[:200]}"
    limit, template = spec
    return template.format(error=error_message[:limit])


def _tactic_retry_hint(error_message: str) -> str:
    """Return the specific retry instruction for one tactic failure."""
    match = re.search(r"unknown tactic '(\w+)'", error_message)
    if match is not None:
        known = ", ".join(sorted(LEAN_TACTICS))
        return f"Use a Lean 4 tactic from this set: {known}. Unknown tactic: `{match.group(1)}`."
    return (
        f"A TACTIC FAILED in your previous attempt:\n{error_message[:300]}\n"
        "Try a different approach or decompose the goal first."
    )
