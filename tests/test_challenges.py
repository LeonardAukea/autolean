"""Truthfulness and source-policy tests for curated challenges."""

from __future__ import annotations

from autolean.challenges import (
    OPEN_PROBLEMS,
    match_open_problem,
    render_challenge_source,
    render_research_brief,
    search_problems,
    suggest_problems,
)
from autolean.scanner import count_sorries


def test_curated_challenges_declare_no_axioms() -> None:
    for problem in OPEN_PROBLEMS:
        source = render_challenge_source(problem)
        assert "\naxiom " not in source
        assert "import Mathlib" in source


def test_every_runnable_challenge_has_an_explicit_proof_target() -> None:
    runnable = [problem for problem in OPEN_PROBLEMS if problem.formalization_status == "formalized"]
    assert runnable
    for problem in runnable:
        assert count_sorries(render_challenge_source(problem)) > 0


def test_problem_search_includes_boundaries_and_tags() -> None:
    filling = search_problems("surrogate invariant")
    number_theory = search_problems("number theory")

    assert [problem.id for problem in filling] == ["filling-area"]
    assert any(problem.id == "collatz" for problem in number_theory)


def test_open_problem_match_is_exact_and_whitespace_tolerant() -> None:
    assert match_open_problem("  Riemann hypothesis\n").id == "riemann"  # type: ignore[union-attr]
    assert match_open_problem("collatz").id == "collatz"  # type: ignore[union-attr]
    assert match_open_problem("a theorem about zeta") is None


def test_problem_suggestions_prefer_formalized_bounded_work() -> None:
    suggestions = suggest_problems(limit=3)

    assert suggestions
    assert all(problem.formalization_status == "formalized" for problem in suggestions)


def test_research_brief_makes_source_fidelity_a_required_step() -> None:
    scaffold = next(problem for problem in OPEN_PROBLEMS if problem.id == "filling-area")
    brief = render_research_brief(scaffold)

    assert scaffold.description in brief
    assert scaffold.limitations in brief
    assert "primary source" in brief.casefold()
    assert "without a surrogate invariant" in brief


def test_semantic_surrogates_are_marked_as_scaffolds() -> None:
    scaffold_ids = {problem.id for problem in OPEN_PROBLEMS if problem.formalization_status == "scaffold"}
    assert scaffold_ids == {
        "growth-gap",
        "filling-area",
        "poincare-higher",
        "riemann",
        "lonely-runner",
    }
    for problem in OPEN_PROBLEMS:
        if problem.formalization_status == "scaffold":
            assert problem.limitations
