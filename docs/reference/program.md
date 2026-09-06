# `program.md` configuration

`program.md` contains validated settings and advisory mathematical context.
It is read once at command start.

## Sections

`## Mode`

: Required agent mode. The supported value is `sorry-elimination`.

`## Lean Project Path`

: Lean project path, absolute or relative to `program.md`. The default is
  `workspace`.

`## Goals`

: Numbered or bulleted advisory goals included in model requests.

`## Constraints`

: Numbered or bulleted mathematical and project constraints.

`## Strategy Hints`

: Numbered or bulleted proof methods or reductions.

`## LLM Configuration`

: Provider-neutral model and generation settings, written as `key: value`.

`## Experiment Budget`

: The cycle budget for one invocation.

## LLM settings

`model`

: Profile alias or raw model name. `auto` is the default and resolves a
  machine provider as described in
  [Choose and switch models](../how-to/choose-a-model.md).

`provider`

: Optional provider override. Use a short name such as `codex` or `anthropic`;
  `autolean models` lists every name. Canonical backend IDs are also accepted.

`endpoint`

: HTTP endpoint for a self-hosted provider. Only `http` and `https` endpoints
  with a host are accepted.

`effort`

: Reasoning effort: `none`, `minimal`, `low`, `medium`, `high`, `xhigh`, or
  `max`. The backend must advertise the selected control.

`temperature`

: Optional finite value from 0 through 2. Raw models default to `0.4` when
  their provider supports sampling. Named profiles own this setting, and
  reasoning providers reject it when they expose no temperature control.

`max_output_tokens`

: Positive output limit. `num_predict` is the accepted local-provider alias.
  Providers without an enforceable output control reject this setting.

`llm_timeout_seconds`

: Positive finite provider timeout. `timeout` is an accepted alias.

`search_scope`

: Placement for advisory Loogle, LeanSearch, and arXiv queries: `local`,
  `remote`, or `auto`. `local` uses the project CodeDB index only. `remote`
  sends the theorem name and goal to the configured research services. `auto`
  enables those services when model inference is remote. The default is
  `auto`.

`max_retries_per_sorry`

: Positive attempt count for one target. The default is 5.

`cycle_timeout_seconds`

: Positive Lean experiment timeout. The default is 120.

`max_proof_lines`

: Positive source-policy limit for a generated proof. The default is 30.

`escalation_policy`

: `never`, `ask`, or `auto`. The default is `ask`.

`escalation_model`

: Optional stronger profile or raw model name.

`escalation_after_failures`

: Positive number of eligible failures before a switch is offered. The
  default is 2.

`max_cycles`

: Non-negative experiment cycle count. The default is 5. Zero is unlimited.

## Example

```markdown
# AutoLean Program

## Mode

sorry-elimination

## Lean Project Path

workspace

## Goals

1. Close the selected theorem with a kernel-checked proof.

## Constraints

- Preserve the declaration and its mathematical meaning.

## Strategy Hints

- Search the local project and Mathlib before generating a long proof.

## LLM Configuration

model: auto
search_scope: auto
temperature: 0.4
max_output_tokens: 32768
max_retries_per_sorry: 5
escalation_policy: ask
escalation_after_failures: 2
cycle_timeout_seconds: 120
llm_timeout_seconds: 600
max_proof_lines: 30

## Experiment Budget

max_cycles: 5
```

## Precedence and capability checks

`--model` and `--provider` override the file. Workflow-specific cycle and
escalation options override the corresponding settings for that invocation.

A profile supplies its provider binding, endpoint, effort, token limit, seed,
revision, and artifact identity. A profile and an explicit provider must
describe the same binding. Use a raw provider model ID when no shipped profile
represents the required combination. Configuration is validated before
provider or project work begins.

Some subscription CLIs do not expose temperature, stop sequences, or a hard
output ceiling. AutoLean rejects an explicit setting its provider cannot
enforce.
