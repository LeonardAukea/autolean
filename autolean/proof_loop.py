"""Bounded search and strategy context for one Lean proof attempt."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from autolean.code_search import CodeDBSearchProvider
from autolean.llm import GenerateFn
from autolean.routing import EscalationDecision, EscalationRouter, ModelTransition
from autolean.scanner import SorryTarget, difficulty_score
from autolean.strategy import PlanAttempt, ProofPlan, ProofStrategyError, generate_proof_plan

if TYPE_CHECKING:
    from autolean.search import SearchResult

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
    search_sha256: str
    indexed_sha256: str
    strategy_sha256: str
    strategy_response_sha256: str
    remote_search: bool

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("proof context text must not be empty")
        digests = (
            self.search_sha256,
            self.indexed_sha256,
            self.strategy_sha256,
            self.strategy_response_sha256,
        )
        if any(
            not isinstance(digest, str) or (digest and re.fullmatch(r"[0-9a-f]{64}", digest) is None)
            for digest in digests
        ):
            raise ValueError("proof context digests must be lowercase SHA-256 values")
        if not self.strategy_sha256 or not self.strategy_response_sha256:
            raise ValueError("proof context must identify its accepted strategy")
        if not isinstance(self.remote_search, bool):
            raise ValueError("proof context search placement must be a boolean")


class ProofContextBuilder:
    """Cache search results and one accepted model strategy per target."""

    def __init__(self, project_root: Path, emit: Emit) -> None:
        if not isinstance(project_root, Path):
            raise ValueError("proof context project root must be a path")
        if not callable(emit):
            raise ValueError("proof context emitter must be callable")
        self.project_root = project_root.resolve()
        self.emit = emit
        self.code_search = CodeDBSearchProvider()
        self._search_cache: dict[str, str] = {}
        self._searched: set[str] = set()
        self._indexed_cache: dict[str, str] = {}
        self._indexed_sha256: dict[str, str] = {}
        self._strategy_cache: dict[str, ProofPlan] = {}
        self._strategy_response_sha256: dict[str, str] = {}

    def invalidate_strategy(self, target_id: str) -> None:
        """Require the active model to plan again after a model transition."""
        self._strategy_cache.pop(target_id, None)
        self._strategy_response_sha256.pop(target_id, None)

    def invalidate_target(self, target_id: str) -> None:
        """Discard every context value derived from one source target."""
        self._search_cache.pop(target_id, None)
        self._searched.discard(target_id)
        self._indexed_cache.pop(target_id, None)
        self._indexed_sha256.pop(target_id, None)
        self.invalidate_strategy(target_id)

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
        remote_search: bool,
    ) -> ProofContext:
        """Return cached search context and one model-produced proof plan."""
        search = self._semantic_search(
            target,
            goal_state,
            remote_search=remote_search,
        )
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
            search_sha256=(hashlib.sha256(search.encode()).hexdigest() if search else ""),
            indexed_sha256=self._indexed_sha256.get(target.id, ""),
            strategy_sha256=plan.sha256,
            strategy_response_sha256=self._strategy_response_sha256.get(target.id, ""),
            remote_search=remote_search,
        )

    def _semantic_search(
        self,
        target: SorryTarget,
        goal_state: str,
        *,
        remote_search: bool,
    ) -> str:
        if not remote_search:
            self.emit("Remote research disabled for this model placement", "dim")
            return ""
        if target.id in self._searched:
            return self._search_cache.get(target.id, "")
        self._searched.add(target.id)

        from autolean.search import (
            search_arxiv,
            search_relevant_lemmas,
        )

        self.emit("Searching mathlib (Loogle + LeanSearch)...", "dim")
        report = search_relevant_lemmas(goal_state, target.decl_name)
        unavailable = list(report.unavailable)
        parts = [self._lemma_evidence(report.results)]

        if difficulty_score(target) >= 7:
            self.emit("Searching arXiv for relevant research...", "dim")
            papers = search_arxiv(
                target.decl_name.replace("_", " "),
                max_results=2,
                on_unavailable=unavailable.append,
            )
            parts.append(self._paper_evidence(papers))

        parts.append(self._availability_evidence(unavailable))

        combined = "\n\n".join(part for part in parts if part)
        if combined:
            self._search_cache[target.id] = combined
        return combined

    def _lemma_evidence(self, results: Sequence[SearchResult]) -> str:
        """Render and report one ranked set of Mathlib candidates."""
        from autolean.search import format_search_results_for_prompt

        if not results:
            self.emit("No relevant lemmas found in mathlib", "dim")
            return ""
        self.emit(f"Found {len(results)} relevant lemmas", "cyan")
        for result in results[:3]:
            self.emit(f"  {result.name}: {result.type_sig[:60]}", "dim")
        return format_search_results_for_prompt(results)

    def _paper_evidence(self, papers: list[dict[str, str]]) -> str:
        """Render and report one ranked set of arXiv candidates."""
        from autolean.search import format_arxiv_for_prompt

        if not papers:
            return ""
        self.emit(f"Found {len(papers)} relevant papers", "cyan")
        for paper in papers:
            self.emit(f"  {paper.get('title', '')[:70]}", "dim")
        return format_arxiv_for_prompt(papers)

    def _availability_evidence(self, unavailable: Sequence[str]) -> str:
        """Render unique remote-service failures as prompt evidence."""
        failures = tuple(dict.fromkeys(unavailable))
        for reason in failures:
            self.emit(reason, "yellow")
        if not failures:
            return ""
        return "## Remote research availability\n" + "\n".join(f"- {reason}" for reason in failures)

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
