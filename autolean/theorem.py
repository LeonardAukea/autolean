"""Natural-language theorem formalization with sandboxed compiler repair."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from autolean.generated_code import (
    GeneratedCodeError,
    safe_lean_comment_text,
    validate_generated_declarations,
)
from autolean.llm import GenerateFn, LLMError
from autolean.scanner import count_sorries
from autolean.strategy import ProofPlan

_DECLARATION = re.compile(r"(?m)^[ \t]*(?:theorem|lemma)[ \t]+([A-Za-z_][A-Za-z0-9_'.]*)")
_FENCE = re.compile(r"^```(?:lean4?|)?\s*\n?(.*?)\n?```\s*$", re.DOTALL)

_SYSTEM_PROMPT = """\
You are a Lean 4 and Mathlib formalization expert. Return one source fragment
containing definitions followed by exactly one theorem or lemma whose proof is
`by sorry`. Use fully qualified, current Mathlib names and explicit coercions.
Return Lean code only. Imports are supplied by the caller.
"""

_INITIAL_PROMPT = """\
Formalize this mathematical statement:

{statement}

Research plan:
{plan}

The formal theorem must express the stated mathematics faithfully. Mark an
open or underspecified statement in a doc comment, while keeping the theorem
itself precise.
"""

_REPAIR_PROMPT = """\
Repair this Lean 4 formalization of the mathematical statement below.

Statement:
{statement}

Research plan:
{plan}

Current Lean:
```lean
{code}
```

Compiler diagnostics:
{diagnostics}

Preserve the intended mathematics. Return the complete corrected source
fragment with exactly one `by sorry` theorem or lemma.
"""


class FormalizationError(ValueError):
    """A statement could not reach sandboxed Lean elaboration."""


class FormalizationProject(Protocol):
    """Lean project operations required before source installation."""

    root: Path

    def validate_candidate(
        self,
        lean_file: Path,
        content: str,
        *,
        timeout: int,
    ) -> Any: ...


@dataclass(frozen=True)
class FormalizedTheorem:
    """A single compiling theorem scaffold and its formalization evidence."""

    declaration_name: str
    code: str
    source: str
    declaration_line: int
    attempts: int


def formalize_theorem(
    statement: str,
    plan: ProofPlan,
    llm_generate: GenerateFn,
    project: FormalizationProject,
    *,
    max_repairs: int = 2,
    timeout: int = 120,
) -> FormalizedTheorem:
    """Generate and compiler-repair one theorem before project installation."""
    if max_repairs < 0:
        raise ValueError("max_repairs must be non-negative")
    prompt = _INITIAL_PROMPT.format(statement=statement, plan=plan.render())
    code = ""
    diagnostics = ""
    for attempt in range(1, max_repairs + 2):
        try:
            response = llm_generate(_SYSTEM_PROMPT, prompt)
            code = _normalize_formalization(response.text)
            theorem = _formalized_theorem(
                code,
                statement=statement,
                plan=plan,
                attempts=attempt,
            )
        except (LLMError, GeneratedCodeError, FormalizationError) as error:
            diagnostics = str(error)
        else:
            result = project.validate_candidate(
                Path(project.root) / "AutoLean" / "Generated" / "FormalizationCandidate.lean",
                theorem.source,
                timeout=timeout,
            )
            if result.success:
                return theorem
            diagnostics = _diagnostic_summary(result)

        if attempt > max_repairs:
            break
        prompt = _REPAIR_PROMPT.format(
            statement=statement,
            plan=plan.render(),
            code=code or "(no valid Lean source)",
            diagnostics=diagnostics,
        )
    raise FormalizationError(
        f"formalization did not compile after {max_repairs + 1} attempt(s): "
        f"{diagnostics or 'no compiler diagnostics'}"
    )


def generated_theorem_path(lean_root: Path, declaration_name: str) -> Path:
    """Choose an absent, project-relative source path for one theorem."""
    leaf = declaration_name.rsplit(".", 1)[-1]
    words = re.findall(r"[A-Za-z0-9]+", leaf)
    stem = "".join(word[:1].upper() + word[1:] for word in words) or "Theorem"
    if stem[0].isdigit():
        stem = f"Theorem{stem}"
    directory = lean_root / "AutoLean" / "Generated"
    candidate = directory / f"{stem}.lean"
    suffix = 2
    while candidate.exists():
        candidate = directory / f"{stem}_{suffix}.lean"
        suffix += 1
    return candidate


def _normalize_formalization(raw: str) -> str:
    text = raw.strip()
    fenced = _FENCE.fullmatch(text)
    if fenced:
        text = fenced.group(1).strip()
    text = "\n".join(
        line for line in text.splitlines() if not line.strip().startswith(("import ", "-- import"))
    )
    return validate_generated_declarations(text)


def _formalized_theorem(
    code: str,
    *,
    statement: str,
    plan: ProofPlan,
    attempts: int,
) -> FormalizedTheorem:
    if count_sorries(code) != 1:
        raise FormalizationError("formalization must contain exactly one `sorry`")
    sorry = re.search(r"\bsorry\b", code)
    assert sorry is not None
    declarations = [match for match in _DECLARATION.finditer(code) if match.start() < sorry.start()]
    if not declarations:
        raise FormalizationError("`sorry` must belong to a named theorem or lemma")
    declaration = declarations[-1]
    statement_text = safe_lean_comment_text(statement)
    plan_text = safe_lean_comment_text(plan.render())
    prefix = (
        "import Mathlib\n\n"
        "/-!\n"
        "# AutoLean proof task\n\n"
        f"Informal statement: {statement_text}\n"
        f"Proof plan: {plan_text}\n"
        f"Proof plan SHA-256: {plan.sha256}\n"
        "-/\n\n"
    )
    source = f"{prefix}{code.strip()}\n"
    declaration_line = prefix.count("\n") + code[: declaration.start()].count("\n") + 1
    return FormalizedTheorem(
        declaration_name=declaration.group(1),
        code=code.strip(),
        source=source,
        declaration_line=declaration_line,
        attempts=attempts,
    )


def _diagnostic_summary(result: object) -> str:
    messages = [
        " ".join(error.message.split())
        for error in getattr(result, "errors", [])[:5]
        if getattr(error, "message", "")
    ]
    stderr = " ".join(str(getattr(result, "stderr", "")).split())
    if stderr:
        messages.append(stderr)
    return "\n".join(messages)[:4000] or "Lean rejected the formalization"
