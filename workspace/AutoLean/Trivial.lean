/-!
# Trivial targets

These should be solvable by single tactics: `rfl`, `trivial`, `decide`, `omega`.
Good warmup for the agent — expect 100% success rate.
-/

-- T1: Reflexivity
theorem trivial_rfl : 1 + 1 = 2 := by
  sorry

-- T2: Propositional identity
theorem trivial_id (P : Prop) (h : P) : P := by
  sorry

-- T3: And introduction
theorem trivial_and (h1 : True) (h2 : True) : True ∧ True := by
  sorry

-- T4: Or introduction left
theorem trivial_or_left (h : True) : True ∨ False := by
  sorry

-- T5: True is true
theorem trivial_true : True := by
  sorry

-- T6: Natural number equality
theorem trivial_nat_eq : 2 + 3 = 5 := by
  sorry

-- T7: Boolean decide
theorem trivial_bool : (true && false) = false := by
  sorry

-- T8: Implication
theorem trivial_impl (P Q : Prop) (h : P) (hpq : P → Q) : Q := by
  sorry
