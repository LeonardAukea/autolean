"""Mathlib lemma search — database lookups, NOT LLM calls.

Before asking the local LLM for a proof, the agent searches for relevant
lemmas and includes them in the prompt context. This dramatically
improves proof quality by giving the LLM concrete building blocks.

These are pure database/index queries — no LLM inference involved:
  1. Loogle (loogle.lean-lang.org) — type-pattern search over mathlib index
  2. LeanSearch (leansearch.net) — natural language search over mathlib
  3. Local lean-lsp MCP tools (if available)
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

log = logging.getLogger("autolean")

LOOGLE_URL = "https://loogle.lean-lang.org/json"
LEANSEARCH_URL = "https://leansearch.net/search"  # POST, query as list
ARXIV_API_URL = "https://export.arxiv.org/api/query"


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
    """Search mathlib by natural language via LeanSearch (POST API).

    Examples:
        "sum of two even numbers is even"
        "reverse of reversed list is identity"
        "Cauchy-Schwarz inequality"
    """
    try:
        resp = httpx.post(
            LEANSEARCH_URL,
            json={"query": [query], "num_results": max_results},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()

        results = []
        # Response is [[result1, result2, ...]] (list of lists)
        items = data[0] if data and isinstance(data[0], list) else data
        for hit in items[:max_results]:
            r = hit.get("result", hit) if isinstance(hit, dict) else {}
            name_parts = r.get("name", [])
            name = ".".join(name_parts) if isinstance(name_parts, list) else str(name_parts)
            type_sig = r.get("signature", "") or r.get("type", "")
            if name:
                results.append(SearchResult(name=name, type_sig=type_sig, source="leansearch"))

        log.debug("LeanSearch: %d results for '%s'", len(results), query)
        return results

    except Exception as e:
        log.debug("LeanSearch failed: %s", e)
        return []


def search_arxiv(query: str, max_results: int = 3, timeout: float = 15.0) -> list[dict]:
    """Search arXiv for relevant papers to aid proof formulation.

    Returns paper metadata (title, abstract, authors, URL).
    This is a database lookup — no LLM involved.
    """
    try:
        resp = httpx.get(
            ARXIV_API_URL,
            params={
                "search_query": f"all:{query}",
                "start": 0,
                "max_results": max_results,
                "sortBy": "relevance",
            },
            timeout=timeout,
        )
        resp.raise_for_status()

        # Parse Atom XML (minimal, no lxml dependency)
        import re
        papers = []
        for entry in re.findall(r"<entry>(.*?)</entry>", resp.text, re.DOTALL):
            title = re.search(r"<title>(.*?)</title>", entry, re.DOTALL)
            summary = re.search(r"<summary>(.*?)</summary>", entry, re.DOTALL)
            arxiv_id = re.search(r"<id>http://arxiv.org/abs/(.*?)</id>", entry)
            authors = re.findall(r"<name>(.*?)</name>", entry)

            if title:
                papers.append({
                    "title": title.group(1).strip().replace("\n", " "),
                    "abstract": (summary.group(1).strip()[:500] if summary else ""),
                    "arxiv_id": arxiv_id.group(1) if arxiv_id else "",
                    "authors": authors[:3],
                    "url": f"https://arxiv.org/abs/{arxiv_id.group(1)}" if arxiv_id else "",
                })

        log.debug("arXiv: %d results for '%s'", len(papers), query)
        return papers

    except Exception as e:
        log.debug("arXiv search failed: %s", e)
        return []


def format_arxiv_for_prompt(papers: list[dict]) -> str:
    """Format arXiv results as proof hints for the LLM."""
    if not papers:
        return ""
    lines = ["## Relevant Research (arXiv)"]
    for p in papers:
        lines.append(f"- [{p['title'][:80]}]({p['url']})")
        if p["abstract"]:
            lines.append(f"  {p['abstract'][:150]}...")
    return "\n".join(lines)


def search_relevant_lemmas(
    goal_state: str,
    theorem_name: str = "",
    max_results: int = 8,
) -> list[SearchResult]:
    """Search for lemmas relevant to the current proof goal.

    Strategy:
    1. Search by theorem name (often the lemma has a similar name in mathlib)
    2. Search by goal conclusion type pattern (Loogle)
    3. Search by natural language (LeanSearch)
    """
    results: list[SearchResult] = []
    seen: set[str] = set()

    # Strategy 1: Search by theorem name — often mathlib has exactly this
    if theorem_name:
        # Convert our name to mathlib-style: list_reverse_append -> List.reverse_append
        parts = theorem_name.split("_")
        # Try capitalized prefix: "list_reverse_append" -> "List.reverse_append"
        if len(parts) >= 2:
            mathlib_name = parts[0].capitalize() + "." + "_".join(parts[1:])
            for r in search_loogle(mathlib_name, max_results=3):
                if r.name not in seen:
                    results.append(r)
                    seen.add(r.name)

    # Strategy 2: Search by goal conclusion type pattern (Loogle)
    if goal_state:
        goal_lines = goal_state.strip().split("\n")
        conclusion = ""
        for line in reversed(goal_lines):
            stripped = line.strip()
            if stripped.startswith("⊢") or stripped.startswith("|-"):
                conclusion = stripped.lstrip("⊢|- ").strip()
                break

        if conclusion:
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
    """Format search results as a prompt section for the LLM.

    If a lemma looks like it directly closes the goal, highlight it
    with explicit tactic suggestions (exact, simp, rw).
    """
    if not results:
        return ""

    lines = ["## Relevant Mathlib Lemmas (from search)"]
    lines.append("TRY THESE FIRST before writing a manual proof:")
    for r in results:
        sig = r.type_sig[:150] if r.type_sig else ""
        lines.append(f"- `{r.name}` : {sig}")
        # Suggest concrete tactics for each lemma
        lines.append(f"  Try: `exact {r.name}` or `simp [{r.name}]` or `rw [{r.name}]`")

    return "\n".join(lines)
