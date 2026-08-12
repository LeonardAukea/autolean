# Governance

AutoLean has one maintainer, Leonard Aukea. The maintainer owns repository
access, releases, security response, moderation, and the final decision to
accept a change.

## Decisions

Changes begin with an issue when they alter a command, stored record, provider
contract, proof boundary, or release policy. The proposal states the invariant
and the evidence that decides acceptance. Discussion seeks agreement through
source, tests, and reproducible results. The maintainer records the decision in
the issue or pull request.

Accepted changes pass the aggregate `Required` job and receive a focused
review. Security and proof-acceptance changes also pass the native containment
suite. A passing check establishes only the boundary named by that check.

## Releases

CI publishes a Hashver release from each qualified `main` commit. GitHub locks
the tag and assets when the release is published and emits an attestation for
them. The maintainer may stop publication when a boundary is uncertain; the
next qualified commit receives a new identity.

The repository remains private until the
[public launch gate](docs/how-to/open-the-repository.md) passes. Visibility
changes require an explicit maintainer decision and are never part of routine
release automation.

## Security and conduct

Private security reports follow [SECURITY.md](SECURITY.md). Community conduct
follows [CODE_OF_CONDUCT.md](CODE_OF_CONDUCT.md). The maintainer separates
security response from public issue discussion and limits report access to the
people needed for resolution.

## Continuity

If maintenance pauses, the README and repository description will state that
status before new contributions are solicited. A future maintainer receives
repository access only after the security, release, and proof-boundary duties
are transferred explicitly.
