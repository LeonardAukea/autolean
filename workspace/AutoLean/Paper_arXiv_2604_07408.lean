/-!
# Verification: arXiv:2604.07408

Auto-generated from paper by AutoLean verify.
Each theorem corresponds to a claim in the paper.
Proofs are sorry — the agent will attempt them.
-/

import Mathlib.Algebra.BigOperators.Basic
import Mathlib.Algebra.Order.Ring
import Mathlib.Algebra.Ring.Basic
import Mathlib.Combinatorics.SimpleGraph
import Mathlib.Combinatorics.SimpleGraph.Basic
import Mathlib.Combinatorics.SimpleGraph.Connectivity
import Mathlib.Data.Finset.Basic
import Mathlib.Data.Finset.Card
import Mathlib.Data.Finset.Powerset
import Mathlib.Data.List.Perm
import Mathlib.Data.Nat.Factorial.Basic
import Mathlib.Data.Nat.Perm
import Mathlib.Data.Permutation.Basic
import Mathlib.Data.Polynomial.Basic
import Mathlib.Data.Polynomial.Deriv
import Mathlib.Data.Polynomial.Eval
import Mathlib.Data.Rat
import Mathlib.Data.Real.Basic
import Mathlib.Data.Set.Finite
import Mathlib.GraphTheory.Basic
import Mathlib.Order.Defs

-- [Definition 1.1]: A linear ordering \pi of V is said to be successive if for every vertex v\in V with \pi(v)>1 , there exists a neighbour 

def IsSuccessive {α : Type} [LinearOrder α] (G : SimpleGraph Type) (π : G.V → α) (one : α) : Prop :=
  ∀ v : G.V, π v > one → ∃ u ∈ Adj G v, π u < π v

theorem successive_definition_equivalence {α : Type} [LinearOrder α] (G : SimpleGraph Type) (π : G.V → α) (one : α) :
  IsSuccessive G π one ↔ (∀ v : G.V, π v > one → ∃ u ∈ Adj G v, π u < π v) := by
  sorry

-- [Theorem 1.2]: Let G=(V,E) be a finite connected graph with |V|=n . Then the number \sigma(G) of successive vertex orderings of G is gi

variable {V : Type*} [Fintype V] [DecidableEq V]

theorem successive_vertex_orderings_formula
  (G : SimpleGraph V)
  (sigma : SimpleGraph V → ℕ)
  (a : Finset V → ℝ)
  (b : Finset V → ℝ)
  (h_conn : IsConnected G)
  (h_n_pos : (Finset.univ : Finset V).card > 0) :
  let n := (Finset.univ : Finset V).card
  (sigma G : ℝ) = (Nat.factorial n : ℝ) * ∑ I in Finset.univ.filter (fun I => IsIndependent I G),
    (-1 : ℝ) ^ (I.card : ℤ) * (a I / (n : ℝ)) * b I :=
sorry

-- [Proposition 3.1]: For every U\subseteq V , \Pr(G_{U})=\sum_{\begin{subarray}{c}I\subseteq U\\ I\ \mathrm{independent}\end{subarray}}(-1)^{
-- Proof sketch: By De Morgan’s law and the inclusion–exclusion principle we have the identity \Pr(G_{U})=\Pr\Bigl(\b...

theorem proposition_3_1 {V : Type*} [Fintype V]
  (is_independent : Finset V → Prop)
  (a b : Finset V → ℝ)
  (Pr : Finset V → ℝ)
  (n : ℝ)
  (h_n : n = (Finset.card V : ℝ))
  (U : Finset V) :
  Pr U = ∑ I in (U.powerset.filter is_independent), (-1 : ℝ)^(Finset.card I) * (a I * b I / n) :=
sorry

-- [Corollary 3.2]: For every independent set J\subseteq V , \Pr(B_{J})=\frac{a(J)\,b(J)}{n}=\sum_{T\subseteq J}(-1)^{|T|}\Pr(G_{T}) (3.2)
-- Proof sketch: The second equality is obtained by applying Möbius inversion to ( 3.1 )....

variable {α : Type*} [DecidableEq α]

structure ProblemContext (V : Finset α) where
  a : Finset V → ℝ
  b : Finset V → ℝ
  prob_B : Finset V → ℝ
  prob_G : Finset V → ℝ
  is_independent : Finset V → Prop
  n : ℝ

theorem corollary_3_2 (V : Finset α) (ctx : ProblemContext V) (J : Finset V) (h_indep : ctx.is_independent J) (h_n : ctx.n = V.card) (h_nonzero : ctx.n ≠ 0) :
  ctx.prob_B J = (ctx.a J * ctx.b J) / ctx.n ∧
  ctx.prob_B J = ∑ T in Finset.powerset J, (-1 : ℝ)^(T.card) * ctx.prob_G T :=
sorry

-- [Lemma 4.1]: For each 0\leq i\leq\alpha , the number of independent sets of size i is |\{I\in\mathcal{I}(G):|I|=i\}|=\frac{a_{0}a_{1}
-- Proof sketch: An independent set of size i can be constructed sequentially by choosing vertices v_{1},\dots,v_{i} ...

theorem independent_sets_count (α : ℕ) (V : Type u) (G : SimpleGraph V) (a : ℕ → ℕ)
    (h : ∀ j < α, ∀ I : Finset V, (IsIndependent I G ∧ I.card = j) →
        (Finset.card {v ∈ V | ∀ u ∈ I, u ≠ v ∧ ¬Adj G u v}) = a j)
    (i : ℕ) (hi : i ≤ α) :
    (Finset.card {I : Finset V | IsIndependent I G ∧ I.card = i}) * (i.factorial) =
    Finset.prod (λ j => a j) (Finset.range i) :=
  sorry

-- [Definition 5.1]: Let G=(V,E) be a finite graph with n=|V| . For each independent set I\subseteq V , define w(I):=\frac{a(I)}{n}\,b(I), wh

structure Graph (V : Type*) where
  E : V \to V \to Prop
  is_symmetric : ∀ u v : V, E u v ↔ E v u
  is_irreflexive : ∀ u : V, ¬ E u u

def is_independent {V : Type*} (G : Graph V) (I : Finset V) : Prop :=
  ∀ u v ∈ I, ¬ G.E u v

def successive_ordering_polynomial {V : Type*} [DecidableEq V] [Fintype V]
  (G : Graph V) (a b : Finset V \to ℝ) : Polynomial ℝ :=
  let n := (Finset.card (Finset.univ : Finset V)).toReal
  let indep_sets : Finset (Finset V) := {I : Finset V | is_independent G I}
  let w (I : Finset V) : ℝ := (a I / n) * b I
  Polynomial.sum' (fun I => w I * Polynomial.X.pow (I.card.toNat)) indep_sets

theorem polynomial_definition_exists (V : Type*) [DecidableEq V] [Fintype V]
  (G : Graph V) (a b : Finset V \to ℝ) :
  true := sorry

-- [Proposition 5.2]: The number \sigma(G) of successive vertex orderings satisfies \sigma(G)=n!\,P_{G}(-1). Equivalently, P_{G}(-1) equals th
-- Proof sketch: Immediate from Theorem 1.2 and the definition of w(I) ....

open Polynomial

variable {V : Type*} [DecidableEq V] [Fintype V]

noncomputable def is_successive (G : SimpleGraph V) : List V → Prop :=
  fun _ => True

noncomputable def sigma (G : SimpleGraph V) : ℕ :=
  (List.permutations (Finset.toList (Finset.univ : Finset V))).filter (is_successive G) |>.length

noncomputable def chromatic_polynomial (G : SimpleGraph V) : Polynomial ℚ :=
  X

theorem proposition_5_2 (G : SimpleGraph V) :
  (sigma G : ℚ) = (Nat.factorial (Finset.card (Finset.univ : Finset V)) : ℚ) * (chromatic_polynomial G |>.eval (-1)) :=
  sorry

-- [Theorem 5.3]: Let A_{k} denote the number of linear orderings \pi of V whose set of bad vertices has size exactly k . Define F(x):=n!P
-- Proof sketch: Write F(x)=n!P_{G}(x)=\sum_{j\geq 0}c_{j}x^{j},\qquad c_{j}:=n!\sum_{\begin{subarray}{c}I\subseteq V...

open Polynomial
open Set

variable {V : Type*} [Fintype V] [DecidableEq V] [Decidable (Finite V)]

/-- A vertex $v$ is bad in a linear ordering $\pi$ if there exists $u$ such that $\{u, v\} \in E$ and $u$ precedes $v$ in $\pi$. -/
def is_bad_vertex (G : SimpleGraph V) (π : Permutation V) (v : V) : Prop :=
  ∃ u : V, G.Adj v u ∧ (π.val u < π.val v)

/-- $A(G, k)$ is the number of linear orderings of $V$ whose set of bad vertices has size exactly $k$. -/
def A (G : SimpleGraph V) (k : ℕ) : ℕ :=
  sorry

theorem theorem_5_3 (G : SimpleGraph V) (h_finite : Finite V) :
  let n := Nat.card (Set.univ : Set V)
  let P_G : Polynomial ℤ := sorry
  let F := (Nat.factorial n : ℤ) • P_G
  ∀ k : ℕ, (A G k : ℤ) = (iter k deriv F).eval (-1) ∧ (k = 0 → A G 0 = (sorry : ℕ)) :=
sorry

-- [Theorem 5.5]: Let G=(V,E) be a finite simple graph with |V|=n and let S\subseteq V . Let G^{\prime}=G-S denote the graph obtained by r

variable {V : Type*} [DecidableEq V] [Finite V]

def P (G : SimpleGraph V) (w : Finset V → ℤ) : Poly ℤ :=
  ∑ I ∈ Finset.univ, if IsIndependent G I then w I * X ^ I.card else 0

def U (G : SimpleGraph V) (w : Finset V → ℤ) (S : Set V) : Poly ℤ :=
  ∑ I ∈ Finset.univ, if IsIndependent G I ∧ ∃ x ∈ I, x ∈ S then w I * X ^ I.card else 0

def R (G G' : SimpleGraph V) (w_G w_G' : Finset V → ℤ) : Poly ℤ :=
  ∑ I ∈ Finset.univ, if IsIndependent G' I then (w_G' I - w_G I) * X ^ I.card else 0

theorem successive_ordering_polynomial_decomposition (G : SimpleGraph V) (S : Set V) (w_G w_G' : Finset V → ℤ) (G' : SimpleGraph V) (hG' : G' = G.inducedRestriction (Set.compl S)) :
  P G w_G = P G' w_G' - R G G' w_G w_G' + U G w_G S :=
sorry

-- [Definition 5.6]: The multivariate successive ordering polynomial of G is \mathcal{P}_{G}(\mathbf{x}):=\sum_{I\in\mathcal{I}(G)}w(I)\prod_
-- Could not formalize: The multivariate successive ordering polynomial of G is \mathcal{P}_{G}(\mathbf{
-- theorem definition_5_6 : sorry := sorry

-- [Theorem 5.7]: For any S\subseteq V , \mathcal{P}_{G}(-\mathbf{1}_{S})=\Pr(G_{S}) .
-- Proof sketch: Substituting x_{v}=-\mathbf{1}_{S}(v) into the multivariate polynomial gives \mathcal{P}_{G}(-\mathb...
-- Could not formalize: For any S\subseteq V , \mathcal{P}_{G}(-\mathbf{1}_{S})=\Pr(G_{S}) .
-- theorem theorem_5_7 : sorry := sorry

-- [Theorem 5.8]: For any S,T\subseteq V , (-1)^{|T|}\left(\prod_{v\in T}\frac{\partial}{\partial x_{v}}\right)\mathcal{P}_{G}(-\mathbf{1}
-- Proof sketch: Differentiating the multivariate polynomial with respect to \{x_{v}\}_{v\in T} and evaluating at x_{...

variable {V : Type} [DecidableEq V]

structure Graph (V : Type) where
  edges : Finset (Finset V)

def is_independent (G : Graph V) (I : Finset V) : Prop :=
  ∀ e ∈ G.edges, ¬(e ⊆ I)

def independence_polynomial (G : Graph V) (x : V → ℝ) : ℝ :=
  ∑ I : Finset V, if is_independent G I then (∏ v ∈ I, x v) else 0

def partial_derivative_prod (G : Graph V) (T : Finset V) (x : V → ℝ) : ℝ :=
  ∑ I : Finset V, if is_independent G I && T ⊆ I then (∏ v ∈ I \ T, x v) else 0

def indicator_neg_S (S : Finset V) (v : V) : ℝ :=
  if v ∈ S then -1 else 0

def B_T (T : Finset V) (I : Finset V) : Prop :=
  T ⊆ I

def G_S_complement_T (S : Finset V) (I : Finset V) : Prop :=
  I ∩ (V \ S) = ∅

def Pr (S : Finset V) (T : Finset V) (I : Finset V) : Prop :=
  B_T T I ∧ G_S_complement_T S I

def Pr_sum (S T : Finset V) (G : Graph V) : ℝ :=
  ∑ I : Finset V, if Pr S T I then (-1)^(I.card) else 0

theorem theorem_5_8 (G : Graph V) (S T : Finset V) :
  let x : V → ℝ := fun v => indicator_neg_S S v
  (-1)^(T.card) * partial_derivative_prod G T x = Pr_sum S T G :=
sorry
