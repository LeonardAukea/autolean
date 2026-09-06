import Mathlib.GroupTheory.ResiduallyFinite
import Mathlib.Combinatorics.SimpleGraph.Cayley
import Mathlib.Combinatorics.SimpleGraph.Metric

/-!
# Gromov's residual-finiteness question

Does every word-hyperbolic group admit enough finite quotients to separate
its elements? The final implication is an open research target. The first
two targets concern finite quotients under explicit hypotheses.

Source: M. Gromov, Hyperbolic Groups (1987), pp. 76, 89, 141.
https://www.ihes.fr/~gromov/wp-content/uploads/2018/08/657.pdf
Current status: Gardam, Kielak and Logan (2026).
https://doi.org/10.1017/S0305004126101959
-/

universe u

namespace Gromov

/-- A connected Cayley graph from finitely many generators with a uniform
four-point bound.
Connectivity makes its natural-valued graph distance a genuine metric. -/
def IsWordHyperbolic (G : Type u) [Group G] : Prop :=
  ∃ S : Finset G,
    let X := SimpleGraph.mulCayley (S : Set G)
    X.Connected ∧ ∃ δ : ℕ, ∀ a b c d : G,
      X.dist a c + X.dist b d ≤
        max (X.dist a b + X.dist c d) (X.dist a d + X.dist b c) + 2 * δ

variable {G : Type u} [Group G]

/-- A known finite-group sanity case supplied by Mathlib. -/
theorem finite_group [Finite G] : Group.ResiduallyFinite G := inferInstance

/-- One finite quotient separates every pair in a chosen finite set. -/
theorem finite_quotient_injective [Group.ResiduallyFinite G] (s : Finset G) :
    ∃ N : FiniteIndexNormalSubgroup G,
      Set.InjOn (QuotientGroup.mk' N.toSubgroup) (s : Set G) := by
  sorry

/-- Finite-set separation implies residual finiteness. -/
theorem residually_finite_of_finite_quotients
    (h : ∀ s : Finset G, ∃ N : FiniteIndexNormalSubgroup G,
      Set.InjOn (QuotientGroup.mk' N.toSubgroup) (s : Set G)) :
    Group.ResiduallyFinite G := by
  classical
    rw [Group.residuallyFinite_iff_exists_finiteIndexNormalSubgroup]
    intro g hg
    obtain ⟨N, hN⟩ := h ({g, 1} : Finset G)
    refine ⟨N, ?_⟩
    intro hgN
    apply hg
    apply hN (by simp) (by simp)
    simpa using (QuotientGroup.eq_one_iff g).mpr hgN

/-- The open implication, with no residual-finiteness hypothesis. -/
theorem residual_finiteness_question (h : IsWordHyperbolic G) :
    Group.ResiduallyFinite G := by
  sorry

end Gromov
