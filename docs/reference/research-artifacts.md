# Research artifact records

AutoLean records the boundary inputs and accepted outputs of each research
workflow. JSON records carry a `schema` field. Readers reject unsupported
schemas and mismatched content hashes.

## Proof session

Schema: `autolean.proof-session.v1`

Location: `PROJECT/.autolean/sessions/SESSION_ID.json`

A session owns its command, target scope, model routing, cycle budget, status,
result path, and timestamps. Atomic replacement makes the record resumable
after a process stops. `autolean sessions --json` is the command interface.

## Experiment rows

Location: `PROJECT/results.tsv`

Each row identifies a target attempt, candidate, outcome, Lean diagnostic,
model, prompt context, proof environment, and strategy-response hash. Rows are
append-only evidence for `autolean results`, resume logic, and training export.

## Activity journal

Schema: `autolean.progress.v1`

Location: `PROJECT/logs/*.events.jsonl`

Each JSON Lines record names a phase, target, goal, candidate, feedback,
learning observation, summary, or completion. It includes a UTC timestamp,
target, cycle, and attempt. Detail is bounded to 8,192 characters; terminal
panels show a shorter excerpt. The TUI reads the same events from child
output, framed by `AUTOLEAN_EVENT ` when `AUTOLEAN_PROGRESS=json` is set.
Deterministic search reports each tactic and verdict. Cycle zero denotes
activity before an experiment record exists. The transcript retains at most
1,000 lines and coalesces pending updates within the same bound.

Events describe activity. Experiment rows and proof commits establish
acceptance. Journal or display failures leave that acceptance path intact.
Open targets remain open when an attempt budget is exhausted.

## Paper plan

Schema: `autolean.paper-plan.v2`

Location: `PROJECT/AutoLean/Papers/*_plan_*.json`

The record contains the normalized accepted plan and every provider response
used during repair or human revision. Each response records its exact text
hash, reported model, token counts, duration, validation result, and accepted
state. The accepted response must parse to the stored plan.

## Paper coverage

Schema: `autolean.paper-coverage.v2`

Location: `PROJECT/AutoLean/Papers/*_coverage_*.json`

Coverage binds the acquired source, PDF, extracted text, plan, response trace,
reviewed paper profile, numbered-item inventory, Lean evidence module, and
proof environment. Model extraction and PDF extraction each record content,
page, and placement evidence. A reviewed profile passes only when every
expected item and mapping edge appears and the complete evidence module
elaborates.

## Project export

Schema: `autolean.project-export.v1`

Location: `EXPORT/manifest.json`

Accepted proofs are written to `AutoLean/Generated/` inside the Lean project
and committed there. That directory is the working copy; an export is the
portable one.

```bash
autolean export ARTIFACT --title "..."            # every accepted proof
autolean export ARTIFACT --session ID --title ... # one session's closure
```

Either form produces a directory holding `project/` — a standalone Lake
project that builds on its own with `cd project && lake build` — `paper/` for
the companion LaTeX source, and `manifest.json`. The whole-project form
imports each generated proof from the library root, so building the export
checks the results. The session form narrows to one target and its
project-local import closure.

The manifest hashes every included file and links the proof environment,
session, paper records, standalone Lake project, and companion LaTeX source.
Export verification rejects missing records, altered files, and paper records
whose accepted response no longer yields the stored plan.

## Release manifest

Schema: `autolean.release-manifest.v1`

Location: the GitHub release asset `release-manifest.json`

The release manifest binds the full Git object ID, commit timestamp, Hashver
identity, and SHA-256 of every release asset. It identifies a software release;
the project export identifies one research result.

The [proof-environment reference](environment.md) defines the Lean closure
shared by experiment, paper, export, and release records.
