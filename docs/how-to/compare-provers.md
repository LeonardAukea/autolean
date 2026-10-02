# Compare proof workflows

Use the comparison harness to measure accepted proofs from AutoLean and a
plain-prompt baseline on the same cases, model, and Lean environment. Run it
from a checkout with the
[development dependencies](../reference/dependencies.md) and a working
[model configuration](choose-a-model.md).

Start with the [small Std suite](../../benchmarks/lean-std-smoke.json). It
contains elementary goals and a `False` control to check the harness and proof
acceptance. Check that neither workflow accepts a proof of the false control.

To study proof success, choose cases and budgets before running the
comparison. Select a collection that represents the work you want to do,
including cases where either workflow may fail. Reserve separate cases for
evaluating changes to prompts or search. Use the same JSON format as the Std
suite: an `autolean-comparison-suite-v1` object containing `cases`. Each case
needs a unique `id`, a fully qualified `declaration` name, and Lean `source`
with exactly one `sorry`.

Point `program.md` at a prepared Lean project whose imports support the suite.
The harness copies that project for each workflow and repetition, checks that
the case elaborates, and starts with empty research state. It reserves the file
name `AutoLeanComparison.lean` for the case.

```bash
uv run python -m scripts.compare_provers \
  --program program.md \
  --suite benchmarks/lean-std-smoke.json \
  --output /tmp/comparison-results \
  --calls 4 --attempts 4 --repeats 3
```

The output directory must be new and outside the template project. A run may
incur provider charges. Both workflows share these upper bounds:

- `--calls`: backend `generate` calls per case, workflow, and repetition,
  including planning and repair calls.
- `--attempts`: proof attempts per case, workflow, and repetition.

SDK transport retries can issue several requests within one backend call.
Those retries share the call's record; reported token usage covers returned
responses.

The configured output-token ceiling applies to providers that support it. Read
`output_ceiling_per_call` in each result to see whether the ceiling applied.
The harness fixes the configured temperature, disables model fallback and
escalation, and restricts research to the local project. `--order-seed` shuffles
which workflow runs first; successive repetitions alternate that order. The
seed controls run order only. Model sampling can vary between runs.

For an Ollama model, pass `--model-revision DIGEST` to require a specific digest
before and after each workflow. With other providers, a returned model name
identifies the reported model; reproducibility also depends on the provider's
versioning guarantees.

## Read the results

The baseline, stored as `vanilla`, asks the model to replace the single `sorry`
using a fixed source-and-goal prompt. Each retry receives that same prompt.
AutoLean uses its tactic search, proof plan, local research, and Lean feedback.
Both submit candidates through the same
[proof acceptance checks](../explanation/trust-boundary.md).

`comparison.json` contains each pair and counts five outcomes: AutoLean alone
accepted a proof, the baseline alone accepted a proof, both did, neither did,
or the pair was invalid. Mismatched source, environment, or reported models,
provider errors, and incomplete runs make a pair invalid. The command exits
with status 1 if any pair is invalid; ordinary proof failures remain valid
observations. A strategy response that fails schema validation counts as an
AutoLean failure; a provider exception invalidates the pair.

For each case and workflow, inspect `result.json`, the retained Lean workspace,
and `calls.jsonl`. The call log records exact prompts and responses, reported
model names and token usage, and elapsed time. The top-level `suite.json` and
`implementation.json` identify the cases and implementation used in the run.

Compare acceptance together with model calls, reported tokens, recorded
checks, and elapsed time for each workflow. The records summarize attempts;
compiler invocation counts are outside these measurements. Equal request and
attempt limits allow different amounts of input text and compiler work. State
the case selection, budgets, number of repetitions, invalid pairs, and observed
difference when reporting a result. Report false controls separately when
calculating proof success rates.
The summary counts describe the recorded suite; broader claims need a
representative sample and an analysis of uncertainty.

See the [recorded Std comparison](../reference/comparison-results.md) for one
run, including its accepted proofs, resource use, and limits.
