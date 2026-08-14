# Proof environments and provenance

Proof acceptance is bound to source bytes and a complete Lean environment.

## Pinned closure

The repository pins:

- Lean 4.33.0
- Mathlib 4.33.0
- CSLib 4.33.0
- every transitive Lake dependency by Git commit
- Tree-sitter runtime and Lean grammar artifacts by package version and hash

The Nix flake fetches the official Lean release archives by SHA-256 on Apple
Silicon, AArch64 Linux, and x86-64 Linux. `lean-toolchain` and
`lake-manifest.json` describe the same workspace closure.

## Environment identity

```bash
autolean environment --project workspace --json
```

The environment SHA-256 covers:

- the selected Lean executable and reported version
- `lean-toolchain`, `lakefile.lean`, and `lake-manifest.json`
- resolved dependency names and Git revisions
- importable project and dependency `.olean` files
- native libraries used by the Lean environment

AutoLean hashes the environment before and after final elaboration. A changed
dependency or compiled artifact makes acceptance fail closed.

## Accepted proof record

A successful experiment records:

- proof text SHA-256
- source file SHA-256 before and after installation
- proof environment SHA-256
- declaration name and exact source range
- transitive axiom report
- model, backend, and model artifact identity when available
- prompt and structural-context SHA-256
- input and output token measurements when available
- terminal outcome and duration

The accepted source uses compare-and-swap semantics. An editor save between
validation and installation stops the operation.

## Axiom policy

AutoLean permits Lean's foundational axioms:

- `propext`
- `Quot.sound`
- `Classical.choice`

It rejects `sorryAx`, compiler-trust axioms, project axioms, and unknown
transitive axioms. Anonymous `example` targets are not accepted because Lean
cannot bind them to a durable declaration name.

## Build the closure

```bash
nix develop
cd workspace
lake exe cache get
lake build Cslib
lake build
```

The Nix checks validate the Lean version, grammar load, generated-code command
policy, and native package. Linux CI also runs the Bubblewrap containment
attacks under the host AppArmor profile.

CI builds the hash-pinned Lean grammar once and supplies that shared library
to every supported Python interpreter. A non-Nix installation can materialize
the same versioned grammar through `tree-sitter-language-pack`. If that cache
is unavailable, structural inspection reports `unavailable` and proof search
continues with Lean as its semantic authority.

## Reproducibility boundary

Environment capture and candidate acceptance are deterministic for fixed
bytes. Hosted aliases may point to changing model weights, and providers may
return different completions for the same request. Generation is proposal
search; the recorded proof and environment are the reproducible result.

GitHub releases use a commit-derived Hashver identity and include the Python
artifacts, dependency SBOM, proof environment, and an asset manifest. See
[Qualify and publish a release](../how-to/release.md).
