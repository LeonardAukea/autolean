"""Fail-closed policy for Lean source produced by a language model."""

from __future__ import annotations

import re


class GeneratedCodeError(ValueError):
    """Generated Lean crosses a source-policy boundary."""


_BIDI_CONTROLS = frozenset("\u061c\u200e\u200f\u202a\u202b\u202c\u202d\u202e\u2066\u2067\u2068\u2069")

_ELABORATOR_ESCAPE = re.compile(
    r"\b(?:"
    r"run_tac|run_term_elab|run_cmd|elab|elab_rules|macro|macro_rules|"
    r"syntax|declare_syntax_cat|initialize|builtin_initialize|unsafe|partial|"
    r"include_str|include_bytes|extern|implemented_by|foreign"
    r")\b"
)
_IO_REFERENCE = re.compile(r"\b(?:IO|System|Process)\s*\.")
_UNSOUND_DECLARATION = re.compile(r"\b(?:admit|sorryAx|axiom|constant)\b")
_PLACEHOLDER = re.compile(r"\b(?:sorry|admit|sorryAx)\b")
#: Attributes and modifiers Lean allows before a declaration keyword. A
#: command is still a command behind them, so the keyword match has to look
#: past `@[simp]`, `private`, `scoped`, and their combinations.
_DECLARATION_PREFIX = (
    r"(?:@\[[^\]\r\n]*\][ \t]*|"
    r"(?:private|protected|noncomputable|scoped|local|nonrec)[ \t]+)*"
)
_COMMAND_ESCAPE = re.compile(
    r"(?m)^[ \t]*(?:#|" + _DECLARATION_PREFIX + r"(?:"
    r"import|namespace|section|end|open|theorem|lemma|def|abbrev|instance|"
    r"structure|class|inductive|axiom|opaque|constant|example|attribute|"
    r"set_option|notation|infix|infixl|infixr|prefix|postfix|variable|"
    r"universe|export|include|omit|mutual"
    r")\b)"
)
#: The sandbox fixes elaboration options on the command line, and a
#: file-level `set_option` outranks them: `set_option autoImplicit true`
#: restores the auto-binding the sandbox switches off. Generated source
#: states theorems; choosing how Lean elaborates them is not its part.
_OPTION_COMMAND = re.compile(r"(?m)^\s*set_option\b")

_DECLARATION = re.compile(
    r"(?m)^\s*(?:noncomputable\s+)?"
    r"(?:theorem|lemma|def|abbrev|instance|structure|class|inductive)\b"
)
_NAMED_DECLARATION = re.compile(
    r"(?m)^[ \t]*(?:noncomputable[ \t]+)?"
    r"(?:theorem|lemma|def|abbrev|structure|class|inductive)[ \t]+"
    r"(?P<name>[^\s:({\[<]+)"
)


def validate_generated_proof(proof: str) -> str:
    """Return a proof body that is safe to pass to sandboxed elaboration.

    The operating-system sandbox is the authority boundary. This lexical
    policy rejects explicit elaborator, command, I/O, and logical escape
    forms before source is touched.
    """
    proof = proof.strip()
    _validate_common(proof)
    if _PLACEHOLDER.search(proof) or _UNSOUND_DECLARATION.search(proof):
        raise GeneratedCodeError("proof contains a placeholder or axiom escape")
    if _COMMAND_ESCAPE.search(proof):
        raise GeneratedCodeError("proof contains a command-level escape")
    return proof


def validate_generated_declarations(code: str) -> str:
    """Return declarations permitted in an inert generated source file."""
    code = code.strip()
    _validate_common(code)
    if _UNSOUND_DECLARATION.search(code):
        raise GeneratedCodeError("declarations contain an axiom escape")
    if re.search(r"(?m)^\s*#", code):
        raise GeneratedCodeError("declarations contain a command invocation")
    if re.search(r"(?m)^\s*import\b", code):
        raise GeneratedCodeError("generated declarations cannot select imports")
    if _OPTION_COMMAND.search(code):
        raise GeneratedCodeError("generated declarations cannot set elaboration options")
    if not _DECLARATION.search(code):
        raise GeneratedCodeError("output contains no Lean declaration")
    return code


def validate_generated_closed_declarations(code: str) -> str:
    """Return generated declarations containing no proof placeholders."""
    code = validate_generated_declarations(code)
    if _PLACEHOLDER.search(code):
        raise GeneratedCodeError("declarations contain a proof placeholder")
    return code


def validate_generated_named_declaration(code: str, expected_name: str) -> str:
    """Return one closed public declaration with the requested Lean name.

    Reactive gap repair inserts source into an existing module. Its source
    boundary is exactly one declaration, so surrounding commands cannot
    change the namespace, local context, notation, or elaboration state of
    declarations that follow it.
    """
    code = validate_generated_closed_declarations(code)
    commands = list(_COMMAND_ESCAPE.finditer(code))
    declarations = list(_NAMED_DECLARATION.finditer(code))
    if len(commands) != 1 or len(declarations) != 1:
        raise GeneratedCodeError("gap output must contain exactly one public named declaration")
    actual_name = declarations[0].group("name")
    if actual_name != expected_name:
        raise GeneratedCodeError(f"gap output declares {actual_name!r}; expected {expected_name!r}")
    return code


def safe_lean_comment_text(text: str) -> str:
    """Return untrusted text that stays inside one Lean comment line."""
    text = text.replace("\x00", "")
    text = "".join(char for char in text if char not in _BIDI_CONTROLS)
    text = " ".join(text.split())
    return text.replace("/-", "/ -").replace("-/", "- /")


def _validate_common(code: str) -> None:
    if not code:
        raise GeneratedCodeError("generated Lean is empty")
    if "\x00" in code or any(char in _BIDI_CONTROLS for char in code):
        raise GeneratedCodeError("generated Lean contains hidden control text")
    if _ELABORATOR_ESCAPE.search(code) or _IO_REFERENCE.search(code):
        raise GeneratedCodeError("generated Lean contains an elaborator or I/O escape")
