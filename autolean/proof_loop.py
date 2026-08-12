"""Bounded search and strategy context for one Lean proof attempt."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from autolean.code_search import CodeDBSearchProvider
from autolean.llm import GenerateFn
from autolean.routing import EscalationDecision, EscalationRouter, ModelTransition
from autolean.scanner import SorryTarget, difficulty_score
from autolean.strategy import PlanAttempt, ProofPlan, ProofStrategyError, generate_proof_plan

__all__ = [
    "EscalationDecision",
    "EscalationRouter",
    "ModelTransition",
    "ProofContextBuilder",
    "ProofContextError",
]

Emit = Callable[[str, str], None]


class ProofContextError(ValueError):
    """The model could not produce valid context for a proof attempt."""


@dataclass(frozen=True)
class ProofContext:
    """Prompt text and content identities used by one proof attempt."""

    text: str
    indexed_sha256: str
    strategy_sha256: str
    strategy_response_sha256: str


class ProofContextBuilder:
    """Cache search results and one accepted model strategy per target."""

    def __init__(self, project_root: Path, emit: Emit) -> None:
        self.project_root = project_root
        self.emit = emit
        self.code_search = CodeDBSearchProvider()
        self._search_cache: dict[str, str] = {}
        self._indexed_cache: dict[str, str] = {}
        self._indexed_sha256: dict[str, str] = {}
        self._strategy_cache: dict[str, ProofPlan] = {}
        self._strategy_response_sha256: dict[str, str] = {}

    def invalidate_strategy(self, target_id: str) -> None:
        """Require the active model to plan again after a model transition."""
        self._strategy_cache.pop(target_id, None)
        self._strategy_response_sha256.pop(target_id, None)

    def build(
        self,
        target: SorryTarget,
        goal_state: str,
        attempt: int,
        *,
        structural_quality: str,
        local_references: tuple[str, ...],
        strategy_hints: tuple[str, ...],
        llm_generate: GenerateFn,
    ) -> ProofContext:
        """Return cached search context and one model-produced proof plan."""
        search = self._semantic_search(target, goal_state, attempt)
        indexed = self._indexed_search(target, goal_state, attempt)
        plan = self._strategy_cache.get(target.id)
        if plan is None:
            responses: list[PlanAttempt] = []
            declaration = target.qualified_decl_name or target.decl_name
            context = _planning_context(
                goal_state,
                structural_quality=structural_quality,
                local_references=local_references,
                search=search,
                indexed=indexed,
            )
            self.emit("Requesting a mathematical proof strategy from the model...", "magenta")
            try:
                plan = generate_proof_plan(
                    f"Close the Lean declaration `{declaration}` with goal `{goal_state}`",
                    llm_generate,
                    guidance=strategy_hints,
                    context=context,
                    on_repair=lambda repair, error: self.emit(
                        f"Strategy response rejected ({error}); requesting repair {repair}/1",
                        "yellow",
                    ),
                    on_response=responses.append,
                )
            except ProofStrategyError as error:
                raise ProofContextError(str(error)) from error
            accepted = responses[-1]
            self._strategy_cache[target.id] = plan
            self._strategy_response_sha256[target.id] = accepted.response_sha256
            self.emit(
                f"Model strategy response: {accepted.model}, sha256:{accepted.response_sha256[:12]}",
                "magenta",
            )
        self.emit(
            f"Strategy: {len(plan.methods)} methods, sha256:{plan.sha256[:12]}",
            "magenta",
        )
        parts = [part for part in (search, indexed) if part]
        parts.append(f"## Proof strategy (advisory)\n{plan.render()}")
        return ProofContext(
            text="\n\n".join(parts),
            indexed_sha256=self._indexed_sha256.get(target.id, ""),
            strategy_sha256=plan.sha256,
            strategy_response_sha256=self._strategy_response_sha256.get(target.id, ""),
        )

    def _semantic_search(self, target: SorryTarget, goal_state: str, attempt: int) -> str:
        if attempt != 1:
            return self._search_cache.get(target.id, "")

        from autolean.search import (
            format_arxiv_for_prompt,
            format_search_results_for_prompt,
            search_arxiv,
            search_relevant_lemmas,
        )

        self.emit("Searching mathlib (Loogle + LeanSearch)...", "dim")
        results = search_relevant_lemmas(goal_state, target.decl_name)
        parts: list[str] = []
        if results:
            self.emit(f"Found {len(results)} relevant lemmas", "cyan")
            for result in results[:3]:
                self.emit(f"  {result.name}: {result.type_sig[:60]}", "dim")
            parts.append(format_search_results_for_prompt(results))
        else:
            self.emit("No relevant lemmas found in mathlib", "dim")

        if difficulty_score(target) >= 7:
            self.emit("Searching arXiv for relevant research...", "dim")
            papers = search_arxiv(target.decl_name.replace("_", " "), max_results=2)
            if papers:
                self.emit(f"Found {len(papers)} relevant papers", "cyan")
                for paper in papers:
                    self.emit(f"  {str(paper.get('title', ''))[:70]}", "dim")
                parts.append(format_arxiv_for_prompt(papers))

        combined = "\n\n".join(parts)
        if combined:
            self._search_cache[target.id] = combined
        return combined

    def _indexed_search(self, target: SorryTarget, goal_state: str, attempt: int) -> str:
        if attempt != 1:
            return self._indexed_cache.get(target.id, "")

        result = self.code_search.search(
            self.project_root,
            goal_state,
            target.decl_name,
        )
        self._indexed_sha256[target.id] = result.sha256
        if not result.text:
            self.emit(f"CodeDB: {result.unavailable_reason}", "dim")
            return ""

        rendered = result.render()
        self._indexed_cache[target.id] = rendered
        self.emit(
            f"CodeDB: {len(result.queries)} indexed local query terms",
            "cyan",
        )
        return rendered


def _planning_context(
    goal_state: str,
    *,
    structural_quality: str,
    local_references: tuple[str, ...],
    search: str,
    indexed: str,
) -> str:
    """Bound the independently derived facts supplied to the planner."""
    parts = [
        f"Exact Lean goal:\n{goal_state or '(unavailable)'}",
        f"Tree-sitter parse quality: {structural_quality}",
    ]
    if local_references:
        parts.append("Local declarations:\n" + "\n".join(f"- {name}" for name in local_references[:8]))
    if search:
        parts.append(search[:4_000])
    if indexed:
        parts.append(indexed[:4_000])
    return "\n\n".join(parts)[:10_000]
