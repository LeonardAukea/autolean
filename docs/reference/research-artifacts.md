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

`llm_input_tokens` and `llm_tokens` record the final candidate or definition
response. `attempt_input_tokens`, `attempt_output_tokens`, and
`attempt_model_calls` cover every planning, repair, proof, and definition call
within that attempt. Failed calls count toward the call total but contribute
no unreported token usage. Empty attempt fields mean accounting is unavailable
for that record. Session token totals sum input and output usage across all
returned responses.

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
used during repair or human revision. Each response records its exact text and
hash, reported model, token counts, duration, and any validation error. The
accepted response is identified by its hash and must parse to the stored plan.

## Paper coverage

Schema: `autolean.paper-coverage.v2`

Location: `PROJECT/AutoLean/Papers/*_coverage_*.json`

Coverage binds the acquired source, PDF, extracted text, Markdown artifact,
plan, response trace, reviewed paper profile, numbered-item inventory, Lean
evidence module, and proof environment. Model extraction and PDF extraction
each record content, page, and placement evidence. A reviewed profile passes
only when every expected item and mapping edge appears and the evidence module
elaborates. The profile supplies a reviewed mapping from each numbered paper
item to one or more existing Lean declarations. The workflow emits a closed
alias for each mapping and compiles the aliases together. This establishes
that the mapped declarations resolve in the pinned environment. It supplies
neither an equivalence proof between paper statements and Lean types nor a
separate axiom audit for each mapped declaration.

Each item has a workflow disposition and a recorded status:

| Disposition | Action |
| --- | --- |
| `prove` | Generate a Lean statement and attempt a proof. |
| `define` | Retain a definition or notation for review. |
| `context` | Retain explanatory material. |
| `open` | Retain an open question without attempting to close it. |

For a reviewed profile, mappings supply Lean source for all its numbered
items. Ordinary extraction formalizes only `prove` items.

| Status | Recorded evidence |
| --- | --- |
| `extracted` | The source yielded a proof obligation or definition. |
| `formalized` | Generated Lean source is present; proof may remain open. |
| `mapped` | A reviewed profile names existing Lean declarations. |
| `elaborated` | The complete reviewed mapping module compiled. |
| `context` | The item is retained as explanatory material. |
| `open` | The item is retained as an open question. |

Proof search records accepted proofs in experiment rows and Git commits. The
coverage ledger records the earlier paper preparation phase and is not updated
after each proof attempt.

## Project export

Schema: `autolean.project-export.v1`

Location: `EXPORT/manifest.json`

`prove` writes new statements under `AutoLean/Generated/` inside the Lean
project. `solve` updates the files containing its selected targets. Accepted
source receives a proof commit; an export packages selected project files for
sharing.

```bash
autolean export ARTIFACT --title "..."            # project source snapshot
autolean export ARTIFACT --session ID --title ... # one session's closure
```

Either form produces `project/` for the standalone Lake project, `paper/` for
the companion LaTeX source, and `manifest.json`. Build the project with
`cd project && lake build`. The whole-project form imports each generated
proof from the library root. The session form includes its target file and
that file's project-local imports; it can include other declarations in those
files. An export may contain unresolved `sorry` placeholders, which a
successful Lean build alone does not exclude.

The manifest hashes every included file and links the proof environment,
session, paper records, standalone Lake project, and companion LaTeX source.
Export validation rejects missing paper records, altered paper artifacts, and
records whose accepted response no longer yields the stored plan. The
`markdown_sha256` field binds the complete Markdown artifact, including its
source metadata. Paper records without this identity must be regenerated
before export.

## Release manifest

Schema: `autolean.release-manifest.v1`

Location: the GitHub release asset `release-manifest.json`

The release manifest binds the full Git object ID, commit timestamp, Hashver
identity, and SHA-256 of every release asset. It identifies a software release;
the project export identifies one research result.

The [proof-environment reference](environment.md) defines the Lean closure
shared by experiment, paper, export, and release records.
