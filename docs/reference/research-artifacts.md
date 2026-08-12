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
proof environment. A reviewed profile passes only when every expected item and
mapping edge appears and the complete evidence module elaborates.

## Project export

Schema: `autolean.project-export.v1`

Location: `EXPORT/manifest.json`

An export selects one target or session and its project-local Lean import
closure. The manifest hashes every included file and links the proof
environment, session, paper records, standalone Lake project, and companion
LaTeX source. Export verification rejects missing records, altered files, and
paper records whose accepted response no longer yields the stored plan.

## Release manifest

Schema: `autolean.release-manifest.v1`

Location: the GitHub release asset `release-manifest.json`

The release manifest binds the full Git object ID, commit timestamp, Hashver
identity, and SHA-256 of every release asset. It identifies a software release;
the project export identifies one research result.

The [proof-environment reference](environment.md) defines the Lean closure
shared by experiment, paper, export, and release records.
