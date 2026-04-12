"""Tests for mathlib lemma search."""

from __future__ import annotations

from autolean.search import SearchResult, format_search_results_for_prompt


class TestFormatSearchResults:

    def test_empty_results(self) -> None:
        assert format_search_results_for_prompt([]) == ""

    def test_single_result(self) -> None:
        results = [SearchResult(name="Nat.add_comm", type_sig="∀ (n m : Nat), n + m = m + n", source="loogle")]
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
