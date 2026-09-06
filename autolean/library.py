"""Library builder — create missing types, structures, and lemmas.

When mathlib does not expose a needed concept, AutoLean can build it.
Two modes:
  1. Proactive: `autolean build-library "differential geometry"` — creates
     definitions and basic lemmas for a topic
  2. Reactive: During proving, when a proof fails with "unknown identifier",
     the agent tries to define the missing piece and retries

Generation uses the backend selected by the command's model profile.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from autolean import ui
from autolean.generated_code import (
    safe_lean_comment_text,
    validate_generated_declarations,
    validate_generated_named_declaration,
)
from autolean.llm import GenerateFn, LLMResponse
from autolean.provenance import sha256_text

log = logging.getLogger("autolean")

# ---------------------------------------------------------------------------
# Prompt templates
# ---------------------------------------------------------------------------

BUILD_LIBRARY_PROMPT = """\
You are a Lean 4 formalization expert building a library for {topic}.

Create a .lean file with:
1. Necessary imports from Mathlib (as comments if unsure they exist)
2. Core definitions (structures, types, typeclasses)
3. Basic lemmas with sorry proofs (the agent will fill these later)
4. Docstrings explaining each definition

Focus on foundational definitions that would be needed to state and prove
theorems in {topic}. Keep it self-contained and well-organized.

Output ONLY valid Lean 4 code. No markdown fences.
"""

FILL_GAP_PROMPT = """\
A proof attempt failed because Lean could not find: {missing_name}

The error was: {error_message}

Based on the context:
```lean
{context}
```

Create a Lean 4 definition or lemma for `{missing_name}` that would
make the proof work. Output ONLY the Lean 4 definition. No markdown.
"""

FILL_GAP_SYSTEM = "You are a Lean 4 expert. Create minimal, correct definitions."


# ---------------------------------------------------------------------------
# Gap detection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class MissingDefinition:
    """A definition that's needed but doesn't exist."""

    name: str
    error_message: str
    context: str  # surrounding code where it was needed
    file: str

    def __post_init__(self) -> None:
        values = (self.name, self.error_message, self.context, self.file)
        if any(not isinstance(value, str) for value in values):
            raise ValueError("missing-definition evidence must be text")
        if not self.name or not self.error_message:
            raise ValueError("missing-definition identity must be complete")


@dataclass(frozen=True)
class GeneratedDefinition:
    """One reactive declaration and the request evidence that produced it."""

    code: str
    response: LLMResponse
    prompt_sha256: str

    def __post_init__(self) -> None:
        if not isinstance(self.code, str) or not self.code.strip():
            raise ValueError("generated definition code must not be empty")
        if not isinstance(self.response, LLMResponse):
            raise ValueError("generated definition response must use LLMResponse")
        if re.fullmatch(r"[0-9a-f]{64}", self.prompt_sha256) is None:
            raise ValueError("generated definition prompt must have a lowercase SHA-256")


def detect_missing_definitions(
    error_message: str, context: str = "", file: str = ""
) -> list[MissingDefinition]:
    """Parse build errors to find missing identifiers/definitions.

    Detects unknown identifier and unknown constant diagnostics.
    """
    gaps: list[MissingDefinition] = []

    for m in re.finditer(r"unknown (?:identifier|constant) ['\u2018](\S+?)['\u2019]", error_message):
        name = m.group(1)
        # Single-character and underscore names are binders, not gaps.
        if len(name) < 2 or name.startswith("_"):
            continue
        gaps.append(
            MissingDefinition(
                name=name,
                error_message=error_message[:200],
                context=context,
                file=file,
            )
        )

    return gaps


# ---------------------------------------------------------------------------
# Library file generation
# ---------------------------------------------------------------------------


def generate_library_source(
    topic: str,
    llm_generate: GenerateFn,
) -> str:
    """Generate a Lean 4 library source for a topic, ready for validation."""
    prompt = BUILD_LIBRARY_PROMPT.format(topic=topic)
    response = llm_generate("You are a Lean 4 formalization expert.", prompt)

    code = response.text.strip()
    code = re.sub(r"^```(?:lean4?|)\s*\n?", "", code)
    code = re.sub(r"\n?```\s*$", "", code)
    code = "\n".join(
        line for line in code.splitlines() if not line.strip().startswith(("import ", "-- import"))
    )
    code = validate_generated_declarations(code)

    safe_topic = safe_lean_comment_text(topic)
    header = (
        f"import Mathlib\n\n"
        f"/-!\n"
        f"# {safe_topic.title()} Library\n"
        f"\n"
        f"Auto-generated by AutoLean build-library.\n"
        f"Fill sorry targets with: {ui.command()} solve\n"
        f"-/\n\n"
    )

    return header + code + "\n"


def fill_gap(
    gap: MissingDefinition,
    llm_generate: GenerateFn,
) -> GeneratedDefinition | None:
    """Try to define a missing identifier using the LLM.

    Return the closed declaration and its request evidence when generation
    produces a substantive response.
    """
    prompt = FILL_GAP_PROMPT.format(
        missing_name=gap.name,
        error_message=gap.error_message,
        context=gap.context[:2000],
    )

    response = llm_generate(FILL_GAP_SYSTEM, prompt)

    code = response.text.strip()
    # Clean markdown
    code = re.sub(r"^```(?:lean4?|)\s*\n?", "", code)
    code = re.sub(r"\n?```\s*$", "", code)

    if not code or len(code) < 5:
        return None

    code = validate_generated_named_declaration(code, gap.name)

    log.info("Generated definition for '%s': %d chars", gap.name, len(code))
    return GeneratedDefinition(
        code=code,
        response=response,
        prompt_sha256=sha256_text(f"{FILL_GAP_SYSTEM}\0{prompt}"),
    )
