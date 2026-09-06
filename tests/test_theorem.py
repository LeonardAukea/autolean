from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace

import pytest

from autolean.llm import LLMConfig, LLMResponse
from autolean.strategy import ProofPlan
from autolean.theorem import FormalizationError, formalize_theorem, generated_theorem_path

_CONFIG = LLMConfig(model="fixture", backend="ollama")


class Project:
    def __init__(self, root: Path, results: list[object]) -> None:
        self.root = root
        self.results = results
        self.sources: list[str] = []

    def validate_candidate(
        self,
        lean_file: Path,
        content: str,
        *,
        timeout: int,
    ) -> object:
        assert lean_file == self.root / "AutoLean" / "Generated" / "FormalizationCandidate.lean"
        assert timeout == 120
        self.sources.append(content)
        return self.results.pop(0)


def _plan() -> ProofPlan:
    return ProofPlan(
        objective="State the Riemann hypothesis with explicit complex coercions.",
        formalization=("Use Complex and riemannZeta.",),
        checkpoints=("Compile the theorem scaffold.",),
    )


def test_formalization_repairs_exact_lean_diagnostics(tmp_path: Path) -> None:
    responses = iter(
        [
            "theorem rh (s : ℂ) (n : ℕ) : s = n := by sorry",
            "theorem rh (s : ℂ) (n : ℕ) : s = (n : ℂ) := by sorry",
        ]
    )
    prompts: list[str] = []

    def generate(system: str, user: str) -> LLMResponse:
        del system
        prompts.append(user)
        return LLMResponse(text=next(responses), model="fixture")

    failed = SimpleNamespace(
        success=False,
        errors=[SimpleNamespace(message="type mismatch: n has type Nat but Complex was expected")],
        stderr="",
    )
    passed = SimpleNamespace(success=True, errors=[], stderr="")
    project = Project(tmp_path, [failed, passed])

    theorem = formalize_theorem(
        "Riemann hypothesis",
        _plan(),
        generate,
        project,
        llm_config=_CONFIG,
    )

    assert theorem.declaration_name == "rh"
    assert theorem.attempts == 2
    assert "type mismatch" in prompts[1]
    assert "s = (n : ℂ)" in theorem.source
    assert len(theorem.receipts) == 2
    assert theorem.receipts[-1].backend == "ollama"
    assert theorem.receipts[-1].response_sha256 in theorem.source
    assert "Formalization inference: local" in theorem.source
    assert len(project.sources) == 2


def test_formalization_repairs_an_incomplete_pythagorean_declaration(
    tmp_path: Path,
) -> None:
    responses = iter(
        [
            (
                "open EuclideanGeometry\n\n"
                "theorem pythagorean_theorem\n"
                "    {V : Type*} {P : Type*} [NormedAddCommGroup V]\n"
                "    [InnerProductSpace ℝ V] [MetricSpace P]\n"
                "    [NormedAddTorsor V P]"
            ),
            (
                "open RealInnerProductSpace\n\n"
                "theorem pythagorean_theorem "
                "{V : Type*} [NormedAddCommGroup V] "
                "[InnerProductSpace ℝ V] (x y : V) "
                "(h : ⟪x, y⟫ = 0) :\n"
                "    ‖x + y‖ ^ 2 = ‖x‖ ^ 2 + ‖y‖ ^ 2 := by\n"
                "  sorry"
            ),
        ]
    )
    prompts: list[str] = []

    def generate(system: str, user: str) -> LLMResponse:
        del system
        prompts.append(user)
        return LLMResponse(text=next(responses), model="fixture")

    passed = SimpleNamespace(success=True, errors=[], stderr="")
    project = Project(tmp_path, [passed])

    theorem = formalize_theorem(
        "the pytahgorean theorem",
        _plan(),
        generate,
        project,
        llm_config=_CONFIG,
    )

    assert theorem.declaration_name == "pythagorean_theorem"
    assert theorem.attempts == 2
    assert "the pytahgorean theorem" in prompts[1]
    assert "exactly one `sorry`" in prompts[1]
    assert len(project.sources) == 1


def test_formalization_requires_exactly_one_proof_target(tmp_path: Path) -> None:
    def generate(system: str, user: str) -> LLMResponse:
        del system, user
        return LLMResponse(
            text="theorem first : True := by sorry\ntheorem second : True := by sorry",
            model="fixture",
        )

    project = Project(tmp_path, [])
    with pytest.raises(FormalizationError, match="exactly one"):
        formalize_theorem(
            "two theorems",
            _plan(),
            generate,
            project,
            llm_config=_CONFIG,
            max_repairs=0,
        )


def test_formalization_extracts_a_lean_fence_after_prose(tmp_path: Path) -> None:
    def generate(system: str, user: str) -> LLMResponse:
        del system, user
        return LLMResponse(
            text=(
                "I'll inspect the workspace and then write the theorem.\n"
                "```lean\n"
                "theorem prefixed : True := by\n  sorry\n"
                "```\n"
            ),
            model="fixture",
        )

    theorem = formalize_theorem(
        "True",
        _plan(),
        generate,
        Project(tmp_path, [SimpleNamespace(success=True, errors=[], stderr="")]),
        llm_config=_CONFIG,
        max_repairs=0,
    )

    assert "I'll inspect" not in theorem.source
    assert "theorem prefixed : True := by" in theorem.source


def test_formalization_drops_prose_before_an_unfenced_theorem(tmp_path: Path) -> None:
    def generate(system: str, user: str) -> LLMResponse:
        del system, user
        return LLMResponse(
            text=(
                "I'll inspect the workspace and then write the theorem.\n"
                "theorem unfenced : True := by\n  sorry\n"
            ),
            model="fixture",
        )

    theorem = formalize_theorem(
        "True",
        _plan(),
        generate,
        Project(tmp_path, [SimpleNamespace(success=True, errors=[], stderr="")]),
        llm_config=_CONFIG,
        max_repairs=0,
    )

    assert "I'll inspect" not in theorem.source
    assert theorem.declaration_name == "unfenced"


def test_declaration_line_ignores_blank_lines_before_theorem(tmp_path: Path) -> None:
    source = "open RealInnerProductSpace\n\ntheorem pythagorean : True := by\n  sorry"

    def generate(system: str, user: str) -> LLMResponse:
        del system, user
        return LLMResponse(text=source, model="fixture")

    passed = SimpleNamespace(success=True, errors=[], stderr="")
    theorem = formalize_theorem(
        "Pythagorean theorem",
        _plan(),
        generate,
        Project(tmp_path, [passed]),
        llm_config=_CONFIG,
    )
    actual_line = theorem.source.splitlines().index("theorem pythagorean : True := by") + 1

    assert theorem.declaration_line == actual_line


def test_generated_theorem_path_ignores_unrelated_shared_source(tmp_path: Path) -> None:
    shared = tmp_path / "AutoLean" / "UserTheorems.lean"
    shared.parent.mkdir(parents=True)
    shared.write_text("this source is broken", encoding="utf-8")

    first = generated_theorem_path(tmp_path, "riemann_hypothesis")
    first.parent.mkdir(parents=True)
    first.write_text("existing", encoding="utf-8")
    second = generated_theorem_path(tmp_path, "riemann_hypothesis")

    assert first == tmp_path / "AutoLean" / "Generated" / "RiemannHypothesis.lean"
    assert second.name == "RiemannHypothesis_2.lean"
    assert shared.read_text(encoding="utf-8") == "this source is broken"
