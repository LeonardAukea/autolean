# Research and proof loop

The loop is built around small experiments. One attempt should either produce
an accepted proof or leave evidence that changes the next attempt.

## A plan is an object

Before source generation, AutoLean can record:

- the objective
- the proposed formalization
- examples and special cases
- invariants
- obstructions
- reductions
- premises to verify
- candidate methods
- partial results
- risks
- completion criteria
- checkpoints
- revision triggers

These fields keep mathematical choices reviewable. A long narrative can hide
a bad quantifier; an explicit formalization field cannot.

## One experiment

Before any model is asked, a fixed list of tactics is tried against the
target. A tactic that closes the goal is written, validated, and committed
through the same sandbox, declaration, and axiom audit as any other
candidate, recorded against a `deterministic-tactic-search` identity. Cheap
goals never reach a provider.

Every cycle then performs the same steps:

1. Select the highest-priority target within the requested scope.
2. Read its Lean goal and bounded structural context.
3. Add learned skills and prior failure evidence.
4. Ask the active model for one candidate proof under the target's strategy.
5. Apply the source policy and sandboxed Lean validation.
6. Record the candidate, outcome, and provenance.
7. Install only an accepted candidate, then rescan.

The first cycle on a target also runs its fixed-budget local search and asks
for a proof strategy. Both are cached for that target and requested again
only after a model transition, so later cycles vary the candidate rather than
the plan.

The order is stable. Prompt layers are separate: system rules, project
guidance, and ephemeral target evidence have different ownership and hashes.

The complete cycle, including what failure feeds back into the next attempt:

```mermaid
flowchart TD
    select["select highest-priority target"]
    context["read Lean goal +<br/>bounded structural context"]
    evidence["add fixed-budget search,<br/>learned skills, prior failures"]
    strategy["model strategy<br/>for this target"]
    candidate["one candidate proof<br/>under that strategy"]
    validate["source policy +<br/>sandboxed Lean validation"]
    record["record plan, candidate,<br/>outcome, provenance"]
    install["install accepted source,<br/>commit, rescan"]
    classify["classify diagnostics:<br/>syntax / elaboration / tactic /<br/>environment / structural"]
    stopTarget(["stop this target:<br/>same error, no new evidence"])
    stopRun(["stop the run:<br/>auth / quota / network / project"])
    escalate["bounded model escalation:<br/>at most one switch per invocation"]

    select --> context --> evidence --> strategy --> candidate
    candidate --> validate --> record
    record -- "accepted" --> install --> select
    record -- "rejected" --> classify
    classify -- "new evidence" --> evidence
    classify -- "repeated error" --> stopTarget --> select
    classify -- "environment failure" --> stopRun
    classify -- "eligible proof failures" --> escalate --> candidate
```

Each box is a recorded step. One experiment row per attempt holds the
candidate, the classified outcome, and the prompt, strategy, environment, and
proof identities; the session holds the scope, model routing, and cycle budget
that let a stopped run resume. The
[artifact reference](../reference/research-artifacts.md) states where each
lives, so a stopped run can be inspected and continued without losing
evidence.

## Failure changes the next question

Lean diagnostics are classified into syntax, elaboration, tactic, environment,
and structural categories. Repeating the same error without new evidence ends
work on that target. Authentication, quota, network, and project failures stop
the run because they say nothing about the mathematics.

A smaller model may recommend a stronger profile after eligible proof failures.
The switch is bounded to one per invocation. It retains the target, attempt
budget, and recorded evidence, and asks the incoming model to plan the target
again rather than inheriting a plan it did not make. Model size is a resource
decision; repairing the formalization is the other one.

## Success compounds into skills

An accepted proof also changes future questions. The skill memory extracts
the proof's tactic sequence, names the pattern it instantiates — computation
by `rfl`, simplification, induction with case analysis, arithmetic closure —
and persists a small record: the tactics, an applicability condition, the
theorem that produced it, and success counts.

Before each attempt, stored skills are ranked against the current goal state
by keyword relevance weighted by historical success rate; the strongest few
enter the prompt as named patterns. A pattern that keeps closing goals rises.
A pattern that stops working fades.

```mermaid
flowchart LR
    accepted["accepted proof"] --> extract["extract tactic<br/>sequence"]
    extract --> classify["name the<br/>pattern"]
    classify --> store[("skill record:<br/>tactics, condition,<br/>success counts")]
    store --> rank["rank against the<br/>next goal state"]
    rank --> inject["strongest patterns<br/>enter the prompt"]
    inject --> validate["candidate validated by<br/>policy, sandbox, Lean"]
    validate -- "accepted" --> store
```

Skills are evidence, not authority. An injected pattern can only shape a
proposal; the candidate it shapes passes the same source policy, sandbox,
elaboration, and audit as any other ([trust boundary](trust-boundary.md)).

## Sessions outlive commands

Every mutating workflow writes an atomic session record in the Lean project;
the [artifact reference](../reference/research-artifacts.md) states its
location and shape. `resume` continues the latest active session or an
explicit ID. Guidance and model choice can change without losing the evidence
already gathered.

A run is bounded by default; unbounded work requires `--overnight` or an
explicit zero budget ([`max_cycles`](../reference/program.md)). This makes a
normal command cheap to stop, inspect, and continue.

## Mathematical discipline

Examples test the statement before proof search. Obstructions expose missing
hypotheses. Reductions name the smaller claim that would settle the goal.
Premises are checked against the local project and Mathlib before a model is
asked to improvise.

This structure follows Terence Tao's practical advice on [examples and special
cases][examples], [skeptical checking][skepticism], [partial progress][partial],
and [flexible plans][flexibility].
Research briefs bind definitions and semantic boundaries to primary sources;
the [Gromov archive](https://www.ihes.fr/~gromov/) is one source used by the
curated geometry work.

The implementation also draws on the fixed-budget experiment discipline in
[autoresearch](https://github.com/karpathy/autoresearch), the bounded context
and durable-state model in
[Hermes Agent](https://github.com/NousResearch/hermes-agent), and the explicit
done conditions described in
[Loop Engineering](https://addyosmani.com/blog/loop-engineering/).

The kernel settles the formal theorem. A distinguished mathematical workflow
also asks whether the theorem is the right one. Plans, source citations,
counterexamples, and human review own that question.

[examples]: https://terrytao.wordpress.com/career-advice/solving-mathematical-problems/
[skepticism]: https://terrytao.wordpress.com/career-advice/be-sceptical-of-your-own-work/
[partial]: https://terrytao.wordpress.com/career-advice/on-the-importance-of-partial-progress/
[flexibility]: https://terrytao.wordpress.com/career-advice/be-flexible/
