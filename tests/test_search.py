"""Tests for mathlib lemma search."""

from __future__ import annotations

import httpx
import respx

from autolean.search import (
    ARXIV_API_URL,
    LEANSEARCH_URL,
    LOOGLE_URL,
    SearchResult,
    format_search_results_for_prompt,
    search_arxiv,
    search_leansearch,
    search_loogle,
)


class TestFormatSearchResults:
    def test_empty_results(self) -> None:
        assert format_search_results_for_prompt([]) == ""

    def test_single_result(self) -> None:
        results = [
            SearchResult(name="Nat.add_comm", type_sig="∀ (n m : Nat), n + m = m + n", source="loogle")
        ]
        text = format_search_results_for_prompt(results)
        assert "Nat.add_comm" in text
        assert "Relevant Mathlib Lemmas" in text

    def test_multiple_results(self) -> None:
        results = [
            SearchResult(name="Nat.add_comm", type_sig="n + m = m + n", source="loogle"),
            SearchResult(name="Nat.add_assoc", type_sig="(n + m) + k = n + (m + k)", source="leansearch"),
        ]
        text = format_search_results_for_prompt(results)
        assert "Nat.add_comm" in text
        assert "Nat.add_assoc" in text

    def test_truncates_long_types(self) -> None:
        results = [SearchResult(name="foo", type_sig="x" * 500, source="loogle")]
        text = format_search_results_for_prompt(results)
        assert len(text) < 300  # truncated


@respx.mock
def test_loogle_validates_response_shape() -> None:
    respx.get(LOOGLE_URL).mock(return_value=httpx.Response(200, json=["wrong shape"]))
    assert search_loogle("Nat.add_comm") == []


@respx.mock
def test_loogle_parses_typed_hits() -> None:
    respx.get(LOOGLE_URL).mock(
        return_value=httpx.Response(
            200,
            json={"hits": [{"name": "Nat.add_comm", "type": "∀ a b, a + b = b + a"}]},
        )
    )
    assert search_loogle("Nat.add_comm") == [
        SearchResult(
            name="Nat.add_comm",
            type_sig="∀ a b, a + b = b + a",
            source="loogle",
        )
    ]


@respx.mock
def test_leansearch_rejects_nested_malformed_result() -> None:
    respx.post(LEANSEARCH_URL).mock(return_value=httpx.Response(200, json=[[{"result": []}]]))
    assert search_leansearch("addition") == []


@respx.mock
def test_arxiv_search_uses_atom_parser() -> None:
    xml = b"""\
<feed xmlns="http://www.w3.org/2005/Atom">
  <entry>
    <id>http://arxiv.org/abs/2604.07408v2</id>
    <title>  A Mathematical Result </title>
    <summary> A precise abstract. </summary>
    <author><name>Ada Lovelace</name></author>
  </entry>
</feed>
"""
    respx.get(ARXIV_API_URL).mock(return_value=httpx.Response(200, content=xml))

    papers = search_arxiv("result")

    assert papers == [
        {
            "title": "A Mathematical Result",
            "abstract": "A precise abstract.",
            "arxiv_id": "2604.07408v2",
            "authors": "Ada Lovelace",
            "url": "https://arxiv.org/abs/2604.07408v2",
        }
    ]
