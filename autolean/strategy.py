"""Concise, reviewable mathematical proof strategies."""

from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

from autolean.llm import GenerateFn, LLMError

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
_MAX_ITEMS = 8
_MAX_ITEM_CHARS = 500
_MAX_PLAN_CHARS = 20_000

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

Every field after objective is an array of short strings.
"""


class ProofStrategyError(ValueError):
    """A model response cannot form a bounded proof strategy."""


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
) -> ProofPlan:
    """Generate one concise strategy for a statement and explicit guidance."""
    statement = " ".join(statement.split())
    if not statement:
        raise ProofStrategyError("statement must not be empty")
    guidance_text = "\n".join(f"- {' '.join(item.split())}" for item in guidance) or "(none)"
    context_text = context.strip() or "(none)"
    try:
        response = llm_generate(
            _SYSTEM_PROMPT,
            _USER_PROMPT.format(
                statement=statement,
                guidance=guidance_text,
                context=context_text,
            ),
        )
    except LLMError as error:
        raise ProofStrategyError(f"strategy generation failed: {error}") from error
    return parse_proof_plan(response.text)


def parse_proof_plan(raw: str) -> ProofPlan:
    """Parse and bound one model-produced JSON strategy."""
    text = raw.strip()
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1)
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as error:
        raise ProofStrategyError(f"strategy is not valid JSON: {error.msg}") from error
    if not isinstance(payload, dict):
        raise ProofStrategyError("strategy must be one JSON object")

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


def plan_for_lean_target(
    declaration: str,
    goal_state: str,
    *,
    structural_quality: str,
    local_references: tuple[str, ...] = (),
    strategy_hints: tuple[str, ...] = (),
    indexed_context_available: bool = False,
) -> ProofPlan:
    """Build the deterministic strategy checkpoint for one Lean target."""
    goal = " ".join(goal_state.split())[:_MAX_ITEM_CHARS] or "Goal state unavailable."
    methods = strategy_hints or _methods_for_goal(goal)
    premises = tuple(f"Check local declaration `{name}`." for name in local_references[:5])
    if indexed_context_available:
        premises = (*premises, "Review the indexed local-project matches.")
    risks = []
    if structural_quality != "complete":
        risks.append(f"Tree-sitter parse quality is {structural_quality}; confirm syntax with Lean.")
    if goal_state.strip() == "":
        risks.append("The Lean goal could not be extracted; infer it from the declaration context.")
    return ProofPlan(
        objective=f"Close every Lean goal in `{declaration}` without changing its statement.",
        formalization=(f"Exact current goal: {goal}",),
        observations=("Check simple constructors, computation, and contradiction cases first.",),
        invariants=("Preserve the exact declaration statement and its universe and typeclass context.",),
        obstructions=("Test proposed helper claims against small cases before relying on them.",),
        reductions=("Reduce the goal to independently checkable subgoals before using broad automation.",),
        premises=premises,
        methods=methods,
        partial_results=("Retain independently accepted helper lemmas that reduce the original goal.",),
        risks=tuple(risks),
        completion_criteria=("The declaration elaborates without placeholders or unapproved axioms.",),
        checkpoints=(
            "Elaborate the candidate in the pinned project closure.",
            "Audit the exact declaration range and transitive axioms.",
            "Install only the source bytes accepted by the sandbox.",
        ),
        revision_triggers=(
            "A required premise is absent from the pinned library closure.",
            "Two attempts repeat one kernel diagnostic without reducing the goal.",
        ),
    )


def _methods_for_goal(goal: str) -> tuple[str, ...]:
    methods: list[str] = []
    if re.search(r"\b\d+\b", goal) and "=" in goal:
        methods.append("Try computation or `norm_num` before algebraic automation.")
    if "∀" in goal or "→" in goal:
        methods.append("Introduce quantified variables and hypotheses explicitly.")
    if "∃" in goal:
        methods.append("Construct a concrete witness and prove each obligation separately.")
    if "=" in goal:
        methods.append("Try definitional equality, targeted simplification, then rewriting.")
    methods.append("Use a verified library premise with `exact` or `apply` when available.")
    return tuple(methods[:4])


def _bounded_text(value: Any, label: str) -> str:
    if not isinstance(value, str):
        raise ProofStrategyError(f"{label} must be text")
    text: str = " ".join(value.split())
    if not text:
        raise ProofStrategyError(f"{label} must not be empty")
    if len(text) > _MAX_ITEM_CHARS:
        raise ProofStrategyError(f"{label} exceeds {_MAX_ITEM_CHARS} characters")
    return text
