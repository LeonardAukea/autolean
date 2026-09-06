# Changelog

This file records Python API compatibility and the product narrative. Exact
build chronology, commit-derived Hashver identities, assets, and generated
notes live in [GitHub Releases].

## 0.5.0

### Models

- Prefer GPT-6 Astra at `max` through Codex, and preserve reasoning effort
  when resuming a session.
- Authenticate Grok through `grok login` and select it with `--provider grok`
  or the `grok` profile. A Grok selection uses Grok 4.6.

### Proof workflows

- Recheck current placeholders when resuming previously accepted targets.
- Bind environment identity to Lean module parts, IR, and native libraries.
- Preserve source bytes and permissions, and check for editor changes after
  staging an accepted replacement.
- End optional tactic search when a compiler trial exhausts its time budget.
- Extract a Lean plan, formalization, or tactic from a completion that
  prefixes the artifact with prose.
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
- Wrap Lean source listings in the companion LaTeX document.

### Terminal experience

- Render every command through one console theme with unified status glyphs.
- Keep session status readable in narrow terminals and suggest continuation
  only for unfinished sessions.
- Show liveness for every long wait: a spinner on a terminal, one narration
  line when piped, so scripted and CI runs never go silent.
- Accept `--provider` and `--model` written before the subcommand, and
  `autolean model` as an alias of `autolean models`.
- Point `doctor` at another ready subscription when the selected model hits
  a usage limit.
- Nest a Git repository inside `autolean init` when an enclosing project
  would ignore generated proofs.

### Release engineering

- Profile scan, export, structural context, and environment capture with
  repeatable CPU and allocation receipts.
- Bound subprocess output and own child-process cleanup through one runner.
- Exercise installed packages and isolated tutorial exports in smoke tests.
- Retain demo failure artifacts and synchronize recorded commands with the
  terminal prompt.
- Scope session exports to the selected target and its project-local import
  closure.
- Qualify Python 3.11 through 3.14, the Lean/Nix closure, containment attacks,
  dependency SBOM, and reproducible distributions in the aggregate CI gate.
- Replay the first-proof tutorial end to end in CI against a scripted model.
- Bind each qualified `main` commit to an immutable Hashver release and
  artifact manifest. Public releases also carry GitHub's release attestation
  and per-asset build provenance, verified before PyPI publication.

[GitHub Releases]: https://github.com/LeonardAukea/autolean/releases
