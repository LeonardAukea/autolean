"""Placement and cache invariants for proof-context research."""

from __future__ import annotations

from pathlib import Path

import pytest

from autolean.proof_loop import ProofContextBuilder
from autolean.scanner import SorryTarget
from autolean.search import LemmaSearchReport, SearchResult


def _target(tmp_path: Path) -> SorryTarget:
    return SorryTarget(
        file=tmp_path / "Target.lean",
        line=2,
        col=2,
        decl_name="target",
        decl_line=1,
        context_before="theorem target : True := by",
        context_after="",
    )


def test_local_research_sends_no_query_to_network_services(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queried = False

    def search(goal: str, name: str) -> LemmaSearchReport:
        del goal, name
        nonlocal queried
        queried = True
        return LemmaSearchReport(())

    monkeypatch.setattr("autolean.search.search_relevant_lemmas", search)
    emitted: list[str] = []
    builder = ProofContextBuilder(tmp_path, lambda message, style: emitted.append(message))

    result = builder._semantic_search(
        _target(tmp_path),
        "⊢ True",
        remote_search=False,
    )

    assert result == ""
    assert queried is False
    assert any("disabled" in message for message in emitted)


def test_remote_research_queries_once_and_reuses_exact_evidence(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queries = 0

    def search(goal: str, name: str) -> LemmaSearchReport:
        assert (goal, name) == ("⊢ True", "target")
        nonlocal queries
        queries += 1
        return LemmaSearchReport((SearchResult("True.intro", "True", "loogle"),))

    monkeypatch.setattr("autolean.search.search_relevant_lemmas", search)
    builder = ProofContextBuilder(tmp_path, lambda message, style: None)
    target = _target(tmp_path)

    first = builder._semantic_search(target, "⊢ True", remote_search=True)
    second = builder._semantic_search(target, "⊢ False", remote_search=True)

    assert "True.intro" in first
    assert second == first
    assert queries == 1


def test_remote_research_records_service_unavailability(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "autolean.search.search_relevant_lemmas",
        lambda goal, name: LemmaSearchReport(
            (),
            ("Loogle unavailable: ConnectError",),
        ),
    )
    builder = ProofContextBuilder(tmp_path, lambda message, style: None)

    result = builder._semantic_search(
        _target(tmp_path),
        "⊢ True",
        remote_search=True,
    )

    assert "Remote research availability" in result
    assert "Loogle unavailable: ConnectError" in result


def test_source_invalidation_discards_remote_and_local_context(
    tmp_path: Path,
) -> None:
    builder = ProofContextBuilder(tmp_path, lambda message, style: None)
    target_id = _target(tmp_path).id
    builder._search_cache[target_id] = "remote"
    builder._searched.add(target_id)
    builder._indexed_cache[target_id] = "local"
    builder._indexed_sha256[target_id] = "a" * 64

    builder.invalidate_target(target_id)

    assert target_id not in builder._search_cache
    assert target_id not in builder._searched
    assert target_id not in builder._indexed_cache
    assert target_id not in builder._indexed_sha256
