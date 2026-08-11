"""Curated open-problem statements and formalization scaffolds.

A curated list of open mathematical problems, ranging from accessible
sub-results to millennium-level challenges. Each problem includes:
  - Natural language description
  - Difficulty rating
  - Known sub-results
  - A formalized Lean statement or an explicitly labeled scaffold

Only source-faithful formalizations can enter the proof loop.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from rich.console import Console
from rich.table import Table

console = Console()


@dataclass
class OpenProblem:
    """An open problem with a formalized statement or labeled scaffold."""

    id: str
    name: str
    # `field` is an attribute name here, so the defaults below must spell out
    # `dataclasses.field` rather than shadow it with a bare import.
    field: str
    difficulty: str  # "accessible" | "hard" | "very-hard" | "millennium"
    description: str
    lean_statement: str  # Lean 4 theorem statement with sorry
    formalization_status: Literal["formalized", "scaffold"] = "formalized"
    limitations: str = ""
    sub_results: list[str] = dataclasses.field(default_factory=list)  # provable lemmas
    references: list[str] = dataclasses.field(default_factory=list)
    tags: list[str] = dataclasses.field(default_factory=list)


# ---------------------------------------------------------------------------
# The Collection
# ---------------------------------------------------------------------------

OPEN_PROBLEMS: list[OpenProblem] = [
    # === NUMBER THEORY ===
    OpenProblem(
        id="collatz",
        name="Collatz Conjecture",
        field="Number Theory",
        difficulty="very-hard",
        description=(
            "For any positive integer n, the sequence n → n/2 (if even) or "
            "3n+1 (if odd) eventually reaches 1."
        ),
        lean_statement="""\
def collatz_step (n : Nat) : Nat :=
  if n % 2 == 0 then n / 2 else 3 * n + 1

def collatz_reaches_one (n : Nat) : Prop :=
  ∃ k : Nat, (Nat.iterate collatz_step k n) = 1

theorem collatz_conjecture (n : Nat) (hn : n > 0) :
    collatz_reaches_one n := by
  sorry""",
        sub_results=[
            "theorem collatz_step_one : collatz_step 1 = 4 := by sorry",
            "theorem collatz_step_two : collatz_step 2 = 1 := by sorry",
            "theorem collatz_reaches_one_of_pow2 (k : Nat) : collatz_reaches_one (2^k) := by sorry",
        ],
        references=["https://en.wikipedia.org/wiki/Collatz_conjecture"],
        tags=["number-theory", "dynamics", "elementary"],
    ),
    OpenProblem(
        id="twin-primes",
        name="Twin Prime Conjecture",
        field="Number Theory",
        difficulty="very-hard",
        description="There are infinitely many pairs of primes (p, p+2).",
        lean_statement="""\
def IsTwinPrime (p : Nat) : Prop :=
  Nat.Prime p ∧ Nat.Prime (p + 2)

theorem twin_prime_conjecture :
    ∀ N : Nat, ∃ p : Nat, p > N ∧ IsTwinPrime p := by
  sorry""",
        sub_results=[
            "theorem twin_prime_3 : IsTwinPrime 3 := by sorry",
            "theorem twin_prime_5 : IsTwinPrime 5 := by sorry",
            "theorem twin_prime_11 : IsTwinPrime 11 := by sorry",
        ],
        references=["https://en.wikipedia.org/wiki/Twin_prime"],
        tags=["number-theory", "primes"],
    ),
    OpenProblem(
        id="goldbach",
        name="Goldbach's Conjecture",
        field="Number Theory",
        difficulty="very-hard",
        description="Every even integer greater than 2 is the sum of two primes.",
        lean_statement="""\
theorem goldbach_conjecture (n : Nat) (hn : n > 2) (he : n % 2 = 0) :
    ∃ p q : Nat, Nat.Prime p ∧ Nat.Prime q ∧ p + q = n := by
  sorry""",
        sub_results=[
            "theorem goldbach_4 : ∃ p q, Nat.Prime p ∧ Nat.Prime q ∧ p + q = 4 := by sorry",
            "theorem goldbach_6 : ∃ p q, Nat.Prime p ∧ Nat.Prime q ∧ p + q = 6 := by sorry",
        ],
        references=["https://en.wikipedia.org/wiki/Goldbach%27s_conjecture"],
        tags=["number-theory", "primes", "additive"],
    ),
    # === ALGEBRA / GROUP THEORY ===
    OpenProblem(
        id="growth-gap",
        name="Gromov's Gap Conjecture",
        field="Geometric Group Theory",
        difficulty="very-hard",
        description="No finitely generated group has growth strictly between polynomial and exponential.",
        lean_statement="""\
def GrowthFn := Nat → Nat

def HasPolynomialGrowth (γ : GrowthFn) : Prop :=
  ∃ d C : Nat, C > 0 ∧ ∀ n, n > 0 → γ n ≤ C * n ^ d

def IsExponentialGrowth (γ : GrowthFn) : Prop :=
  ∃ c : Nat, c > 1 ∧ ∀ n, γ n ≥ c ^ n

theorem growth_gap_conjecture (γ : GrowthFn)
    (h_not_poly : ¬ HasPolynomialGrowth γ) :
    IsExponentialGrowth γ := by
  sorry""",
        sub_results=[
            "theorem polynomial_growth_has_witness (γ : GrowthFn) "
            "(h : HasPolynomialGrowth γ) : "
            "∃ d C : Nat, C > 0 ∧ ∀ n, n > 0 → γ n ≤ C * n ^ d := by sorry",
        ],
        formalization_status="scaffold",
        limitations="The growth function is not connected to a finitely generated group.",
        references=["Gromov, Groups of polynomial growth (1981)"],
        tags=["group-theory", "geometric", "growth"],
    ),
    # === TOPOLOGY / GEOMETRY ===
    OpenProblem(
        id="filling-area",
        name="Gromov's Filling Area Conjecture",
        field="Metric Geometry",
        difficulty="hard",
        description=(
            "The hemisphere has the least area among all surfaces that isometrically fill the circle."
        ),
        lean_statement="""\
-- Simplified version: filling area of the unit circle
theorem filling_area_conjecture (area : Nat) (h : area > 0) :
    area ≥ 2 := by  -- π ≈ 2 in this simplified discrete version
  sorry""",
        sub_results=[],
        formalization_status="scaffold",
        limitations="The natural-number area surrogate is not Gromov's filling invariant.",
        references=["Gromov, Filling Riemannian manifolds (1983)"],
        tags=["geometry", "metric", "filling"],
    ),
    OpenProblem(
        id="poincare-higher",
        name="Smooth Poincare Conjecture (dim 4)",
        field="Topology",
        difficulty="millennium",
        description="Is every smooth homotopy 4-sphere diffeomorphic to S^4?",
        lean_statement="""\
-- Statement requires substantial differential topology infrastructure
-- This is a placeholder formalization
structure SmoothManifold4 where
  Carrier : Type
  isHomotopySphere : Prop
  isDiffeomorphicToS4 : Prop

theorem smooth_poincare_dim4 (M : SmoothManifold4) :
    M.isHomotopySphere → M.isDiffeomorphicToS4 := by
  sorry""",
        sub_results=[],
        formalization_status="scaffold",
        limitations="The structure records propositions without differential-topology semantics.",
        references=["https://en.wikipedia.org/wiki/Generalized_Poincar%C3%A9_conjecture"],
        tags=["topology", "smooth", "4-manifold"],
    ),
    # === COMBINATORICS ===
    OpenProblem(
        id="hadamard",
        name="Hadamard Matrix Conjecture",
        field="Combinatorics",
        difficulty="hard",
        description="A Hadamard matrix of order n exists for every n divisible by 4.",
        lean_statement="""\
-- A Hadamard matrix is an n×n matrix with entries ±1 whose rows are orthogonal
def IsHadamard (n : Nat) (M : Fin n → Fin n → Int) : Prop :=
  (∀ i j, M i j = 1 ∨ M i j = -1) ∧
  (∀ i j, i ≠ j → (Finset.sum Finset.univ fun k => M i k * M j k) = 0)

theorem hadamard_conjecture (n : Nat) (h : n % 4 = 0) (hn : n > 0) :
    ∃ M : Fin n → Fin n → Int, IsHadamard n M := by
  sorry""",
        sub_results=[
            "-- Hadamard matrix of order 1 exists",
            "-- Hadamard matrix of order 2 exists (Sylvester construction)",
            "-- Hadamard matrix of order 4 exists",
        ],
        references=["https://en.wikipedia.org/wiki/Hadamard_matrix"],
        tags=["combinatorics", "linear-algebra", "matrices"],
    ),
    # === ANALYSIS ===
    OpenProblem(
        id="riemann",
        name="Riemann Hypothesis",
        field="Analytic Number Theory",
        difficulty="millennium",
        description="All non-trivial zeros of the Riemann zeta function have real part 1/2.",
        lean_statement="""\
-- The Riemann Hypothesis requires complex analysis infrastructure
-- This is the real-variable reformulation via Chebyshev's function
-- ψ(x) = Σ_{p^k ≤ x} log p

theorem riemann_hypothesis_chebyshev (x : Nat) (hx : x > 1) :
    -- |ψ(x) - x| ≤ C * √x * log²x for some constant C
    True := by  -- placeholder: the real statement needs ℝ and analysis
  sorry""",
        sub_results=[
            "-- Prime number theorem: ψ(x) ~ x (proved, could formalize)",
        ],
        formalization_status="scaffold",
        limitations="The current Lean statement is True and does not express the hypothesis.",
        references=["https://en.wikipedia.org/wiki/Riemann_hypothesis"],
        tags=["number-theory", "analysis", "millennium"],
    ),
    # === ACCESSIBLE / FUN ===
    OpenProblem(
        id="perfect-odd",
        name="Odd Perfect Number",
        field="Number Theory",
        difficulty="hard",
        description="Does an odd perfect number exist? (A number equal to the sum of its proper divisors.)",
        lean_statement="""\
def IsPerfect (n : Nat) : Prop :=
  n > 1 ∧ (Finset.sum (Finset.filter (· ∣ n) (Finset.range n)) id) = n

theorem no_odd_perfect_number :
    ∀ n : Nat, n % 2 = 1 → ¬ IsPerfect n := by
  sorry""",
        sub_results=[
            "-- 6 is perfect: 1 + 2 + 3 = 6",
            "-- 28 is perfect: 1 + 2 + 4 + 7 + 14 = 28",
            "-- Structural bounds constrain any odd perfect number",
        ],
        references=["https://en.wikipedia.org/wiki/Perfect_number"],
        tags=["number-theory", "divisors", "accessible"],
    ),
    OpenProblem(
        id="lonely-runner",
        name="Lonely Runner Conjecture",
        field="Combinatorics / Dynamics",
        difficulty="hard",
        description=(
            "For k runners on a circular track with distinct speeds, each runner "
            "is at some point lonely (distance ≥ 1/(k+1) from all others)."
        ),
        lean_statement="""\
-- Simplified discrete version
theorem lonely_runner (k : Nat) (speeds : Fin k → Nat)
    (h_distinct : ∀ i j, i ≠ j → speeds i ≠ speeds j) :
    ∀ i : Fin k, ∃ t : Nat, ∀ j : Fin k, i ≠ j →
      -- runner i is "lonely" at time t
      True := by  -- real version needs modular arithmetic on [0,1)
  sorry""",
        sub_results=[
            "-- Low-runner cases are established",
        ],
        formalization_status="scaffold",
        limitations="The current conclusion is True and omits circular distance.",
        references=["https://en.wikipedia.org/wiki/Lonely_runner_conjecture"],
        tags=["combinatorics", "dynamics", "accessible"],
    ),
    OpenProblem(
        id="erdos-straus",
        name="Erdos-Straus Conjecture",
        field="Number Theory",
        difficulty="accessible",
        description=(
            "For every integer n ≥ 2, 4/n can be written as 1/x + 1/y + 1/z for positive integers x, y, z."
        ),
        lean_statement="""\
theorem erdos_straus (n : Nat) (hn : n ≥ 2) :
    ∃ x y z : Nat, x > 0 ∧ y > 0 ∧ z > 0 ∧
      4 * x * y * z = n * (y * z + x * z + x * y) := by
  sorry""",
        sub_results=[
            "-- The n = 2 case has witnesses x = 1, y = 2, z = 2",
            "-- Finite computation supplies bounded instances",
        ],
        references=["https://en.wikipedia.org/wiki/Erd%C5%91s%E2%80%93Straus_conjecture"],
        tags=["number-theory", "fractions", "accessible"],
    ),
]


# ---------------------------------------------------------------------------
# Display
# ---------------------------------------------------------------------------


def print_problems_table(filter_field: str | None = None, filter_difficulty: str | None = None) -> None:
    """Print the open problems collection as a Rich table."""
    problems = OPEN_PROBLEMS
    if filter_field:
        problems = [p for p in problems if filter_field.lower() in p.field.lower()]
    if filter_difficulty:
        problems = [p for p in problems if p.difficulty == filter_difficulty]

    diff_styles = {
        "accessible": "green",
        "hard": "yellow",
        "very-hard": "red",
        "millennium": "bold magenta",
    }

    table = Table(
        title=f"Open Problems ({len(problems)})",
        show_header=True,
        header_style="bold",
        min_width=80,
    )
    table.add_column("ID", style="cyan", min_width=15)
    table.add_column("Problem", min_width=25)
    table.add_column("Field", min_width=15)
    table.add_column("Difficulty", min_width=12)
    table.add_column("Formalization", min_width=13)
    table.add_column("Sub-results", justify="right", min_width=12)

    for p in problems:
        style = diff_styles.get(p.difficulty, "white")
        table.add_row(
            p.id,
            p.name,
            p.field,
            f"[{style}]{p.difficulty}[/{style}]",
            p.formalization_status,
            str(len(p.sub_results)),
        )

    console.print(table)
    console.print(
        f"\n  [dim]Use:[/] autolean challenge {problems[0].id}"
        f"  [dim]to generate a workspace and start proving[/]"
    )


def render_challenge_source(problem: OpenProblem) -> str:
    """Render a complete Lean source file for one curated challenge."""
    from autolean.generated_code import safe_lean_comment_text

    lines = [
        "import Mathlib",
        "",
        "/-!",
        f"# Challenge: {safe_lean_comment_text(problem.name)}",
        "",
        f"Field: {safe_lean_comment_text(problem.field)}",
        f"Difficulty: {safe_lean_comment_text(problem.difficulty)}",
        f"Formalization: {problem.formalization_status}",
        "",
        safe_lean_comment_text(problem.description),
        safe_lean_comment_text(problem.limitations) if problem.limitations else "",
        "",
        f"Generated by: autolean challenge {problem.id}",
        "-/",
        "",
        "-- Main conjecture",
        problem.lean_statement,
        "",
    ]

    if problem.sub_results:
        lines.append("-- Sub-results (more likely to be provable)")
        lines.append("")
        for i, sub in enumerate(problem.sub_results, 1):
            lines.append(f"-- Sub-result {i}")
            lines.append(sub)
            lines.append("")

    if problem.references:
        lines.append("-- References")
        for ref in problem.references:
            lines.append(f"-- {safe_lean_comment_text(ref)}")

    return "\n".join(lines)


def generate_challenge_file(problem: OpenProblem, output_dir: str | Path = "workspace/AutoLean") -> Path:
    """Write one rendered challenge source and return its path."""
    filename = f"Challenge_{problem.id.replace('-', '_').title()}.lean"
    path = Path(output_dir) / filename
    path.parent.mkdir(parents=True, exist_ok=True)

    path.write_text(render_challenge_source(problem), encoding="utf-8")
    return path
