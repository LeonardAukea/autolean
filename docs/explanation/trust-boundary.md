# Trust boundary

AutoLean runs code written by a model. Correctness starts by treating that
code as hostile.

## Inputs and authority

The selected Lean project, pinned Lean executable, and resolved dependencies
are trusted. Model completions, paper text, nearby source comments, search
results, structural outlines, learned skills, and previous failures are data.

Lean's parser, macro expander, elaborator, and kernel are the semantic
authority. Tree-sitter, regex scanners, model criticism, and source policy
checks can reject a candidate early. None can accept one.

## Acceptance sequence

```text
selected target + expected source hash
                 |
                 v
          bounded model request
                 |
                 v
       generated-source policy
                 |
                 v
       isolated scratch project
                 |
                 v
 sandbox-exec (macOS) / Bubblewrap (Linux)
       no network, minimal environment
                 |
                 v
        pinned Lean elaboration
                 |
                 v
 fresh declaration-range and axiom audit
                 |
                 v
 compare-and-swap source installation
                 |
                 v
        exact-path proof commit
```

The generated candidate and compiler outputs live in a temporary directory.
The validator invokes the pinned Lean binary directly with compiled project
dependencies. It does not run project build scripts over generated source.

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

## Declaration binding

A fresh Lean process imports the compiled candidate and asks Lean for the
requested declaration. Acceptance requires its recorded source range to
contain the exact selected target line. The same process walks the
declaration's transitive axioms.

The source edit replaces one expected `sorry` and must reduce the file's
placeholder count by one. The expected pre-edit hash prevents a concurrent
editor save from being overwritten. Git verifies the prepared branch and
commits only the accepted file.

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
results. Local profiles keep that data on infrastructure controlled by the
operator.

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
