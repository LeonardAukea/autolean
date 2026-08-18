import Mathlib

/-!
# Two-Phase Commit Safety

The state records each participant's vote, the coordinator's decision, and
the decision visible to each participant. Transition constructors carry the
preconditions of the protocol action. Reachability is the reflexive sequence
of those transitions from `TwoPC.init`.
-/

/-- A participant's final vote. -/
inductive Vote where
  | yes
  | no
  deriving DecidableEq, Repr

/-- A participant waits until it casts one final vote. -/
inductive VoteState where
  | waiting
  | cast (vote : Vote)
  deriving DecidableEq, Repr

/-- A local protocol decision. -/
inductive Decision where
  | pending
  | commit
  | abort
  deriving DecidableEq, Repr

/-- The local states of one two-phase commit instance. -/
structure TwoPC (n : Nat) where
  votes : Fin n → VoteState
  coordinator : Decision
  participants : Fin n → Decision

/-- Every participant has voted yes. -/
def AllYes {n : Nat} (s : TwoPC n) : Prop :=
  ∀ i, s.votes i = VoteState.cast Vote.yes

/-- Every participant has cast a final vote. -/
def AllVotesReceived {n : Nat} (s : TwoPC n) : Prop :=
  ∀ i, ∃ vote, s.votes i = VoteState.cast vote

/-- At least one participant has voted no. -/
def HasNoVote {n : Nat} (s : TwoPC n) : Prop :=
  ∃ i, s.votes i = VoteState.cast Vote.no

/-- Every local component starts without a vote or decision. -/
def TwoPC.init (n : Nat) : TwoPC n where
  votes := fun _ => VoteState.waiting
  coordinator := Decision.pending
  participants := fun _ => Decision.pending

/-- The legal atomic transitions of two-phase commit. -/
inductive Step {n : Nat} : TwoPC n → TwoPC n → Prop where
  | castVote (s : TwoPC n) (i : Fin n) (vote : Vote)
      (coordinatorPending : s.coordinator = Decision.pending)
      (participantWaiting : s.votes i = VoteState.waiting) :
      Step s { s with votes := Function.update s.votes i (.cast vote) }
  | commit (s : TwoPC n)
      (coordinatorPending : s.coordinator = Decision.pending)
      (allYes : AllYes s) :
      Step s { s with coordinator := Decision.commit }
  | abort (s : TwoPC n)
      (coordinatorPending : s.coordinator = Decision.pending)
      (allVotesReceived : AllVotesReceived s)
      (someVoteWasNotYes : ¬AllYes s) :
      Step s { s with coordinator := Decision.abort }
  | deliver (s : TwoPC n) (i : Fin n)
      (decisionFinal : s.coordinator ≠ Decision.pending) :
      Step s {
        s with
        participants := Function.update s.participants i s.coordinator
      }

/-- A commit decision is backed by unanimous yes votes. -/
def NoFalseCommit {n : Nat} (s : TwoPC n) : Prop :=
  s.coordinator = Decision.commit → AllYes s

/-- Every delivered decision agrees with the coordinator. -/
def NoSplitDecision {n : Nat} (s : TwoPC n) : Prop :=
  ∀ i,
    s.participants i = Decision.pending ∨
      s.participants i = s.coordinator

/-- The safety invariant carried by every reachable state. -/
def Safe {n : Nat} (s : TwoPC n) : Prop :=
  NoFalseCommit s ∧ NoSplitDecision s

/-- The initial state satisfies the safety invariant. -/
theorem safe_init (n : Nat) : Safe (TwoPC.init n) := by
  constructor
  · intro h
    simp [TwoPC.init] at h
  · intro i
    exact Or.inl rfl

/-- Casting a vote preserves safety. -/
theorem safe_castVote {n : Nat} (s : TwoPC n) (i : Fin n) (vote : Vote)
    (hsafe : Safe s)
    (hpending : s.coordinator = Decision.pending)
    (_hwaiting : s.votes i = VoteState.waiting) :
    Safe { s with votes := Function.update s.votes i (.cast vote) } := by
  constructor
  · intro hcommit
    simp only at hcommit
    rw [hpending] at hcommit
    contradiction
  · exact hsafe.2

/-- A unanimous coordinator commit preserves safety. -/
theorem safe_commit {n : Nat} (s : TwoPC n)
    (hsafe : Safe s)
    (hpending : s.coordinator = Decision.pending)
    (hallYes : AllYes s) :
    Safe { s with coordinator := Decision.commit } := by
  constructor
  · intro hcommit
    exact hallYes
  · intro i
    rcases hsafe.2 i with hpendingParticipant | hagrees
    · exact Or.inl hpendingParticipant
    · exact Or.inl (hagrees.trans hpending)

/-- A coordinator abort preserves safety. -/
theorem safe_abort {n : Nat} (s : TwoPC n)
    (hsafe : Safe s)
    (hpending : s.coordinator = Decision.pending) :
    Safe { s with coordinator := Decision.abort } := by
  constructor
  · intro hcommit
    contradiction
  · intro i
    rcases hsafe.2 i with hpendingParticipant | hagrees
    · exact Or.inl hpendingParticipant
    · exact Or.inl (hagrees.trans hpending)

/-- Delivering the coordinator's final decision preserves safety. -/
theorem safe_deliver {n : Nat} (s : TwoPC n) (i : Fin n)
    (hsafe : Safe s) :
    Safe {
      s with
      participants := Function.update s.participants i s.coordinator
    } := by
  constructor
  · exact hsafe.1
  · intro j
    by_cases hji : j = i
    · subst j
      exact Or.inr (by simp)
    · simpa [Function.update, hji] using hsafe.2 j

/-- Every legal transition preserves safety. -/
theorem Step.preservesSafe {n : Nat} {before after : TwoPC n}
    (hstep : Step before after) (hsafe : Safe before) : Safe after := by
  cases hstep with
  | castVote i vote hpending hwaiting =>
      exact safe_castVote _ i vote hsafe hpending hwaiting
  | commit hpending hallYes =>
      exact safe_commit _ hsafe hpending hallYes
  | abort hpending _ _ =>
      exact safe_abort _ hsafe hpending
  | deliver i _ =>
      exact safe_deliver _ i hsafe

/-- A state is reachable by legal transitions from the initial state. -/
inductive Reachable {n : Nat} : TwoPC n → Prop where
  | init : Reachable (TwoPC.init n)
  | next {before after : TwoPC n} :
      Reachable before → Step before after → Reachable after

/-- Every reachable two-phase commit state is safe. -/
theorem reachable_safe {n : Nat} {s : TwoPC n} (hreachable : Reachable s) :
    Safe s := by
  induction hreachable with
  | init => exact safe_init n
  | next _ hstep ih => exact hstep.preservesSafe ih

/-- A final coordinator decision respects every no vote in a safe state. -/
theorem noVoteRespected {n : Nat} {s : TwoPC n}
    (hsafe : Safe s) (hno : HasNoVote s)
    (hfinal : s.coordinator ≠ Decision.pending) :
    s.coordinator = Decision.abort := by
  rcases hno with ⟨i, hno⟩
  cases hdecision : s.coordinator with
  | pending => exact (hfinal hdecision).elim
  | commit =>
      have hyes := hsafe.1 hdecision i
      simp_all
  | abort => rfl

/-- Every reachable final decision respects every no vote. -/
theorem reachable_noVoteRespected {n : Nat} {s : TwoPC n}
    (hreachable : Reachable s) (hno : HasNoVote s)
    (hfinal : s.coordinator ≠ Decision.pending) :
    s.coordinator = Decision.abort := by
  exact noVoteRespected (reachable_safe hreachable) hno hfinal
