# Investigate Gromov's residual-finiteness question

Does every word-hyperbolic group have enough finite quotients to distinguish
its elements? This is an open research question. The example separates two
supporting lemmas from the open implication, so each accepted proof records
a precise piece of progress.

Gromov discusses the question in [Hyperbolic Groups (1987), p. 141][gromov].
Its open status is stated by [Gardam, Kielak and Logan (2026)][status].

![An accepted supporting proof and an unresolved Gromov question][recording]

This live GPT-6 Astra `max` run accepts the finite-quotient converse, reuses
its learned pattern, and leaves the open implication unresolved. The
[recorder](../../scripts/record_gromov_demo.py) supplies explicit library
hints. Model and Lean waits are omitted from playback.
[MP4](../assets/autolean-gromov.mp4) · [Run evidence](../demos/gromov.json) ·
[Accepted source](../demos/gromov-run.lean)

## Create the research project

Enter the repository's pinned development shell and authenticate Codex as in
[the first-proof tutorial](first-proof.md). In a fresh directory:

    autolean init lean --example gromov --no-cslib
    (cd lean && lake update && lake exe cache get && lake build)
    autolean targets -d lean

The generated [Lean example](../../autolean/examples/gromov.lean) has three
research targets:

- `finite_quotient_injective`: a residually finite group admits one finite
  quotient injective on a given finite set.
- `residually_finite_of_finite_quotients`: finite-set separation implies
  residual finiteness.
- `residual_finiteness_question`: word hyperbolicity implies residual
  finiteness for an arbitrary group.

The definition of word hyperbolicity requires finitely many generators, a
connected Cayley graph, and a uniform four-point distance bound. The finite
generating set places no finiteness restriction on the group. The open target
has no residual-finiteness hypothesis. Compiled guards check its complete
type, finite-group examples, and the connectivity requirement.

## Watch a supporting proof develop

Start with the converse, whose proof uses the finite set `{g, 1}`:

    autolean solve --target residually_finite_of_finite_quotients \
      --model codex --max-cycles 2

The terminal prints the goal, proposed proof, Lean verdict, and any pattern
learned from an accepted proof. A rejection supplies diagnostics to the next
attempt. `gpt-6-astra` with `max` effort is the Codex default. For longer
requests, set `llm_timeout_seconds: 1200` in the generated program's LLM
configuration; the [program reference](../reference/program.md) defines
provider and Lean time budgets separately.

Open the same project in the TUI:

    autolean workbench

Use `/` or `Ctrl+F` to filter targets. Select `residual_finiteness_question`,
choose **Run agent**, and confirm the run. The **Research** tab keeps the current
goal, candidate, Lean feedback, and learning visible together. **Transcript**
retains the detailed activity. The elapsed timer continues while a provider
or Lean check is pending.

Learning means that accepted lemmas become available in the project and
accepted tactic patterns enter later prompts. The model's weights remain
fixed. A run can finish with open targets; the research view and durable
session report that boundary explicitly. **Stop** requests a stop after the
current attempt. Press it again to interrupt the candidate while allowing
accepted proof records to finish.

## Verify the example

The deterministic smoke runs the installed CLI against a scripted model and
the real pinned Lean environment. It checks a rejected candidate, a repaired
supporting proof, persisted learning, reuse in the open attempt, and refusal
of the open declaration's `sorryAx` dependency:

    AUTOLEAN_RUN_TUTORIAL_E2E=1 python -m pytest -q tests/test_gromov_e2e.py

This smoke runs in CI. The [activity record reference][records] defines the
observable events and their relationship to proof evidence.

[gromov]: https://www.ihes.fr/~gromov/wp-content/uploads/2018/08/657.pdf
[status]: https://doi.org/10.1017/S0305004126101959
[records]: ../reference/research-artifacts.md
[recording]: ../assets/autolean-gromov.gif
