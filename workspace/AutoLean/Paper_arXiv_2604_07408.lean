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
import Mathlib.Data.Finset.Basic
import Mathlib.Data.Finset.Pow
import Mathlib.Data.Finset.Powerset
import Mathlib.Data.Fintype.Basic
import Mathlib.Data.Int.Basic
import Mathlib.Data.Nat.Card
import Mathlib.Data.Nat.Factorial.Basic
import Mathlib.Data.Permutation.Basic
import Mathlib.Data.Polynomial.Basic
import Mathlib.Data.Polynomial.Deriv
import Mathlib.Data.Real.Basic
import Mathlib.Data.Set.Basic
import Mathlib.Data.Set.Finite
import Mathlib.Data.Set.PowerSet
import Mathlib.GraphTheory.SimpleGraph.Basic
import Mathlib.Order.Basic

-- [Definition 1.1]: A linear ordering \pi of V is said to be successive if for every vertex v\in V with \pi(v)>1 , there exists a neighbour 

def is_successive {V : Type u} {L : Type u} [LinearOrder L] (G : SimpleGraph V) (π : V → L) (one : L) : Prop :=
  ∀ v : V, one < π v → ∃ u : V, u ∈ G.adj v ∧ π u < π v

theorem successive_property_claim : True := by
  sorry

-- [Theorem 1.2]: Let G=(V,E) be a finite connected graph with |V|=n . Then the number \sigma(G) of successive vertex orderings of G is gi
-- Could not formalize: Let G=(V,E) be a finite connected graph with |V|=n . Then the number \sigma(G) o
-- theorem theorem_1_2 : sorry := sorry

-- [Proposition 3.1]: For every U\subseteq V , \Pr(G_{U})=\sum_{\begin{subarray}{c}I\subseteq U\\ I\ \mathrm{independent}\end{subarray}}(-1)^{
-- Proof sketch: By De Morgan’s law and the inclusion–exclusion principle we have the identity \Pr(G_{U})=\Pr\Bigl(\b...

structure Graph (V : Type u) where
  edges : V → V → Prop

def is_independent {V : Type u} (g : Graph V) (I : Set V) : Prop :=
  ∀ u v, u ∈ I → v ∈ I → ¬ g.edges u v

theorem proposition_3_1
  {V : Type u} [Fintype V] (g : Graph V) (U : Set V)
  (a b : Set V → ℝ) (n : ℝ) (n_nonzero : n ≠ 0)
  (Pr_B : Set V → ℝ) (Pr_G : Set V → ℝ)
  (h_inclusion_exclusion : ∀ U : Set V, Pr_G U = ∑ I \in (powerset U), (-1 : ℝ)^(card I) * Pr_B I)
  (h_Pr_B : ∀ I : Set V, Pr_B I = if is_independent g I then (a I * b I) / n else 0) :
  Pr_G U = ∑ I \in (powerset U), if is_independent g I then (-1 : ℝ)^(card I) * (a I * b I / n) else 0 :=
sorry

-- [Corollary 3.2]: For every independent set J\subseteq V , \Pr(B_{J})=\frac{a(J)\,b(J)}{n}=\sum_{T\subseteq J}(-1)^{|T|}\Pr(G_{T}) (3.2)
-- Proof sketch: The second equality is obtained by applying Möbius inversion to ( 3.1 )....

theorem corollary_3_2 (V : Type*) [Fintype V]
  (is_independent : Finset V → Prop)
  (a b probB probG : Finset V → ℝ)
  (n : ℝ) (J : Finset V)
  (h_indep : is_independent J)
  (h_n : n = (Finset.univ.card : ℝ))
  (h_nonzero : n ≠ 0) :
  probB J = (a J * b J) / n ∧ probB J = ∑ T in J.powerset, (-1 : ℝ) ^ T.card * probG T :=
sorry

-- [Lemma 4.1]: For each 0\leq i\leq\alpha , the number of independent sets of size i is |\{I\in\mathcal{I}(G):|I|=i\}|=\frac{a_{0}a_{1}
-- Proof sketch: An independent set of size i can be constructed sequentially by choosing vertices v_{1},\dots,v_{i} ...

variable {V : Type} (G : SimpleGraph V) (a : ℕ → ℕ) (α : ℕ)

def FullRegularity (G : SimpleGraph V) (a : ℕ → ℕ) (α : ℕ) :=
  ∀ (S : Set V), IsIndependent S → S.card < α →
    (card {v : V | v ∉ S ∧ ∀ u ∈ S, ¬(v ⊥ u)}) = a S.card

theorem independent_sets_count (G : SimpleGraph V) (a : ℕ → ℕ) (α : ℕ)
  (h_alpha : V.card = α) (h_reg : FullRegularity G a α) (i : ℕ) (h_i : i ≤ α) :
  (card {I : Set V | IsIndependent I ∧ I.card = i}) =
    (Finset.prod (fun j => a j) (Finset.range i)) / Nat.factorial i :=
sorry

-- [Definition 5.1]: Let G=(V,E) be a finite graph with n=|V| . For each independent set I\subseteq V , define w(I):=\frac{a(I)}{n}\,b(I), wh

open BigOperators
open Set

variable {α : Type*} [Fintype α] [DecidablePred (α → Prop)] [DecidablePred (Set α → Prop)]

noncomputable def successive_ordering_polynomial (G : SimpleGraph α) (a b : Finset α \to ℝ) : Polynomial ℝ :=
  let n := (Fintype.card α) : ℝ
  let indep_sets := (Finset.univ.powerset).filter (fun I => IsIndependent G (Set.tally I))
  ∑ (I : Finset (Finset α)) in indep_sets, (Polynomial.singleton 0 * (a I / n * b I)) * (Polynomial.X ^ (I.card : ℕ))

theorem polynomial_exists (G : SimpleGraph α) (a b : Finset α \to ℝ) (h : Fintype.card α ≠ 0) :
  True := by
  sorry

-- [Proposition 5.2]: The number \sigma(G) of successive vertex orderings satisfies \sigma(G)=n!\,P_{G}(-1). Equivalently, P_{G}(-1) equals th
-- Proof sketch: Immediate from Theorem 1.2 and the definition of w(I) ....

open SimpleGraph
open Polynomial

noncomputable def sigma (G : SimpleGraph ℕ) : ℕ :=
  sorry

noncomputable def P_G (G : SimpleGraph ℕ) : ℤ[X] :=
  sorry

theorem successive_ordering_identity (G : SimpleGraph ℕ) :
  sigma G = Nat.factorial G.vertexSet.card * (P_G G).eval (-1) :=
sorry

-- [Theorem 5.3]: Let A_{k} denote the number of linear orderings \pi of V whose set of bad vertices has size exactly k . Define F(x):=n!P
-- Proof sketch: Write F(x)=n!P_{G}(x)=\sum_{j\geq 0}c_{j}x^{j},\qquad c_{j}:=n!\sum_{\begin{subarray}{c}I\subseteq V...

variable {V : Type*} [Fintype V] [DecidableEq V] [Decidable (Finset.univ : Finset V)]

open Polynomial

def bad_vertices (G : SimpleGraph V) (π : Sym V) : Finset V :=
  { v | ∃ u, (π.sym u < π.sym v) ∧ u ∈ Adj G v }

def A (G : SimpleGraph V) (k : ∃ (n : ℕ), n = (Finset.univ : Finset V).card ∧ k < n) : ℕ :=
  (Finset.univ.filter (fun π => (bad_vertices G π).card = (Nat.get k)) : Finset (Sym V)).card

def sigma_G (G : SimpleGraph V) : ℕ :=
  let edges := (Finset.univ : Finset V).adj G
  let orientations := Finset.univ.filter (fun (E_dir : Finset (Sym V × Sym V)) =>
    ∀ u v, (u, v) ∈ E_dir ↔ (v, u) ∉ E_dir ∧ (∃ e, e = (u, v) ∨ e = (v, u)) ∧ (u ∈ Finset.univ ∧ v ∈ Finset.univ)
  ) -- This is a simplification; actual acyclic orientation logic is complex.
  -- For the purpose of the claim, we define it as the number of acyclic orientations.
  0 -- Placeholder for the actual count of acyclic orientations.

theorem theorem_5_3 (G : SimpleGraph V) (P_G : Polynomial ℤ) (k : ℕ) (h_k : k < (Finset.univ : Finset V).card) :
    let n := (Finset.univ : Finset V).card
    let F := (n! : ℤ) • P_G
    let A_k := (Finset.univ.filter (fun π => (bad_vertices G π).card = k) : Finset (Sym V)).card
    A_k = (iter k deriv F |> eval (-1)) / (k! : ℤ) ∧
    (Finset.univ.filter (fun π => (bad_vertices G π).card = 0) : Finset (Sym V)).card = 0 :=
sorry

-- [Theorem 5.5]: Let G=(V,E) be a finite simple graph with |V|=n and let S\subseteq V . Let G^{\prime}=G-S denote the graph obtained by r
-- Could not formalize: Let G=(V,E) be a finite simple graph with |V|=n and let S\subseteq V . Let G^{\p
-- theorem theorem_5_5 : sorry := sorry

-- [Definition 5.6]: The multivariate successive ordering polynomial of G is \mathcal{P}_{G}(\mathbf{x}):=\sum_{I\in\mathcal{I}(G)}w(I)\prod_

open Finset

variable {α : Type*} [Fintype α] [DecidableEq α] [Decidable (IsIndependent.mem)] {R : Type*} [CommRing R]

def multivariate_successive_ordering_polynomial (G : SimpleGraph α) (w : Finset α → R) (x : α → R) : R :=
  (Finset.univ.filter (fun I => IsSimpleGraph.IsIndependent.mem I G) : Finset (Finset α)).sum (fun I => w I * (∏ v in I, x v))

theorem multivariate_successive_ordering_polynomial_def (G : SimpleGraph α) (w : Finset α → R) (x : α → R) :
  multivariate_successive_ordering_polynomial G w x =
  ∑ (I : Finset α), (if IsSimpleGraph.IsIndependent.mem I G then w I * (∏ v in I, x v) else 0) := by
  sorry

-- [Theorem 5.7]: For any S\subseteq V , \mathcal{P}_{G}(-\mathbf{1}_{S})=\Pr(G_{S}) .
-- Proof sketch: Substituting x_{v}=-\mathbf{1}_{S}(v) into the multivariate polynomial gives \mathcal{P}_{G}(-\mathb...

structure WeightedGraph (V : Type u) where
  Indep : Finset (Finset V)
  w : Finset V → ℤ

def PartitionFunction (V : Type u) (G : WeightedGraph V) (x : V → ℤ) : ℤ :=
  ∑ I in G.Indep, G.w I * ∏ v in I, x v

def Pr (V : Type u) (G : WeightedGraph V) (S : Set V) : ℤ :=
  ∑ I in G.Indep.filter (λ I => I ⊆ S), G.w I * (-1 : ℤ) ^ (card I)

theorem theorem_5_7 (V : Type u) (G : WeightedGraph V) (S : Set V) :
  PartitionFunction V G (fun v => if v ∈ S then -1 else 0) = Pr V G S := by
  sorry

-- [Theorem 5.8]: For any S,T\subseteq V , (-1)^{|T|}\left(\prod_{v\in T}\frac{\partial}{\partial x_{v}}\right)\mathcal{P}_{G}(-\mathbf{1}
-- Proof sketch: Differentiating the multivariate polynomial with respect to \{x_{v}\}_{v\in T} and evaluating at x_{...

open Finset
open BigOperators

structure Graph (V : Finset ℕ) where
  E : Finset (Finset ℕ)
  is_independent : Finset ℕ → Prop
  is_independent I := ∀ e ∈ E, e ⊆ I → False

def partial_derivative_T (V : Finset ℕ) (G : Graph V) (T : Finset ℕ) (x : ℕ → ℤ) : ℤ :=
  ∑ I : Finset ℕ, (if G.is_independent I ∧ T ⊆ I then 1 else 0) * (∏ u ∈ I.erase T, x u)

def indicator_neg_S (V : ℕ) (S : Finset ℕ) : ℕ → ℤ :=
  fun v => if v ∈ S then -1 else 0

def prob_event (V : Finset ℕ) (G : Graph V) (S T : Finset ℕ) : ℤ :=
  ∑ I : Finset ℕ, (if G.is_independent I ∧ T ⊆ I ∧ I ⊆ S then 1 else 0) * ((-1) ^ I.card)

theorem theorem_5_8 (V : Finset ℕ) (G : Graph V) (S T : Finset ℕ) (hT : T ⊆ V) (hS : S ⊆ V) :
  (-1) ^ T.card * partial_derivative_T V G T (indicator_neg_S 0 S) = prob_event V G S T :=
sorry
