"""Truthfulness and source-policy tests for curated challenges."""

from __future__ import annotations

from autolean.challenges import OPEN_PROBLEMS, render_challenge_source
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
