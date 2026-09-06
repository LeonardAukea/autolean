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
import math
import xml.etree.ElementTree as ET
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Any

import httpx

from autolean.validation import require_int, require_texts

log = logging.getLogger("autolean")

LOOGLE_URL = "https://loogle.lean-lang.org/json"
LEANSEARCH_URL = "https://leansearch.net/search"  # POST, query as list
ARXIV_API_URL = "https://export.arxiv.org/api/query"


@dataclass(frozen=True)
class SearchResult:
    """A single lemma search result."""

    name: str
    type_sig: str  # e.g., "∀ (n : Nat), 0 + n = n"
    source: str  # "loogle" | "leansearch"

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value.strip() for value in (self.name, self.type_sig)):
            raise ValueError("search result identity must be complete")
        if self.source not in {"loogle", "leansearch"}:
            raise ValueError("search result source is invalid")


@dataclass(frozen=True)
class LemmaSearchReport:
    """Lemma candidates and observable remote-service failures."""

    results: tuple[SearchResult, ...]
    unavailable: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.results, tuple) or any(
            not isinstance(result, SearchResult) for result in self.results
        ):
            raise ValueError("lemma search report contains invalid results")
        if not isinstance(self.unavailable, tuple) or any(
            not isinstance(reason, str) or not reason.strip() for reason in self.unavailable
        ):
            raise ValueError("lemma search failures must be non-empty text")


class SearchPayloadError(ValueError):
    """A search service returned a response outside its documented shape."""


def _validate_search_request(query: str, max_results: int, timeout: float) -> None:
    if not isinstance(query, str) or not query.strip() or len(query) > 2_000:
        raise ValueError("search query must contain at most 2000 characters")
    if isinstance(max_results, bool) or not isinstance(max_results, int) or not 1 <= max_results <= 50:
        raise ValueError("search result limit must be between 1 and 50")
    if (
        isinstance(timeout, bool)
        or not isinstance(timeout, (int, float))
        or not math.isfinite(timeout)
        or timeout <= 0
    ):
        raise ValueError("search timeout must be finite and positive")


def _json_payload(response: httpx.Response) -> Any:
    try:
        return response.json()
    except json.JSONDecodeError as e:
        raise SearchPayloadError(f"search response is not JSON: {e}") from e


def search_loogle(
    query: str,
    max_results: int = 5,
    timeout: float = 10.0,
    *,
    on_unavailable: Callable[[str], None] | None = None,
) -> list[SearchResult]:
    """Search mathlib by type pattern via Loogle.

    Examples:
        "Nat.add_comm"           — find by name
        "_ + _ = _ + _"          — find by type pattern
        "(?a → ?b) → List ?a → List ?b"  — polymorphic pattern
        "List.reverse"           — partial name
    """
    _validate_search_request(query, max_results, timeout)
    if on_unavailable is not None and not callable(on_unavailable):
        raise ValueError("search failure observer must be callable")
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
        if on_unavailable is not None:
            on_unavailable(f"Loogle unavailable: {type(e).__name__}")
        return []


def search_leansearch(
    query: str,
    max_results: int = 5,
    timeout: float = 10.0,
    *,
    on_unavailable: Callable[[str], None] | None = None,
) -> list[SearchResult]:
    """Search mathlib by natural language via LeanSearch (POST API).

    Examples:
        "sum of two even numbers is even"
        "reverse of reversed list is identity"
        "Cauchy-Schwarz inequality"
    """
    _validate_search_request(query, max_results, timeout)
    if on_unavailable is not None and not callable(on_unavailable):
        raise ValueError("search failure observer must be callable")
    try:
        resp = httpx.post(
            LEANSEARCH_URL,
            json={"query": [query], "num_results": max_results},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = _json_payload(resp)
        results = _parse_leansearch_results(data, max_results)

        log.debug("LeanSearch: %d results for '%s'", len(results), query)
        return results

    except (httpx.HTTPError, SearchPayloadError) as e:
        log.debug("LeanSearch failed: %s", e)
        if on_unavailable is not None:
            on_unavailable(f"LeanSearch unavailable: {type(e).__name__}")
        return []


def _parse_leansearch_results(data: Any, max_results: int) -> list[SearchResult]:
    """Decode the two response envelopes exposed by LeanSearch."""
    if not isinstance(data, list):
        raise SearchPayloadError("LeanSearch response is not a list")
    items: list[Any] = data[0] if data and isinstance(data[0], list) else data
    return [result for hit in items[:max_results] if (result := _parse_leansearch_hit(hit)) is not None]


def _parse_leansearch_hit(hit: Any) -> SearchResult | None:
    """Decode one LeanSearch hit into the shared result vocabulary."""
    if not isinstance(hit, dict):
        raise SearchPayloadError("LeanSearch returned a malformed hit")
    result = hit.get("result", hit)
    if not isinstance(result, dict):
        raise SearchPayloadError("LeanSearch result is not an object")
    name = _leansearch_name(result.get("name", []))
    type_sig = result.get("signature", "") or result.get("type", "")
    if not isinstance(type_sig, str):
        raise SearchPayloadError("LeanSearch signature is not a string")
    if not name:
        return None
    return SearchResult(name=name, type_sig=type_sig, source="leansearch")


def _leansearch_name(value: Any) -> str:
    """Decode a dotted name from LeanSearch's string or segment form."""
    if isinstance(value, str):
        return value
    if isinstance(value, list) and all(isinstance(part, str) for part in value):
        return ".".join(value)
    raise SearchPayloadError("LeanSearch name is not a string path")


def search_arxiv(
    query: str,
    max_results: int = 3,
    timeout: float = 15.0,
    *,
    on_unavailable: Callable[[str], None] | None = None,
) -> list[dict[str, str]]:
    """Search arXiv for relevant papers to aid proof formulation.

    Returns paper metadata (title, abstract, authors, URL).
    This is a database lookup — no LLM involved.
    """
    _validate_search_request(query, max_results, timeout)
    if on_unavailable is not None and not callable(on_unavailable):
        raise ValueError("search failure observer must be callable")
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
        if on_unavailable is not None:
            on_unavailable(f"arXiv unavailable: {type(e).__name__}")
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
) -> LemmaSearchReport:
    """Search for lemmas relevant to the current proof goal.

    Strategy:
    1. Search by theorem name (often the lemma has a similar name in mathlib)
    2. Search by goal conclusion type pattern (Loogle)
    3. Search by natural language (LeanSearch)
    """
    require_texts(
        (goal_state, theorem_name),
        "lemma search inputs must be text",
        allow_empty=True,
    )
    require_int(
        max_results,
        "lemma search result limit must be between 1 and 50",
        minimum=1,
        maximum=50,
    )
    if not goal_state.strip() and not theorem_name.strip():
        return LemmaSearchReport(())
    results: list[SearchResult] = []
    unavailable: list[str] = []
    seen: set[str] = set()

    _extend_unique(
        results,
        seen,
        _search_theorem_name(theorem_name, unavailable.append),
    )
    _extend_unique(
        results,
        seen,
        _search_goal_conclusion(goal_state, unavailable.append),
    )
    _extend_unique(
        results,
        seen,
        search_leansearch(
            _natural_language_query(theorem_name, goal_state),
            max_results=4,
            on_unavailable=unavailable.append,
        ),
    )

    log.info("Found %d relevant lemmas for %s", len(results), theorem_name or "goal")
    return LemmaSearchReport(
        tuple(results[:max_results]),
        tuple(dict.fromkeys(unavailable)),
    )


def _extend_unique(
    results: list[SearchResult],
    seen: set[str],
    candidates: Sequence[SearchResult],
) -> None:
    """Append candidates by declaration identity while preserving rank."""
    for result in candidates:
        if result.name not in seen:
            results.append(result)
            seen.add(result.name)


def _search_theorem_name(
    theorem_name: str,
    on_unavailable: Callable[[str], None],
) -> list[SearchResult]:
    """Map a snake-case declaration to a likely Mathlib dotted name."""
    parts = theorem_name.split("_") if theorem_name else []
    if len(parts) < 2:
        return []
    mathlib_name = (parts[0].capitalize() + "." + "_".join(parts[1:]))[:2_000]
    return search_loogle(
        mathlib_name,
        max_results=3,
        on_unavailable=on_unavailable,
    )


def _search_goal_conclusion(
    goal_state: str,
    on_unavailable: Callable[[str], None],
) -> list[SearchResult]:
    """Search the conclusion line of one Lean goal state."""
    conclusion = _goal_conclusion(goal_state)
    if not conclusion:
        return []
    return search_loogle(
        conclusion[:2_000],
        max_results=4,
        on_unavailable=on_unavailable,
    )


def _goal_conclusion(goal_state: str) -> str:
    """Return the final turnstile line from a Lean goal state."""
    for line in reversed(goal_state.strip().splitlines()):
        stripped = line.strip()
        if stripped.startswith(("⊢", "|-")):
            return stripped.lstrip("⊢|- ").strip()
    return ""


def _natural_language_query(theorem_name: str, goal_state: str) -> str:
    """Build one bounded LeanSearch query from declaration and goal text."""
    return f"{theorem_name.replace('_', ' ')[:1_799]} {goal_state[:200]}".strip()


def format_search_results_for_prompt(results: Sequence[SearchResult]) -> str:
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
        lines.append(f"  Try: `exact {r.name}` or `simp [{r.name}]` or `rw [{r.name}]`")

    return "\n".join(lines)
