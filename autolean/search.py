"""Mathlib lemma search over Loogle, LeanSearch, and local lean-lsp indexes.

Results enter the prompt context as candidate lemmas before each proof
request:
  1. Loogle (loogle.lean-lang.org) — type-pattern search
  2. LeanSearch (leansearch.net) — natural-language search
  3. Local lean-lsp MCP tools when available
"""

from __future__ import annotations

import json
import logging
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Any

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


class SearchPayloadError(ValueError):
    """A search service returned a response outside its documented shape."""


def _json_payload(response: httpx.Response) -> Any:
    try:
        return response.json()
    except json.JSONDecodeError as e:
        raise SearchPayloadError(f"search response is not JSON: {e}") from e


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
        data = _json_payload(resp)
        if not isinstance(data, dict) or not isinstance(data.get("hits"), list):
            raise SearchPayloadError("Loogle response has no hits list")

        results = []
        for hit in data["hits"][:max_results]:
            if not isinstance(hit, dict):
                raise SearchPayloadError("Loogle returned a malformed hit")
            name = hit.get("name", "")
            type_sig = hit.get("type", "")
            if not isinstance(name, str) or not isinstance(type_sig, str):
                raise SearchPayloadError("Loogle hit fields are not strings")
            if name:
                results.append(SearchResult(name=name, type_sig=type_sig, source="loogle"))

        log.debug("Loogle: %d results for '%s'", len(results), query)
        return results

    except (httpx.HTTPError, SearchPayloadError) as e:
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
        data = _json_payload(resp)
        if not isinstance(data, list):
            raise SearchPayloadError("LeanSearch response is not a list")

        results = []
        # Response is [[result1, result2, ...]] (list of lists)
        items: list[Any] = data[0] if data and isinstance(data[0], list) else data
        for hit in items[:max_results]:
            if not isinstance(hit, dict):
                raise SearchPayloadError("LeanSearch returned a malformed hit")
            result = hit.get("result", hit)
            if not isinstance(result, dict):
                raise SearchPayloadError("LeanSearch result is not an object")
            name_parts = result.get("name", [])
            if isinstance(name_parts, list) and all(isinstance(part, str) for part in name_parts):
                name = ".".join(name_parts)
            elif isinstance(name_parts, str):
                name = name_parts
            else:
                raise SearchPayloadError("LeanSearch name is not a string path")
            type_sig = result.get("signature", "") or result.get("type", "")
            if not isinstance(type_sig, str):
                raise SearchPayloadError("LeanSearch signature is not a string")
            if name:
                results.append(SearchResult(name=name, type_sig=type_sig, source="leansearch"))

        log.debug("LeanSearch: %d results for '%s'", len(results), query)
        return results

    except (httpx.HTTPError, SearchPayloadError) as e:
        log.debug("LeanSearch failed: %s", e)
        return []


def search_arxiv(query: str, max_results: int = 3, timeout: float = 15.0) -> list[dict[str, str]]:
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

        root = ET.fromstring(resp.content)
        namespace = {"atom": "http://www.w3.org/2005/Atom"}
        papers = []
        for entry in root.findall("atom:entry", namespace):
            title = " ".join(entry.findtext("atom:title", "", namespace).split())
            summary = " ".join(entry.findtext("atom:summary", "", namespace).split())
            identifier = entry.findtext("atom:id", "", namespace)
            arxiv_id = identifier.rsplit("/", 1)[-1] if identifier else ""
            authors = [
                " ".join(name.text.split())
                for name in entry.findall("atom:author/atom:name", namespace)
                if name.text
            ]
            if title:
                papers.append(
                    {
                        "title": title,
                        "abstract": summary[:500],
                        "arxiv_id": arxiv_id,
                        "authors": ", ".join(authors[:3]),
                        "url": f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else "",
                    }
                )

        log.debug("arXiv: %d results for '%s'", len(papers), query)
        return papers

    except (httpx.HTTPError, ET.ParseError) as e:
        log.debug("arXiv search failed: %s", e)
        return []


def format_arxiv_for_prompt(papers: list[dict[str, str]]) -> str:
    """Format arXiv results as proof hints for the LLM."""
    if not papers:
        return ""
    lines = ["## Relevant Research (arXiv)"]
    for p in papers:
        title = p.get("title", "")
        url = p.get("url", "")
        abstract = p.get("abstract", "")
        lines.append(f"- [{title[:80]}]({url})")
        if abstract:
            lines.append(f"  {abstract[:150]}...")
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
        # Mathlib declarations use namespace-qualified dotted names.
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
