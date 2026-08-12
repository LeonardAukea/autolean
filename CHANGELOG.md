# Changelog

This file records Python API compatibility and the product narrative. Exact
build chronology, commit-derived Hashver identities, assets, and generated
notes live in [GitHub Releases].

## 0.5.0

### Proof workflows

- Generate one bounded model strategy per proof target and preserve the exact
  accepted response identity with every experiment.
- Give each natural-language theorem a generated module that defines its
  formalization scope.
- Continue existing open-problem workspaces through durable sessions with a
  five-cycle default budget and explicit model switching.

### Paper evidence

- Acquire arXiv HTML with Lightpanda and read PDFs with layout-aware extraction
  and selective OCR.
- Audit reviewed paper profiles through closed Lean aliases in one sandboxed
  evidence module.
- Export model responses, coverage, source, proof environment, session, Lean
  project, and companion LaTeX source as one linked artifact.
- Keep the AGPL or commercially licensed PyMuPDF document stack in the explicit
  `pdf` installation extra.

### Release engineering

- Scope session exports to the selected target and its project-local import
  closure.
- Qualify Python 3.11 through 3.14, the Lean/Nix closure, containment attacks,
  dependency SBOM, and reproducible distributions in the aggregate CI gate.
- Bind each qualified `main` commit to an immutable Hashver release and
  artifact manifest. Public releases also carry GitHub's release attestation.

[GitHub Releases]: https://github.com/LeonardAukea/autolean/releases
