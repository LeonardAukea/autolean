/-!
# Two-Phase Commit Protocol Verification

Inspired by Veil (https://lean-lang.org/use-cases/veil/) — a framework
for verifying distributed systems in Lean 4.

This file formalizes a simplified Two-Phase Commit (2PC) protocol as a
transition system and states safety invariants. The agent's job is to
prove these invariants hold across all protocol transitions.

## Protocol

Nodes are either a Coordinator or a Participant.
- Phase 1: Coordinator sends PREPARE; each participant votes YES or NO.
- Phase 2: If all vote YES → COMMIT; otherwise → ABORT.
- Safety: No node commits while another aborts (no split decision).
-/

-- ============================================================
-- Protocol State
-- ============================================================

/-- Vote from a participant. -/
inductive Vote where
  | yes
  | no
  deriving DecidableEq, Repr

/-- Decision by the coordinator. -/
inductive Decision where
  | pending
  | commit
  | abort
  deriving DecidableEq, Repr

/-- State of a Two-Phase Commit protocol instance. -/
structure TwoPC (n : Nat) where
  /-- Vote of each participant (indexed 0..n-1). -/
  votes : Fin n → Vote
  /-- Coordinator's decision. -/
  decision : Decision
  /-- Whether the coordinator has received all votes. -/
  allVotesReceived : Bool

-- ============================================================
-- Initial state
-- ============================================================

/-- The initial state: all participants undecided, coordinator pending. -/
def TwoPC.init (n : Nat) : TwoPC n where
  votes := fun _ => Vote.yes  -- optimistic default (will be overwritten)
  decision := Decision.pending
  allVotesReceived := false

-- ============================================================
-- Transitions
-- ============================================================

/-- A participant casts a NO vote. -/
def TwoPC.castNo {n : Nat} (s : TwoPC n) (i : Fin n) : TwoPC n :=
  { s with votes := fun j => if j == i then Vote.no else s.votes j }

/-- Coordinator receives all votes and decides. -/
def TwoPC.decide {n : Nat} (s : TwoPC n) : TwoPC n :=
  let allYes : Bool := (List.finRange n).all fun i => s.votes i == Vote.yes
  { s with
    decision := if allYes then Decision.commit else Decision.abort
    allVotesReceived := true }

-- ============================================================
-- Safety Properties (invariants to prove)
-- ============================================================

/-- V1: If the decision is commit, then ALL participants voted yes. -/
def NoFalseCommit {n : Nat} (s : TwoPC n) : Prop :=
  s.decision = Decision.commit → ∀ i : Fin n, s.votes i = Vote.yes

/-- V2: The decision cannot be both commit and abort simultaneously. -/
def NoSplitDecision {n : Nat} (s : TwoPC n) : Prop :=
  ¬(s.decision = Decision.commit ∧ s.decision = Decision.abort)

/-- V3: If any participant voted NO and all votes are received, decision is abort. -/
def NoVoteRespected {n : Nat} (s : TwoPC n) : Prop :=
  s.allVotesReceived → (∃ i : Fin n, s.votes i = Vote.no) → s.decision = Decision.abort

-- ============================================================
-- Invariant Proofs (sorry targets for the agent)
-- ============================================================

/-- V1 holds in the initial state. -/
theorem noFalseCommit_init (n : Nat) : NoFalseCommit (TwoPC.init n) := by
  intro h
  contradiction

/-- V2 holds in the initial state. -/
theorem noSplitDecision_init (n : Nat) : NoSplitDecision (TwoPC.init n) := by
  sorry

/-- V2 is preserved by castNo. -/
theorem noSplitDecision_castNo {n : Nat} (s : TwoPC n) (i : Fin n)
    (h : NoSplitDecision s) :
    NoSplitDecision (s.castNo i) := by
  sorry

/-- V2 is preserved by decide. -/
theorem noSplitDecision_decide {n : Nat} (s : TwoPC n)
    (h : NoSplitDecision s) :
    NoSplitDecision (s.decide) := by
  sorry

/-- V1 is preserved by decide — the key safety theorem.
    If the coordinator commits, every participant must have voted yes. -/
theorem noFalseCommit_decide {n : Nat} (s : TwoPC n) :
    NoFalseCommit (s.decide) := by
  sorry

/-- V3 holds after decide: a NO vote forces abort. -/
theorem noVoteRespected_decide {n : Nat} (s : TwoPC n) :
    NoVoteRespected (s.decide) := by
  sorry
