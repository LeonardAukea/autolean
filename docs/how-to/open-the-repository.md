# Maintain public distribution

The repository is public. Its default-branch ruleset requires a pull request,
resolved review threads, signed commits, linear history, deletion protection,
and the aggregate `Required` check. The routine qualification commands live
in [Qualify a release](release.md).

## Audit publication surfaces

Review source, complete reachable history, assets, distribution contents,
Actions logs, and release artifacts for credentials, personal data, and
unpublished research. Check copied assets against their source licenses.
Generated proofs, training records, logs, and local indexes belong to their
research workspace.

The public community boundary consists of the license, citation record,
security-reporting route, contributor guide, support route, governance,
code of conduct, issue forms, and pull-request template. Keep their contact
routes usable and their ownership explicit.

## Check repository controls

Verify the default-branch ruleset, secret scanning, push protection,
Dependabot updates, CodeQL, release immutability, and full-SHA action pins.
These controls are repository settings as well as source configuration.
Inspect the live settings before relying on their enforcement.

Require the complete CI gate on the exact release commit. Validate supported
Python versions, the declared Nix systems, native containment, the standalone
export, dependency SBOM, and reproducible distribution bytes. Record the
platform and commit for every result.

## Qualify each installation route

An anonymous user must be able to clone the source, enter the development
shell, and run the first-proof tutorial. Downloaded wheels must pass the
installed-package smoke test outside the checkout. Release downloads must
match their manifest and attestations.

PyPI publication requires a trusted publisher with these exact fields:

| Field | Value |
| --- | --- |
| Project | `autolean-proof` |
| Owner | `LeonardAukea` |
| Repository | `autolean` |
| Workflow filename | `publish-pypi.yml` |
| Environment | `pypi` |

The publisher is configured in the owning PyPI account. The publication
workflow accepts an immutable, qualified GitHub release tag. Verify registry
installation after the workflow succeeds, using the
[publication procedure](release.md#6-publish-the-python-distribution).
