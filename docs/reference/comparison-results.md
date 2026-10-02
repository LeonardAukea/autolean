# Recorded proof comparison

On 2 October 2026, AutoLean accepted proofs for five of six elementary Std
cases; independent plain prompts to the same model accepted two. Two
repetitions gave the same outcomes. Both workflows rejected the `False`
control and left the induction case unresolved.

| Case | AutoLean, accepted repetitions | Plain prompts, accepted repetitions |
| --- | ---: | ---: |
| Natural-number addition by zero | 2/2 | 0/2 |
| A natural number is below its successor | 2/2 | 2/2 |
| Appending the empty list | 2/2 | 0/2 |
| Reversing a list twice | 2/2 | 0/2 |
| Conjunction elimination | 2/2 | 2/2 |
| Recursive tally, by induction | 0/2 | 0/2 |
| `False` control | 0/2 | 0/2 |

AutoLean's successful proofs came from its deterministic tactic search and
made no model calls. The result supports that search on these cases. It gives
no evidence that AutoLean's model-driven planning improves difficult proofs.

The six positive cases, across both repetitions, used these resources:

| Measurement | AutoLean | Plain prompts |
| --- | ---: | ---: |
| Accepted proofs | 10/12 | 4/12 |
| Backend generation calls | 8 | 36 |
| Reported input tokens | 8,116 | 4,296 |
| Reported output tokens | 968 | 310 |
| Sum of arm wall times | 170.973 s | 122.522 s |

All AutoLean model calls on the positive cases went to the unresolved tally
case. Fewer calls therefore coexisted with greater token use and elapsed time.
These wall times include each arm's setup and validation. Warm caches and host
load affect them; the repeated cases also share a model and are not independent
samples of mathematical difficulty.

## Conditions and evidence

The [suite](../../benchmarks/lean-std-smoke.json) and budgets were fixed before
the run: four generation calls, four proof attempts, temperature zero, and a
2,048-token output ceiling per response. Run order used seed zero and reversed
between repetitions. Model sampling had no explicit seed. The model was
`qwen2.5:7b` through Ollama, with digest
`845dbda0ea48ed749caafd9e6037047aa19acfcfd82e704d7ca97d631a0b697e`.

Both workflows used Python 3.12.14, Lean 4.34.1, the same starting sources, and
the same declaration acceptance policy. Each ran in a fresh project with empty
research state. The baseline received the source and goal again on each retry;
AutoLean could use tactics, planning, local context, and compiler feedback.
There were no invalid pairs.

The [complete result](../../benchmarks/results/2026-10-02-std.json) retains
prompts, responses, diagnostics, usage, runtime versions, and source identities.
The [implementation receipt](../../benchmarks/results/2026-10-02-implementation.json)
identifies the source snapshot. The
[comparison guide](../how-to/compare-provers.md) describes the protocol and
how to repeat it.

This suite checks the comparison machinery and elementary proof behavior. A
claim about research mathematics needs a representative collection reserved
for evaluation. The recorded advantage and costs apply to this suite and
model.
