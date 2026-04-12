-- Fresh targets for 1-hour run

theorem u1_rfl : 1 + 1 = 2 := by rfl
theorem u2_id (P : Prop) (h : P) : P := by exact h
theorem u3_and_intro (a b : Prop) (ha : a) (hb : b) : a ∧ b := by constructor
                                                                  exact ha
                                                                  exact hb
theorem u4_or_inl (a : Prop) (ha : a) (b : Prop) : a ∨ b := by exact Or.inl ha
theorem u5_true : True := by trivial
theorem u6_nat_eq : 2 + 3 = 5 := by rfl
theorem u7_bool_and : (true && false) = false := by rfl
theorem u8_mp (P Q : Prop) (h : P) (f : P → Q) : Q := by exact f h
theorem u9_and_comm (P Q : Prop) (h : P ∧ Q) : Q ∧ P := by exact ⟨h.2, h.1⟩
theorem u10_or_elim (P Q R : Prop) (hp : P → R) (hq : Q → R) (h : P ∨ Q) : R := by cases h with
                                                                                   | inl hP => exact hp hP
                                                                                   | inr hQ => exact hq hQ
theorem u11_contra (P Q : Prop) (f : P → Q) (nq : ¬Q) : ¬P := by intro h
                                                                 exact nq (f h)
theorem u12_dne (P : Prop) (h : P) : ¬¬P := by intro h_not_P
                                               exact h_not_P h
theorem u13_nat_le : ∀ n : Nat, n ≤ n + 1 := by intro n
                                                apply Nat.le_succ n
theorem u14_append_nil (α : Type) (l : List α) : l ++ [] = l := by simp
theorem u15_comp (α β γ : Type) (f : α → β) (g : β → γ) (x : α) : g (f x) = (g ∘ f) x := by rfl
theorem u16_add_comm (n m : Nat) : n + m = m + n := by rw [Nat.add_comm]
theorem u17_le_trans (a b c : Nat) (h1 : a ≤ b) (h2 : b ≤ c) : a ≤ c := by omega
theorem u18_ite_same (P : Prop) [Decidable P] (a : Nat) : (if P then a else a) = a := by simp
theorem u19_mul_add (a b c : Nat) : a * (b + c) = a * b + a * c := by sorry
theorem u20_exists (n : Nat) : ∃ m, m = n + 1 := by sorry

theorem one_plus_one_eq_two : 1 + 1 = 2 := by
  rfl
