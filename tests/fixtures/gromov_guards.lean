namespace GromovGuards

#check (@Gromov.residual_finiteness_question :
  ∀ (G : Type u) [Group G],
    Gromov.IsWordHyperbolic G → Group.ResiduallyFinite G)

theorem connected_generators {G : Type u} [Group G]
    (h : Gromov.IsWordHyperbolic G) :
    ∃ S : Finset G, (SimpleGraph.mulCayley (S : Set G)).Connected := by
  rcases h with ⟨S, hS, δ, hδ⟩
  exact ⟨S, hS⟩

theorem finite_isWordHyperbolic (G : Type u) [Group G] [Finite G] :
    Gromov.IsWordHyperbolic G := by
  classical
  let : Fintype G := Fintype.ofFinite G
  refine ⟨Finset.univ, ?_⟩
  simp only [Finset.coe_univ, SimpleGraph.mulCayley_univ]
  refine ⟨SimpleGraph.connected_top, 1, ?_⟩
  intro a b c d
  have distance_le_one (x y : G) : (⊤ : SimpleGraph G).dist x y ≤ 1 := by
    by_cases h : x = y <;> simp [SimpleGraph.dist_top, h]
  exact (Nat.add_le_add (distance_le_one a c) (distance_le_one b d)).trans
    (Nat.le_add_left 2 _)

theorem empty_disconnected (G : Type u) [Group G] [Nontrivial G] :
    ¬(SimpleGraph.mulCayley (∅ : Set G)).Connected := by
  simpa only [SimpleGraph.mulCayley_empty] using
    (SimpleGraph.not_connected_bot (V := G))

end GromovGuards
