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

Regenerate a recorded demonstration when its command or output changes:

```bash
python scripts/record_prove_demo.py    # README front page
python scripts/record_paper_demo.py    # paper audit
```

Each recorder requires the configured live provider. It verifies the input
identity, checks the accepted evidence and export, then drives the versioned
VHS tape. The recorder and tape contain the command and any human review
guidance. Provider responses enter the generated provenance records at
runtime.

## 3. Merge through the required gate

Open a pull request against `main`. The `Required` check aggregates the
Lean-grammar, Python matrix, dependency-audit, dependency-review, Lean/Nix,
and reproducible-package jobs; the sandbox containment attacks and the
tutorial replay are steps inside the Lean/Nix job. Resolve every review thread
and update the branch before squash merging.

The repository accepts squash merges, keeps a linear history, and deletes
merged topic branches. Branch-rule enforcement of the `Required` check is
owned by the [public launch gate](open-the-repository.md); merge only after
inspecting the aggregate check where no rule enforces it.

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
gh release download --repo LeonardAukea/autolean --dir release
python -m json.tool release/release-manifest.json
```

The CI release job attaches every asset and publishes the immutable release.
The `Verify release` workflow checks the immutable flag, exact tag and commit,
complete asset set, file sizes, and SHA-256 values from the manifest. A manual
dispatch with the exact tag repeats the same verification.

GitHub supplies release attestations for public repositories on the current
plan. The verifier also runs `gh release verify` after the repository becomes
public. Private repository attestations require GitHub Enterprise Cloud; the
[GitHub availability contract](https://docs.github.com/en/actions/how-tos/secure-your-work/use-artifact-attestations/use-artifact-attestations)
defines that qualification boundary.

Two attestations cover every asset. The release attestation establishes that
GitHub bound the asset digest to the immutable release, its tag, and its
commit. The build provenance attestation establishes that the CI release
workflow run built exactly those bytes from that commit (SLSA Build Level 2).
Verify a downloaded asset with gh 2.81 or later:

```bash
gh release verify-asset --repo LeonardAukea/autolean <tag> \
  release/autolean_proof-*.whl
gh attestation verify release/autolean_proof-*.whl \
  --repo LeonardAukea/autolean \
  --signer-workflow LeonardAukea/autolean/.github/workflows/ci.yml
```

Repository release immutability was enabled on 2026-08-12. Earlier private
qualification releases remain mutable and are not public distribution
artifacts. Build provenance exists for releases created after the repository
became public.

## What signs a release

Three signatures cover the path from a commit to a downloaded file, and each
answers a different question.

| Signature | Key | Answers |
| --- | --- | --- |
| Commit | the maintainer's SSH key, or GitHub's key on a squash merge | who wrote this source |
| Release attestation | Sigstore, keyed to the repository | GitHub bound this digest to this tag and commit |
| Build provenance | Sigstore, keyed to the workflow identity | this workflow run built these bytes from that commit |

Commits are signed before they reach the repository. The maintainer signs
locally with an SSH key; a squash merge is signed by GitHub, which reports
both as verified. Enforce it so an unsigned commit cannot reach `main`:

```bash
gh api -X PUT repos/LeonardAukea/autolean/rulesets/<id> \
  --input ruleset-with-required-signatures.json
```

Release assets are signed without a long-lived key. The CI job exchanges a
short-lived OIDC token for a Sigstore certificate bound to the workflow
identity, signs the asset digests, and records the signature in a public
transparency log. `gh attestation verify --signer-workflow` fails unless the
bytes came from that workflow in this repository.

A stored signing key would weaken this. It would have to live in a repository
secret, so anyone who could read that secret could sign anything, forever, and
nothing outside the repository would record that it happened. The keyless
certificate expires in minutes, names the workflow that requested it, and
leaves a public log entry. The key that does belong to a person — the
maintainer's — signs the commits a person actually wrote.

Do not move or reuse a release tag. Correct a failed release with a new pull
request; the resulting commit receives its own identity and evidence.

The [environment reference](../reference/environment.md) defines the proof
identity recorded in every accepted result.

Repository visibility is governed by the separate
[public launch gate](open-the-repository.md).

## 5. Point the Homebrew formula at the release

The formula pins one immutable release by URL and SHA-256, so it names a
prior release until it is moved:

```bash
python scripts/update_formula.py <tag>
brew update-python-resources Formula/autolean.rb   # only when a runtime
                                                   # dependency changed
brew style Formula/autolean.rb
brew install --build-from-source Formula/autolean.rb
brew test autolean
```

A policy test fails when a runtime dependency has no matching resource, so
the formula cannot silently stop installing.

## 6. Publish the Python distribution

Python publication is an explicit maintainer action from an immutable GitHub
release. The distribution name is `autolean-proof` and the installed command
is `autolean`.

Configure a PyPI trusted publisher for this repository, the `Publish Python`
workflow, and the `pypi` environment. Then dispatch the workflow with the exact
Hashver tag. The workflow verifies the immutable release, source identity,
manifest, and downloaded distributions. Public repositories also require the
GitHub release attestation and build provenance for each distribution. The
publisher exchanges its GitHub OIDC token directly with PyPI.

GitHub OIDC supplies a short-lived publication credential. PyPI's immutable
version records make a repeated version fail closed.

PyPI records a PEP 740 attestation establishing that this repository's
trusted publisher uploaded each distribution. Verify a published file with
[pypi-attestations](https://pypi.org/project/pypi-attestations/):

```bash
pypi-attestations verify pypi \
  --repository https://github.com/LeonardAukea/autolean \
  pypi:autolean_proof-<version>-py3-none-any.whl
```
