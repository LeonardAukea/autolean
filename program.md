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
Run `autolean models` to see every profile and its local setup state.
Subscription profiles include fable, opus, sonnet, codex, codex-terra, and
codex-luna. Hosted profiles append `-api`. Local profiles include muse-glimmer,
gemma4, and deepseek-prover. A raw model string also works, for example
`gemma4:26b`.

backend: overrides the profile's backend with one of
claude_cli, codex_cli, anthropic, openai, ollama, openai_compat, muse_glimmer.
endpoint: optional HTTP base URL for a self-hosted inference server.
effort: optional reasoning-depth override for capable models.
temperature: sampling value for backends that advertise support.
escalation_policy: never, ask, or auto; ask is silent in non-interactive runs.
escalation_model: optional exact stronger profile or raw model ID.
escalation_after_failures: eligible kernel failures before a routing decision.
-->
model: opus
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
