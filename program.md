# AutoLean Program

> This file configures one AutoLean run. Typed settings and generated-code
> policy are enforced. Goals, constraints, and strategy hints are advisory
> context included in every model request.

## Mode

<!-- The autonomous agent supports sorry-elimination. -->
sorry-elimination

## Lean Project Path

<!-- Absolute path to the Lean 4 project root (must contain lakefile.lean) -->
workspace

## Goals

<!-- Describe what you want the agent to accomplish. Be specific. -->

1. Produce complete proofs for the selected `sorry` targets.
2. Prefer proofs that expose the mathematical argument clearly.

## Constraints

- Preserve imports, dependencies, declarations, and theorem statements exactly.
- Return only the proof body for the selected placeholder.
- Keep proofs readable and prefer named tactics where they clarify intent.

## Strategy Hints

<!-- Optional hints the agent can use when stuck -->

- Try `simp`, `omega`, `ring`, `decide` first for simple goals.
- For goals involving natural numbers, try `omega` or `Nat.recAux`.
- For algebraic goals, try `ring` or `field_simp` then `ring`.
- For logical goals, try `tauto` or `aesop`.
- When a hypothesis exactly matches the goal, try `exact h` or `assumption`.
- For inductive types, try `cases` or `induction` on the relevant variable.
- When automation fails, decompose with `constructor`, `intro`, and `apply`.

## LLM Configuration

<!--
`auto` selects the strongest profile for an authenticated provider. Run
`autolean models` for profiles and setup state; docs/reference/program.md
defines every key.
-->
model: auto
temperature: 0.0
max_retries_per_sorry: 5
escalation_policy: ask
escalation_after_failures: 2
cycle_timeout_seconds: 120
llm_timeout_seconds: 600
max_proof_lines: 30

## Experiment Budget

<!-- Each invocation gets this many cycles. Use --overnight explicitly. -->
max_cycles: 5
