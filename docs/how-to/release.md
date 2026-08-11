# Qualify and publish a release

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
python scripts/record_pythagorean_demo.py
```

The recorder uses deterministic local model responses and the real Lean
acceptance path. Pass `--live` only when provider variability is part of the
demonstration.

## 3. Merge through the required gate

Open a pull request against `main`. The `Required` check aggregates the Python,
dependency, Lean/Nix, sandbox, and reproducible-package jobs. Resolve every
review thread and update the branch before squash merging.

`main` rejects direct pushes, force pushes, and deletions. The repository keeps
a linear history and deletes merged topic branches.

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
gh release download --repo LeonardAukea/autolean --dir release
python -m json.tool release/release-manifest.json
```

Do not move or reuse a release tag. Correct a failed release with a new pull
request; the resulting commit receives its own identity and evidence.

The [environment reference](../reference/environment.md) defines the proof
identity recorded in every accepted result.
