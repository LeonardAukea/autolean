import Mathlib

/-!
# Polynomial Growth Bounds

This module develops elementary bounds for functions from natural radii to
natural counts. A group-growth application supplies a finitely generated
group, a finite generating set, its word metric, and the resulting ball-count
function as a separate mathematical layer.
-/

/-- A radius-indexed counting function. -/
abbrev GrowthFn := Nat → Nat

/-- `γ` is bounded by a positive multiple of `n ^ d` at positive radii. -/
def IsPolynomialGrowth (γ : GrowthFn) (d : Nat) : Prop :=
  ∃ C : Nat, C > 0 ∧ ∀ n : Nat, n > 0 → γ n ≤ C * n ^ d

/-- `γ` has a polynomial bound of some natural degree. -/
def HasPolynomialGrowth (γ : GrowthFn) : Prop :=
  ∃ d : Nat, IsPolynomialGrowth γ d

/-- A positive constant function has degree-zero polynomial growth. -/
theorem constant_growth_is_poly (c : Nat) (hc : c > 0) :
    IsPolynomialGrowth (fun _ => c) 0 := by
  refine ⟨c, hc, ?_⟩
  intro n hn
  simp

/-- An affine function with positive slope has a linear growth bound. -/
theorem linear_growth_is_poly (a : Nat) (ha : a > 0) (b : Nat) :
    IsPolynomialGrowth (fun n => a * n + b) 1 := by
  refine ⟨a + b, by omega, ?_⟩
  intro n hn
  have hb : b ≤ b * n := by
    have hn_one : 1 ≤ n := by omega
    simpa using Nat.mul_le_mul_left b hn_one
  simpa [Nat.add_mul] using Nat.add_le_add_left hb (a * n)

/-- A polynomial bound remains valid after increasing its degree by one. -/
theorem poly_growth_degree_mono (γ : GrowthFn) (d : Nat) :
    IsPolynomialGrowth γ d → IsPolynomialGrowth γ (d + 1) := by
  rintro ⟨C, hC, hbound⟩
  refine ⟨C, hC, ?_⟩
  intro n hn
  have hn_one : 1 ≤ n := by omega
  have hpow : n ^ d ≤ n ^ (d + 1) := by
    calc
      n ^ d = n ^ d * 1 := by simp
      _ ≤ n ^ d * n := Nat.mul_le_mul_left (n ^ d) hn_one
      _ = n ^ (d + 1) := by simp [pow_succ]
  exact (hbound n hn).trans (Nat.mul_le_mul_left C hpow)

/-- A bound at one degree witnesses polynomial growth. -/
theorem has_poly_of_poly_degree (γ : GrowthFn) (d : Nat) :
    IsPolynomialGrowth γ d → HasPolynomialGrowth γ := by
  intro h
  exact ⟨d, h⟩

/-- The constant-one counting function has degree-zero growth. -/
theorem identity_growth_is_poly :
    IsPolynomialGrowth (fun _ => 1) 0 := by
  exact constant_growth_is_poly 1 (by omega)
