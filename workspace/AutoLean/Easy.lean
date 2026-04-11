/-!
# Easy targets

These need small tactic chains: `intro` + `exact`, `cases`, `simp`,
`constructor`, `omega` on small arithmetic. Expect ~80% success rate.
-/

-- E1: Modus ponens
theorem easy_mp (P Q : Prop) : P → (P → Q) → Q := by
  intro hP hPQ
  exact hPQ hP

-- E2: Symmetry of And
theorem easy_and_comm (P Q : Prop) : P ∧ Q → Q ∧ P := by
  intro ⟨hP, hQ⟩
  exact ⟨hQ, hP⟩

-- E3: Disjunction elimination
theorem easy_or_elim (P Q R : Prop) : (P → R) → (Q → R) → P ∨ Q → R := by
  intro hP hQ hOr
  cases hOr with
  | inl p => exact hP p
  | inr q => exact hQ q

-- E4: Contrapositive
theorem easy_contrapositive (P Q : Prop) : (P → Q) → ¬Q → ¬P := by
  intro h h' p
  exact h' (h p)

-- E5: Nat inequality
theorem easy_nat_le : ∀ n : Nat, n ≤ n + 1 := by
  intro n
  apply Nat.le_succ

-- E6: List append nil
theorem easy_append_nil (α : Type) (l : List α) : l ++ [] = l := by
  simp

-- E7: Function composition
theorem easy_comp_assoc (α β γ δ : Type)
    (f : α → β) (g : β → γ) (h : γ → δ) (x : α) :
    h (g (f x)) = (h ∘ g ∘ f) x := by
  sorry

-- E8: Double negation introduction
theorem easy_dne_intro (P : Prop) : P → ¬¬P := by
  sorry
