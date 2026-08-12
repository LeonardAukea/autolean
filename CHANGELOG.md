# Changelog

AutoLean release names use [Hashver] with the form
`YYYY.MM.DD+<12-character-commit>`. The Python package version states API
compatibility. The release manifest binds both identities to the full commit
and artifact hashes.

## Unreleased

### Proof workflows

- Generate one bounded model strategy per proof target and preserve the exact
  accepted response identity with every experiment.
- Isolate natural-language theorems in their own generated modules so an
  unrelated local source error cannot contaminate formalization.
- Continue existing open-problem workspaces through durable sessions with a
  five-cycle default budget and explicit model switching.

### Paper evidence

- Acquire arXiv HTML with Lightpanda and read PDFs with layout-aware extraction
  and selective OCR.
- Audit the reviewed 25-item Ionescu-Tulcea profile through 33 closed Lean
  aliases in one sandboxed evidence module.
- Export exact model responses, coverage, source, proof environment, session,
  Lean project, and companion LaTeX source as one linked artifact.

### Release engineering

- Scope session exports to the selected target and its project-local import
  closure.
- Qualify Python 3.11 through 3.14, the Lean/Nix closure, containment attacks,
  dependency SBOM, and reproducible distributions in the aggregate CI gate.

## 2026.08.11+04665ad96d5f

- Published the first content-addressed Python distributions, dependency SBOM,
  proof-environment record, and release manifest.
- Added the private repository maintenance, citation, security, contribution,
  and documentation structure.

[Hashver]: https://miniscruff.github.io/hashver/
