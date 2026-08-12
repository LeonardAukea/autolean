"""Concise, reviewable mathematical proof strategies."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from autolean.llm import GenerateFn, LLMError, LLMResponse

_PLAN_FIELDS = (
    "formalization",
    "observations",
    "invariants",
    "obstructions",
    "reductions",
    "premises",
    "methods",
    "partial_results",
    "risks",
    "completion_criteria",
    "checkpoints",
    "revision_triggers",
)
_MAX_ITEMS = 4
_MAX_ITEM_CHARS = 240
_MAX_PLAN_CHARS = 12_000
_MAX_RESPONSE_CHARS = 64_000

_SYSTEM_PROMPT = """\
You are a mathematical research planner. Produce a concise, reviewable proof
strategy whose claims can be checked independently. State uncertainty and open
status explicitly. Do not provide hidden chain-of-thought or narrative
reasoning. Return one JSON object and no Markdown.
"""

_USER_PROMPT = """\
Plan work on this mathematical statement:

{statement}

User guidance:
{guidance}

Additional formal context:
{context}

Return exactly these JSON fields:
- objective: one precise sentence
- formalization: domains, quantifiers, hypotheses, and definitions to fix
- observations: small examples, edge cases, or possible counterexamples
- invariants: quantities, structures, or semantic facts that must be preserved
- obstructions: counterexamples, failure modes, and barriers to test first
- reductions: independently checkable subgoals
- premises: existing results or library facts to verify
- methods: candidate proof methods in preferred order
- partial_results: useful intermediate results worth retaining independently
- risks: ambiguity, missing infrastructure, or signs the statement is open
- completion_criteria: exact evidence required to call the work complete
- checkpoints: finite milestones that can be validated before proceeding
- revision_triggers: observations that require changing the plan

Every field after objective is an array of at most {max_items} strings. Each
string is at most {max_item_chars} characters. Preserve the named fields even
when an array is empty. Keep the complete response concise enough to review in
one terminal screen.
"""

_REPAIR_PROMPT = """\
Your previous proof strategy violated this response contract:

{error}

Return one corrected JSON object with the exact requested fields. Every array
contains at most {max_items} strings, and every string contains at most
{max_item_chars} characters. Do not omit mathematical risks or completion
criteria; combine related items.

Previous response:
{response}
"""


class ProofStrategyError(ValueError):
    """A model response cannot form a bounded proof strategy."""


@dataclass(frozen=True)
class PlanAttempt:
    """One exact model response in a bounded planning exchange."""

    attempt: int
    guidance: tuple[str, ...]
    response: str
    model: str
    input_tokens: int
    output_tokens: int
    duration_seconds: float
    validation_error: str = ""

    @property
    def response_sha256(self) -> str:
        """Return the identity of the provider response bytes."""
        return hashlib.sha256(self.response.encode()).hexdigest()

    def as_dict(self) -> dict[str, object]:
        """Return the complete response record for an audit artifact."""
        return {
            "attempt": self.attempt,
            "duration_seconds": self.duration_seconds,
            "guidance": list(self.guidance),
            "input_tokens": self.input_tokens,
            "model": self.model,
            "output_tokens": self.output_tokens,
            "response": self.response,
            "response_sha256": self.response_sha256,
            "validation_error": self.validation_error,
        }


@dataclass(frozen=True)
class ProofPlan:
    """A bounded mathematical work plan exposed to the user and prover."""

    objective: str
    formalization: tuple[str, ...] = ()
    observations: tuple[str, ...] = ()
    invariants: tuple[str, ...] = ()
    obstructions: tuple[str, ...] = ()
    reductions: tuple[str, ...] = ()
    premises: tuple[str, ...] = ()
    methods: tuple[str, ...] = ()
    partial_results: tuple[str, ...] = ()
    risks: tuple[str, ...] = ()
    completion_criteria: tuple[str, ...] = ()
    checkpoints: tuple[str, ...] = ()
    revision_triggers: tuple[str, ...] = ()

    @property
    def sha256(self) -> str:
        """Return the content identity of the canonical plan."""
        return hashlib.sha256(self.to_json().encode()).hexdigest()

    def as_dict(self) -> dict[str, str | list[str]]:
        """Return the stable JSON representation."""
        result: dict[str, str | list[str]] = {"objective": self.objective}
        for field_name in _PLAN_FIELDS:
            result[field_name] = list(getattr(self, field_name))
        return result

    def to_json(self) -> str:
        """Serialize the plan canonically for provenance and export."""
        return json.dumps(self.as_dict(), ensure_ascii=False, sort_keys=True, separators=(",", ":"))

    def render(self) -> str:
        """Render a compact plan suitable for a terminal or model prompt."""
        labels = {
            "formalization": "Formalization",
            "observations": "Examples and special cases",
            "invariants": "Invariants",
            "obstructions": "Obstructions to test",
            "reductions": "Reductions",
            "premises": "Premises to verify",
            "methods": "Candidate methods",
            "partial_results": "Partial results worth retaining",
            "risks": "Risks and open boundaries",
            "completion_criteria": "Completion criteria",
            "checkpoints": "Checkpoints",
            "revision_triggers": "Revise the plan when",
        }
        lines = [f"Objective: {self.objective}"]
        for field_name in _PLAN_FIELDS:
            values = getattr(self, field_name)
            if not values:
                continue
            lines.append(f"\n{labels[field_name]}:")
            lines.extend(f"- {value}" for value in values)
        return "\n".join(lines)


def generate_proof_plan(
    statement: str,
    llm_generate: GenerateFn,
    *,
    guidance: tuple[str, ...] = (),
    context: str = "",
    max_repairs: int = 1,
    on_repair: Callable[[int, str], None] | None = None,
    on_response: Callable[[PlanAttempt], None] | None = None,
) -> ProofPlan:
    """Generate one concise strategy for a statement and explicit guidance."""
    statement = " ".join(statement.split())
    if not statement:
        raise ProofStrategyError("statement must not be empty")
    guidance_text = "\n".join(f"- {' '.join(item.split())}" for item in guidance) or "(none)"
    context_text = context.strip() or "(none)"
    if max_repairs < 0 or max_repairs > 3:
        raise ProofStrategyError("strategy repair budget must be between 0 and 3")
    try:
        response = llm_generate(
            _SYSTEM_PROMPT,
            _USER_PROMPT.format(
                statement=statement,
                guidance=guidance_text,
                context=context_text,
                max_items=_MAX_ITEMS,
                max_item_chars=_MAX_ITEM_CHARS,
            ),
        )
        for repair in range(max_repairs + 1):
            try:
                plan = parse_proof_plan(response.text)
            except ProofStrategyError as error:
                if on_response is not None:
                    on_response(
                        _plan_attempt(
                            response,
                            attempt=repair + 1,
                            guidance=guidance,
                            validation_error=str(error),
                        )
                    )
                if repair == max_repairs:
                    raise
                if on_repair is not None:
                    on_repair(repair + 1, str(error))
                response = llm_generate(
                    _SYSTEM_PROMPT,
                    _REPAIR_PROMPT.format(
                        error=error,
                        response=response.text[:_MAX_PLAN_CHARS],
                        max_items=_MAX_ITEMS,
                        max_item_chars=_MAX_ITEM_CHARS,
                    ),
                )
            else:
                if on_response is not None:
                    on_response(
                        _plan_attempt(
                            response,
                            attempt=repair + 1,
                            guidance=guidance,
                        )
                    )
                return plan
    except LLMError as error:
        raise ProofStrategyError(f"strategy generation failed: {error}") from error
    raise ProofStrategyError("strategy generation produced no response")


def parse_proof_plan(raw: str) -> ProofPlan:
    """Parse and bound one model-produced JSON strategy."""
    text = raw.strip()
    if len(text) > _MAX_RESPONSE_CHARS:
        raise ProofStrategyError(f"strategy response exceeds {_MAX_RESPONSE_CHARS} characters")
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise ProofStrategyError(f"strategy is not valid JSON: {error.msg}") from error
    if not isinstance(payload, dict):
        raise ProofStrategyError("strategy must be one JSON object")

    expected_fields = {"objective", *_PLAN_FIELDS}
    actual_fields = set(payload)
    if actual_fields != expected_fields:
        missing = ", ".join(sorted(expected_fields - actual_fields)) or "none"
        unexpected = ", ".join(sorted(actual_fields - expected_fields)) or "none"
        raise ProofStrategyError(f"strategy fields differ; missing: {missing}; unexpected: {unexpected}")

    objective = _bounded_text(payload.get("objective"), "objective")
    values: dict[str, tuple[str, ...]] = {}
    for field_name in _PLAN_FIELDS:
        raw_items = payload.get(field_name, [])
        if not isinstance(raw_items, list):
            raise ProofStrategyError(f"{field_name} must be an array")
        if len(raw_items) > _MAX_ITEMS:
            raise ProofStrategyError(f"{field_name} exceeds {_MAX_ITEMS} items")
        values[field_name] = tuple(_bounded_text(item, f"{field_name} item") for item in raw_items)
    plan = ProofPlan(objective=objective, **values)
    if len(plan.to_json()) > _MAX_PLAN_CHARS:
        raise ProofStrategyError(f"strategy exceeds {_MAX_PLAN_CHARS} characters")
    return plan


def _plan_attempt(
    response: LLMResponse,
    *,
    attempt: int,
    guidance: tuple[str, ...],
    validation_error: str = "",
) -> PlanAttempt:
    """Capture one provider response without normalizing its text."""
    return PlanAttempt(
        attempt=attempt,
        guidance=guidance,
        response=response.text,
        model=response.model,
        input_tokens=response.input_tokens,
        output_tokens=response.output_tokens,
        duration_seconds=response.duration_seconds,
        validation_error=validation_error,
    )


def _bounded_text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ProofStrategyError(f"{label} must be text")
    text: str = " ".join(value.split())
    if not text:
        raise ProofStrategyError(f"{label} must not be empty")
    if len(text) > _MAX_ITEM_CHARS:
        raise ProofStrategyError(f"{label} exceeds {_MAX_ITEM_CHARS} characters")
    return text
