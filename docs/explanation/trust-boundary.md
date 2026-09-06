# Trust boundary

AutoLean runs code written by a model. The product of a run is an auditable
derivation: exact source bytes, a pinned Lean and Mathlib closure, and a
recorded identity for every accepted proof — the
[environment reference](../reference/environment.md) states that record. This
page defines the boundary that keeps the record trustworthy while generated
code executes: what is trusted, what is data, and which layer owns each
rejection.

## Inputs and authority

The selected Lean project, pinned Lean executable, and resolved dependencies
are trusted. Model completions, paper text, nearby source comments, search
results, structural outlines, learned skills, and previous failures are data.

Lean's parser, macro expander, elaborator, and kernel are the semantic
authority. Tree-sitter, regex scanners, model criticism, and source policy
checks can reject a candidate early. None can accept one.

## Acceptance sequence

```mermaid
flowchart TD
    target["selected target + expected source hash"]
    request["bounded model request"]
    policy["generated-source policy"]
    scratch["isolated scratch project"]
    sandbox["sandbox-exec (macOS) / Bubblewrap (Linux)<br/>no network, minimal environment"]
    elab["pinned Lean elaboration"]
    audit["fresh declaration-range and axiom audit"]
    install["compare source and install atomically"]
    commit["exact-path proof commit"]

    target --> request --> policy --> scratch --> sandbox
    sandbox --> elab --> audit --> install --> commit
    policy -- "reject" --> stop(["candidate discarded,<br/>diagnostics recorded"])
    elab -- "reject" --> stop
    audit -- "reject" --> stop
```

The generated candidate and compiler outputs live in a temporary directory.
The validator invokes the pinned Lean binary directly with compiled project
dependencies. It does not run project build scripts over generated source.

## Compiler-guided generation

AutoLean gives the model a bounded proposal loop over the pinned Lean runner.
The host process extracts the exact goal, supplies local source structure and
search evidence, applies the generated-source policy, and runs each candidate
inside the operating-system sandbox. Lean diagnostics become evidence for the
next repair or proof attempt.

The host process owns the runner and filesystem authority. The model receives
the mathematical context and compiler results needed to improve Lean source;
accepted bytes still pass fresh elaboration, declaration binding, and the
axiom audit.

## Source policy

The policy rejects source that carries authority beyond a proof body:

- `sorry`, `admit`, new axioms, and unsafe declarations
- imports and environment-changing commands
- explicit process, file, network, and dynamic-library operations
- elaborator hooks and command execution surfaces
- hidden bidirectional controls and malformed Unicode
- proof bodies above the configured line bound

This is defense in depth. Lean elaboration can execute metaprograms, so the
operating-system sandbox owns process, filesystem, environment, and network
containment.

## Why an operating-system sandbox

Elaborating Lean source is executing a program. Macros, `elab` rules, and
tactic frameworks run arbitrary compiled code inside the elaborator, so
checking an untrusted candidate is code execution, whatever the candidate
looks like. The layers above the sandbox cannot carry this responsibility:

- The source policy is a syntactic filter. It rejects the obvious escape
  hatches, and a determined payload can be disguised from any scanner that
  does not run the code.
- The kernel guarantees logical soundness. It says nothing about what the
  elaborator did to the host while producing the term it checks.

The sandbox is the one layer whose guarantee does not depend on the
candidate's content: no network, a minimal environment, and a filesystem view
enforced by the operating system. The sandbox permits reads of the pinned
toolchain and compiled dependencies and confines writes to scratch outputs.

Native compiler and sandbox costs require host-specific measurements. The
[profiling guide](../how-to/profile.md) separates those subprocesses from
Python runtime measurements.

## Declaration binding

A fresh Lean process imports the compiled candidate and asks Lean for the
requested declaration. Acceptance requires its recorded source range to
contain the exact selected target line. The same process walks the
declaration's transitive axioms.

The source edit replaces one expected `sorry` and must reduce the file's
placeholder count by one. Git verifies the prepared branch and commits only
the accepted file. The [source installation contract](../reference/environment.md#accepted-proof-record)
defines the comparison and writer requirements.

## Structural context

Tree-sitter gives the model imports, namespace, declaration span, syntax path,
earlier referenced declarations, and neighbours. The context includes parser
and grammar versions, source SHA-256, recovery quality, and parse-error spans.
It is cached by source hash and bounded to 6,000 characters.

Lean projects can add syntax and macros at runtime. A recovered or unavailable
outline remains visible as advisory context; it never replaces the Lean goal
state.

## Provider boundary

Hosted and subscription models receive the selected declaration, nearby
source, goal state, prior failures, guidance, skills, and bounded search
results. AutoLean labels effective inference as local or remote from the
provider and any explicit endpoint.

Advisory research has an independent placement. `search_scope: local` uses
the project CodeDB index and sends no goal query to Loogle, LeanSearch, or
arXiv. `search_scope: remote` permits those services, and `auto` follows model
placement. Each proof attempt records whether remote research ran and the
content hashes of remote and indexed evidence separately.

Document-capable hosted providers receive the exact native PDF bytes admitted
by `--pages`. The paper coverage ledger records the provider, inference
placement, request and response identities, document identity and size, and
the one-indexed pages transferred. Other providers receive bounded Markdown.

PaddleOCR-VL receives a page-bounded PDF at the endpoint named by the user.
The coverage ledger records whether that endpoint is local or remote, its URL,
the exact request identity and size, the page set, and the Markdown result
identity. Hybrid PDF extraction runs locally and records the same content
identities without an endpoint.

Provider credentials remain in the process environment. They do not enter
`program.md`, prompts, session records, or exported artifacts. OpenAI hosted
requests disable Responses API storage where the API exposes that control.
Other provider retention follows the selected service's terms.

`--dry-run` sends the same bounded request and runs the same source and Lean
checks. It does not install source or write logs, results, skills, or training
data.

## What a proof means

A passing result establishes the exact Lean declaration under the recorded
axioms and environment. It does not establish that a natural-language claim,
paper, or conjecture was formalized faithfully. That source-to-statement step
is a separate mathematical review.

`autolean doctor` builds the existing project after its generated smoke proof.
Run it only on a project whose source and dependencies you trust.
