-- Series of theorems for end-to-end testing
-- These require actual multi-step proofs, not just lemma lookups

-- U1: Simple logic (intro + exact)
theorem imp_trans (P Q R : Prop) : (P → Q) → (Q → R) → P → R := by
  intro hPQ hQR hP
  exact hPQ hP |> hQR

-- U2: Nat arithmetic (omega should work)
theorem add_lt_add_of_lt (a b c : Nat) : a < b → a + c < b + c := by
  sorry

-- U3: Boolean logic (decide/simp)
theorem bool_and_true (b : Bool) : (b && true) = b := by
  cases b with
  | true => rfl
  | false => rfl

-- U4: List length (needs induction)
theorem length_map_eq (α β : Type) (f : α → β) (l : List α) :
    (l.map f).length = l.length := by
  sorry

-- U5: Exists introduction
theorem exists_succ (n : Nat) : ∃ m : Nat, m = n + 1 := by
  sorry
