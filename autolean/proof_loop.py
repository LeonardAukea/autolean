"""Bounded search and strategy context for one Lean proof attempt."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from autolean.code_search import CodeDBSearchProvider
from autolean.routing import EscalationDecision, EscalationRouter, ModelTransition
from autolean.scanner import SorryTarget, difficulty_score
from autolean.strategy import plan_for_lean_target

__all__ = [
    "EscalationDecision",
    "EscalationRouter",
    "ModelTransition",
    "ProofContextBuilder",
]

Emit = Callable[[str, str], None]


@dataclass(frozen=True)
class ProofContext:
    """Prompt text and identities produced by deterministic context tools."""

    text: str
    indexed_sha256: str
    strategy_sha256: str


class ProofContextBuilder:
    """Cache search results and produce one bounded prompt layer."""

    def __init__(self, project_root: Path, emit: Emit) -> None:
        self.project_root = project_root
        self.emit = emit
        self.code_search = CodeDBSearchProvider()
        self._search_cache: dict[str, str] = {}
        self._indexed_cache: dict[str, str] = {}
        self._indexed_sha256: dict[str, str] = {}

    def build(
        self,
        target: SorryTarget,
        goal_state: str,
        attempt: int,
        *,
        structural_quality: str,
        local_references: tuple[str, ...],
        strategy_hints: tuple[str, ...],
    ) -> ProofContext:
        """Return cached search context followed by a fresh proof plan."""
        search = self._semantic_search(target, goal_state, attempt)
        indexed = self._indexed_search(target, goal_state, attempt)
        plan = plan_for_lean_target(
            target.qualified_decl_name or target.decl_name,
            goal_state,
            structural_quality=structural_quality,
            local_references=local_references,
            strategy_hints=strategy_hints,
            indexed_context_available=bool(indexed),
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
