/-!
# Gromov-style Growth Theory

Sub-results related to Gromov's Polynomial Growth Theorem and the
**Gap Conjecture** — one of Gromov's favorite open problems in
geometric group theory.

The Gap Conjecture states that no finitely generated group has growth
rate strictly between polynomial and exponential. Equivalently: if a
group's growth is not polynomial (of any degree), then it must be
exponential.

We formalize basic growth definitions and prove elementary cases.
The agent should attempt ALL targets, including the open problem (G6).

## Difficulty Guide
- G1-G3: Trivial (omega, ring, constructor)
- G4-G5: Easy (intro, use hypotheses, arithmetic)
- G6: Open problem (Gap Conjecture — attempt it!)
-/

-- ============================================================
-- Definitions
-- ============================================================

/-- A growth function maps radius n to the number of elements
    reachable in at most n steps from the identity. -/
def GrowthFn := Nat → Nat

/-- A growth function is polynomially bounded of degree d:
    there exists C > 0 such that γ(n) ≤ C · n^d for all n > 0. -/
def IsPolynomialGrowth (γ : GrowthFn) (d : Nat) : Prop :=
  ∃ C : Nat, C > 0 ∧ ∀ n : Nat, n > 0 → γ n ≤ C * n ^ d

/-- A growth function has at most polynomial growth (of some degree). -/
def HasPolynomialGrowth (γ : GrowthFn) : Prop :=
  ∃ d : Nat, IsPolynomialGrowth γ d

/-- A growth function is at least exponential:
    there exists c > 1 such that γ(n) ≥ c^n for all n. -/
def IsExponentialGrowth (γ : GrowthFn) : Prop :=
  ∃ c : Nat, c > 1 ∧ ∀ n : Nat, γ n ≥ c ^ n

/-- A growth function is sub-multiplicative (a natural property
    of word growth in groups). -/
def IsSubMultiplicative (γ : GrowthFn) : Prop :=
  ∀ m n : Nat, γ (m + n) ≤ γ m * γ n

/-- A growth function is monotone (larger radius → more elements). -/
def IsMonotone (γ : GrowthFn) : Prop :=
  ∀ m n : Nat, m ≤ n → γ m ≤ γ n

-- ============================================================
-- Provable sub-results (agent targets)
-- ============================================================

/-- G1: Constant growth is polynomial of degree 0. -/
theorem constant_growth_is_poly (c : Nat) (hc : c > 0) :
    IsPolynomialGrowth (fun _ => c) 0 := by
  sorry

/-- G2: Linear growth is polynomial of degree 1. -/
theorem linear_growth_is_poly (a : Nat) (ha : a > 0) (b : Nat) :
    IsPolynomialGrowth (fun n => a * n + b) 1 := by
  sorry

/-- G3: Polynomial growth of degree d implies degree d+1.
    (Growth bounds are monotone in degree.) -/
theorem poly_growth_degree_mono (γ : GrowthFn) (d : Nat) :
    IsPolynomialGrowth γ d → IsPolynomialGrowth γ (d + 1) := by
  sorry

/-- G4: If γ has polynomial growth, then it has polynomial growth. -/
theorem has_poly_of_poly_degree (γ : GrowthFn) (d : Nat) :
    IsPolynomialGrowth γ d → HasPolynomialGrowth γ := by
  sorry

/-- G5: The identity growth function (γ(n) = 1) is polynomial. -/
theorem identity_growth_is_poly :
    IsPolynomialGrowth (fun _ => 1) 0 := by
  sorry

-- ============================================================
-- The Gap Conjecture (OPEN PROBLEM)
-- ============================================================

/-- G6: **Gromov's Gap Conjecture** (Open Problem)

If a growth function is sub-multiplicative and monotone (as all
word growth functions in finitely generated groups are), and it
is NOT polynomially bounded of any degree, then it must be
exponential.

This is a major open problem in geometric group theory.
The agent should attempt this — even partial progress is valuable.

Known partial results:
- True for solvable groups (Wolf, 1968)
- True for residually nilpotent groups
- True for linear groups (Tits alternative)
- Gromov proved the converse direction (polynomial ↔ virtually nilpotent)
-/
theorem gap_conjecture (γ : GrowthFn)
    (h_sub : IsSubMultiplicative γ)
    (h_mono : IsMonotone γ)
    (h_not_poly : ¬ HasPolynomialGrowth γ) :
    IsExponentialGrowth γ := by
  sorry
