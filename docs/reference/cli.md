# Command-line interface

Root help presents one interactive entry point, six proof workflows, three
inspection commands, and four project commands:

```bash
autolean --help
```

Command help is the exact option reference for the installed version:

```bash
autolean COMMAND --help
```

## Interactive

`workbench`

: Open the interactive interface over the same commands and configuration.

## Proof workflows

`plan STATEMENT`

: Produce a reviewable mathematical plan. This calls the selected model and
  does not edit Lean source.

`prove STATEMENT`

: Plan, formalize, compiler-repair, and prove one natural-language statement.
  `--review-plan` pauses before source generation. The default limit is five
  proof attempts; zero is unlimited.

`solve`

: Work through existing `sorry` targets. `--dry-run` validates candidates
  without project writes. `--target` restricts work to one declaration query.

`resume [SESSION_ID]`

: Continue the latest active session or the named session. Model, backend,
  guidance, escalation, and cycle budget may change.

`verify SOURCE`

: Acquire a paper, extract claims, formalize them, and start a proof session.
  `--extract-only` stops after acquisition. `--formalize-only` writes reviewed
  Lean candidates and stops before proof search.

`problems`

: List, search, inspect, suggest, and work on curated open problems. The
  subcommands are `list`, `search`, `show`, `suggest`, and `work`.

## Inspection

`sessions`

: List durable proof sessions. `--active` selects resumable work and `--json`
  emits canonical records.

`targets`

: List `sorry` targets in priority order. Structured output is suitable for
  scripts.

`inspect TARGET_QUERY`

: Print the bounded Tree-sitter context for a target. `--goal-state` adds
  Lean's elaborated goal. `--format json` exposes the same source and context
  hashes used by the agent.

## Project commands

`doctor`

: Check model readiness, validate a model-generated smoke proof in the native
  sandbox, record the proof environment, and build the trusted project.

`models`

: List profiles, backends, capabilities, setup commands, and observed
  readiness.

`init PATH`

: Create a pinned Lean project and a `program.md` in the current directory.
  Mathlib and CSLib are enabled by default.

`export OUTPUT`

: Write a standalone Lean project, provenance manifest, and companion LaTeX
  paper. `--session` includes one durable session record.

## Operational records

These commands remain available for scripts and detailed audits. Root help
keeps the task-oriented surface small.

`environment`

: Identify the complete Lean proof environment by content. `--json` emits the
  record stored with accepted proofs and release artifacts.

`changes`

: Show the uncommitted Lean diff and recent proof commits for one project.

`results`

: Read recent experiment rows from `results.tsv`. `--tail` bounds the output
  and `--file` selects another record.

## Shared model options

Model-aware commands accept a profile or raw model string through `--model`.
`--backend` selects one transport:

- `claude_cli`
- `codex_cli`
- `anthropic`
- `openai`
- `ollama`
- `openai_compat`
- `muse_glimmer`

The CLI value wins over `program.md`. A profile supplies its backend unless
`--backend` is explicit.

Proof workflows also expose bounded model routing:

- `--escalation never|ask|auto`
- `--escalate-after N`
- `--escalate-to MODEL`

One invocation can switch at most once. A switch retains the original attempt
budget and is recorded with its failure evidence.

## Project selection

Commands that use `program.md` accept `--program PATH`. The Lean project path
inside that file is resolved relative to the program file's directory.

`inspect` and `targets` can take a Lean project directly with `--project`.

## Exit status

AutoLean exits non-zero for invalid configuration, missing credentials, quota
failure, transport errors, sandbox failure, Git failure, malformed provider
responses, and rejected command-level output.

A candidate proof rejected by Lean is an experiment outcome. The workflow
continues while its budget permits and exits according to the terminal session
state.

## Compatibility names

The canonical names above are used in help and documentation. These aliases
remain callable for existing scripts:

- `check` → `doctor`
- `diff` → `changes`
- `run` → `solve`
- `scan` → `targets`
- `ui` → `workbench`
