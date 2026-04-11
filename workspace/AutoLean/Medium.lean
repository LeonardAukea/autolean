/-!
# Medium targets

These need multi-step proofs: induction, case splits with nontrivial
branches, rewriting chains, or structured arguments. Expect ~40% success rate.
-/

-- M1: Addition is commutative (by induction)
theorem medium_add_comm (n m : Nat) : n + m = m + n := by
  rw [Nat.add_comm]

-- M2: Length of reversed list
theorem medium_length_reverse (α : Type) (l : List α) :
    (l.reverse).length = l.length := by
  sorry

-- M3: Map preserves length
theorem medium_map_length (α β : Type) (f : α → β) (l : List α) :
    (l.map f).length = l.length := by
  induction l with
  | nil => rfl
  | cons x xs ih => simp [List.map, List.length, ih]

-- M4: Zero is identity for addition (both sides)
theorem medium_add_zero (n : Nat) : n + 0 = n ∧ 0 + n = n := by
  constructor
  · rfl
  · induction n with
  | zero => rfl
  | succ n ih => simp [ih]

-- M5: Transitivity of ≤
theorem medium_le_trans (a b c : Nat) : a ≤ b → b ≤ c → a ≤ c := by
  intro h1 h2
  exact Nat.le_trans h1 h2

-- M6: If-then-else simplification
theorem medium_ite_same (P : Prop) [Decidable P] (a : Nat) :
    (if P then a else a) = a := by
  sorry

-- M7: Distributivity
theorem medium_mul_add (a b c : Nat) : a * (b + c) = a * b + a * c := by
  sorry

-- M8: Sum of first n naturals
theorem medium_sum_formula (n : Nat) :
    2 * (List.range n).sum = n * (n - 1) := by
  sorry
