# AutoLean Program

> This file is your interface to the AutoLean agent. Edit it to steer what the agent
> works on overnight. The agent reads this file at startup and follows the goals below.

## Mode

<!-- Pick ONE: sorry-elimination | autoformalize | proof-golf -->
sorry-elimination

## Lean Project Path

<!-- Absolute path to the Lean 4 project root (must contain lakefile.lean) -->
workspace

## Goals

<!-- Describe what you want the agent to accomplish. Be specific. -->

1. Find all `sorry` placeholders in .lean files under the project.
2. For each sorry, attempt to fill in a valid proof.
3. Prioritize sorries in files with fewer remaining sorries (low-hanging fruit first).
4. If a sorry cannot be resolved after 5 attempts, skip it and move to the next.

## Constraints

- Do NOT modify any import statements.
- Do NOT add new dependencies to lakefile.lean.
- Do NOT delete existing theorems or definitions.
- Do NOT change theorem statements — only fill in proofs.
- Keep proofs readable: prefer named tactics over term-mode when possible.
- Maximum proof length: 30 lines per sorry.

## Strategy Hints

<!-- Optional hints the agent can use when stuck -->

- Try `simp`, `omega`, `ring`, `decide` first for simple goals.
- For goals involving natural numbers, try `omega` or `Nat.recAux`.
- For algebraic goals, try `ring` or `field_simp` then `ring`.
- For logical goals, try `tauto` or `aesop`.
- If the goal has hypotheses, try `exact h`, `assumption`, or `contradiction`.
- For inductive types, try `cases` or `induction` on the relevant variable.
- When automation fails, decompose: `constructor`, `intro`, `apply`, then recurse.

## LLM Configuration

<!-- Which Ollama model to use -->
model: gemma4:26b
temperature: 0.4
max_retries_per_sorry: 5
cycle_timeout_seconds: 120

## Experiment Budget

<!-- Stop after this many total cycles (0 = unlimited, run until interrupted) -->
max_cycles: 0
