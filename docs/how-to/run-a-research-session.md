# Run a research session

Use a session when a theorem needs several attempts, new guidance, or a model
change. A session is the durable unit of work; one command invocation is only
a bounded continuation.

## Start with a plan

```bash
autolean plan "state the theorem here" --guide "Preserve the source notation."
```

Read the examples, obstructions, premises, reductions, completion criterion,
and checkpoints. A plan should make failure informative. Add a premise or
change a reduction before increasing the attempt budget.

To formalize and prove one new statement:

```bash
autolean prove "state the theorem here" --review-plan
```

To work through declarations already containing `sorry`:

```bash
autolean solve --max-cycles 5
```

Use `--dry-run` to exercise model and Lean validation while keeping source,
logs, results, skills, and training data unchanged.

## Continue saved work

```bash
autolean sessions --active
autolean resume SESSION_ID --max-cycles 5
```

The continuation keeps prior failures and provenance. You may change the model,
backend, guidance, escalation policy, or cycle budget. Accepted source is never
replayed as an unverified suggestion.

## Work on an open problem

Search the curated catalog before inventing a statement:

```bash
autolean problems search "metric geometry"
autolean problems suggest
autolean problems show filling-area
```

`problems show` states the semantic boundary. A `formalized` entry can enter
proof search. A `scaffold` entry needs a source-faithful Lean statement first.

```bash
autolean problems work collatz
```

Running the same command continues its workspace. Existing generated source is
inspected and reused; AutoLean does not overwrite it blindly.

## Guide the loop interactively

```bash
autolean workbench
```

The workbench composes the same CLI operations. Select a target and model,
edit mathematical guidance, validate without writing, then accept the proof
only after inspecting its evidence. `Escape` stops an active worker.

## Choose a budget

Five cycles is the normal default. A small explicit budget is easier to audit:

```bash
autolean solve --max-cycles 3
```

Unlimited work is deliberate:

```bash
autolean solve --overnight
```

An overnight run increases retry limits, resets epochs, and resumes until it
is stopped. Use it only after a bounded run has shown that failures are useful
proof evidence rather than authentication, environment, or formalization
errors.

Export a finished session with `autolean export`. The export is the shareable
artifact; runtime logs and learned context remain local project state.
