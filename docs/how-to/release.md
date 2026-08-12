# Qualify a release

This guide is for maintainers. A release is the output of a qualified `main`
commit. Tags and assets are produced by CI.

## 1. Prepare one reviewable change

Work on a branch. Keep runtime proof data, training exports, learned skills,
logs, and local indexes out of the commit. Update the Python compatibility
version in `pyproject.toml` only when the public API contract changes.

Run the focused tests after each subsystem change. Then run the complete local
gate from the repository root:

```bash
nix develop
uv lock --check
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy autolean
uv run --frozen pytest -q
nix flake check path:. --no-build --all-systems
nix flake check path:.
nix build path:.#default
```

The host containment tests need permission to create the native sandbox:

```bash
AUTOLEAN_RUN_SANDBOX_E2E=1 \
  uv run --frozen pytest -q -p no:cacheprovider \
  tests/test_lean_sandbox_e2e.py
```

Build the Lean closure when the workspace, toolchain, or proof boundary changes:

```bash
cd workspace
lake build Cslib
lake build
```

## 2. Check release artifacts

```bash
uv build
python -m zipfile -l dist/*.whl
```

CI builds the source distribution and wheel twice with the commit timestamp as
`SOURCE_DATE_EPOCH`. Their SHA-256 values must match between builds. CI also
audits the locked dependency graph and emits a CycloneDX SBOM.

Regenerate the README demonstration when its command or output changes:

```bash
python scripts/record_paper_demo.py
```

The recorder requires the configured live provider. It verifies the exact PDF,
checks the accepted evidence and export, then drives the versioned VHS tape.
The recorder and tape contain the command and human review guidance. Provider
responses enter the generated provenance records at runtime.

## 3. Merge through the required gate

Open a pull request against `main`. The `Required` check aggregates the Python,
dependency, Lean/Nix, sandbox, and reproducible-package jobs. Resolve every
review thread and update the branch before squash merging.

The repository accepts squash merges, keeps a linear history, and deletes
merged topic branches. Require the `Required` check through a branch rule when
the repository plan exposes private-repository rules. GitHub's current free
private-repository plan does not expose that control, so maintainers merge only
after inspecting the aggregate check. Apply and verify the rule before changing
the repository to public visibility.

## 4. Verify the immutable release

Each qualified `main` commit receives a Hashver identity:

```text
YYYY.MM.DD+<12-character-commit>
```

The release contains the wheel, source distribution, dependency SBOM, proof
environment, and `release-manifest.json`. The manifest binds every asset to its
SHA-256 and the full source commit.

```bash
gh release view --repo LeonardAukea/autolean
gh release view --repo LeonardAukea/autolean \
  --json tagName,isImmutable,targetCommitish
gh release verify --repo LeonardAukea/autolean
gh release download --repo LeonardAukea/autolean --dir release
python -m json.tool release/release-manifest.json
```

The CI release job attaches every asset and publishes the immutable release.
The `Verify release` workflow begins after successful CI and allows 15 minutes
for GitHub's asynchronous release attestation to appear. A manual dispatch
with the exact tag repeats verification against the same immutable assets.

Repository release immutability was enabled on 2026-08-12. Earlier private
qualification releases remain mutable and are not public distribution
artifacts.

Do not move or reuse a release tag. Correct a failed release with a new pull
request; the resulting commit receives its own identity and evidence.

The [environment reference](../reference/environment.md) defines the proof
identity recorded in every accepted result.

The repository remains private until the separate
[public launch gate](open-the-repository.md) passes.

## 5. Publish the Python distribution

Python publication is an explicit maintainer action from an immutable GitHub
release. The distribution name is `autolean-proof` and the installed command
is `autolean`.

Configure a PyPI trusted publisher for this repository, the `Publish Python`
workflow, and the `pypi` environment. Then dispatch the workflow with the exact
Hashver tag. The workflow verifies GitHub's release attestation, downloads the
qualified wheel and source distribution, and exchanges its GitHub OIDC token
directly with PyPI.

GitHub OIDC supplies a short-lived publication credential. PyPI's immutable
version records make a repeated version fail closed.
