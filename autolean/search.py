"""Mathlib lemma search — query Loogle, LeanSearch, and local tools.

Before asking the LLM for a proof, the agent searches for relevant
lemmas and includes them in the prompt context. This dramatically
improves proof quality by giving the LLM concrete building blocks.

Search backends:
  1. Loogle (loogle.lean-lang.org) — type-pattern search
  2. LeanSearch (leansearch.net) — natural language search
  3. Local lean-lsp MCP tools (if available)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger("autolean")

LOOGLE_URL = "https://loogle.lean-lang.org/json"
LEANSEARCH_URL = "https://leansearch.net/api/v1/search"


@dataclass
class SearchResult:
    """A single lemma search result."""

    name: str
    type_sig: str  # e.g., "∀ (n : Nat), 0 + n = n"
    source: str  # "loogle" | "leansearch"


def search_loogle(query: str, max_results: int = 5, timeout: float = 10.0) -> list[SearchResult]:
    """Search mathlib by type pattern via Loogle.

    Examples:
        "Nat.add_comm"           — find by name
        "_ + _ = _ + _"          — find by type pattern
        "(?a → ?b) → List ?a → List ?b"  — polymorphic pattern
        "List.reverse"           — partial name
    """
    try:
        resp = httpx.get(
            LOOGLE_URL,
            params={"q": query},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        for hit in data.get("hits", [])[:max_results]:
            name = hit.get("name", "")
            type_sig = hit.get("type", "")
            if name:
                results.append(SearchResult(name=name, type_sig=type_sig, source="loogle"))

        log.debug("Loogle: %d results for '%s'", len(results), query)
        return results

    except Exception as e:
        log.debug("Loogle search failed: %s", e)
        return []


def search_leansearch(query: str, max_results: int = 5, timeout: float = 10.0) -> list[SearchResult]:
    """Search mathlib by natural language via LeanSearch.

    Examples:
        "sum of two even numbers is even"
        "reverse of reversed list is identity"
        "Cauchy-Schwarz inequality"
    """
    try:
        resp = httpx.get(
            LEANSEARCH_URL,
            params={"query": query, "num_results": max_results},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        for hit in data if isinstance(data, list) else data.get("results", []):
            name = hit.get("name", "") or hit.get("formal_name", "")
            type_sig = hit.get("type", "") or hit.get("formal_statement", "")
            if name:
                results.append(SearchResult(name=name, type_sig=type_sig, source="leansearch"))

        log.debug("LeanSearch: %d results for '%s'", len(results), query)
        return results

    except Exception as e:
        log.debug("LeanSearch failed: %s", e)
        return []


def search_relevant_lemmas(
    goal_state: str,
    theorem_name: str = "",
    max_results: int = 8,
) -> list[SearchResult]:
    """Search for lemmas relevant to the current proof goal.

    Uses both Loogle (type-based) and LeanSearch (natural language) and
    merges results.
    """
    results: list[SearchResult] = []
    seen: set[str] = set()

    # Extract key types/patterns from goal state for Loogle
    if goal_state:
        # Try searching by the goal's conclusion
        goal_lines = goal_state.strip().split("\n")
        # The last line starting with ⊢ or |- is the conclusion
        conclusion = ""
        for line in reversed(goal_lines):
            stripped = line.strip()
            if stripped.startswith("⊢") or stripped.startswith("|-"):
                conclusion = stripped.lstrip("⊢|- ").strip()
                break

        if conclusion:
            # Loogle: search by type pattern
            for r in search_loogle(conclusion, max_results=4):
                if r.name not in seen:
                    results.append(r)
                    seen.add(r.name)

    # LeanSearch: natural language query from theorem name + goal
    nl_query = theorem_name.replace("_", " ")
    if goal_state:
        # Add goal context
        nl_query += " " + goal_state[:200]

    for r in search_leansearch(nl_query, max_results=4):
        if r.name not in seen:
            results.append(r)
            seen.add(r.name)

    log.info("Found %d relevant lemmas for %s", len(results), theorem_name or "goal")
    return results[:max_results]


def format_search_results_for_prompt(results: list[SearchResult]) -> str:
    """Format search results as a prompt section for the LLM."""
    if not results:
        return ""

    lines = ["## Relevant Mathlib Lemmas (from search)"]
    for r in results:
        sig = r.type_sig[:150] if r.type_sig else ""
        lines.append(f"- `{r.name}` : {sig}")

    return "\n".join(lines)
