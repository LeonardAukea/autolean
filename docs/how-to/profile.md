# Profile the runtime

Run the repeatable local workloads inside the pinned development environment:

```bash
uv run python scripts/profile_runtime.py --workload scan --output /tmp/profile
uv run python scripts/profile_runtime.py --workload export --output /tmp/profile
uv run python scripts/profile_runtime.py --workload structure --output /tmp/profile
uv run python scripts/profile_runtime.py --workload environment --output /tmp/profile
```

Each command records the interpreter, platform, cold invocation, repeated warm
timings, output identity, Python allocations, and process peak resident memory.
Every repetition must return the same output identity. CPU and allocation
tracing run separately from timing measurements. The environment workload hashes
the pinned Lean closure on its cold invocation and checks its fingerprint on
warm invocations. Structural context uses its normal bounded parser cache.

The artifacts include a machine-readable JSON receipt, a `cProfile` data file,
and text reports for cumulative CPU time and retained allocations. Python's
[profiling](https://docs.python.org/3/library/profile.html) and
[tracemalloc](https://docs.python.org/3/library/tracemalloc.html) references
describe the measurements. Peak resident memory covers the complete Python
process lifetime; native Lean and model subprocesses have separate lifetimes.

For a change, run the same workload against the same project before and after
the edit, preserving both output directories. Compare output identities before
comparing latency or memory. Keep the machine otherwise idle and use several
repetitions. CI checks behaviour and bounds; machine-dependent latency is an
observed measurement.

Measure native Lean independently with the host's `time` utility, using one
exported proof and its pinned dependency cache. Provider latency and token
accounting belong to live session records. A local Python speedup establishes
only the workload named in its receipt.
