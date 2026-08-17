# Open the repository

This gate governs the change from private to public visibility. Changing
visibility is a separate maintainer action after the qualifying pull request
merges.

## 1. Audit the complete history

Scan every reachable commit for credentials, personal data, unpublished paper
text, model transcripts, logs, training records, and generated research state.
Check each vendored or copied asset for its license and source. Remove a secret
from its upstream service before repairing Git history.

Require a clean unauthenticated clone to pass the release guide from setup
through the README demonstration. Check the wheel, source distribution, release
assets, GitHub Actions logs, caches, and old workflow artifacts as publication
surfaces.

## 2. Finish the community boundary

Verify the public contact addresses, support route, moderation owner, security
advisory process, issue labels, issue forms, pull-request template, license,
citation record, code of conduct, changelog, and release notes. Read each page
as a first-time contributor and remove private operational detail.

Reserve the `autolean-proof` PyPI distribution and configure its trusted
publisher for the `Publish Python` workflow and `pypi` environment.

Confirm that one maintainer can acknowledge security reports and review pull
requests while another person is unavailable. Record any single-maintainer
limit in the launch decision.

## 3. Qualify the public build

Require the aggregate `Required` job and CodeQL on the launch commit. Run the
host containment suite on macOS and Linux. Build the standalone paper artifact
and its LaTeX companion from a clean directory. Verify the release manifest,
SBOM, proof-environment record, and byte-identical Python distributions.

Keep the release qualified for supported Python versions and every declared Nix
system. Record unavailable hardware or builders as explicit boundaries.

## 4. Change visibility once

Review GitHub's [visibility-change warnings] immediately before publication.
Making the repository public exposes source, Actions history, logs, and prior
activity. It also enables public forks.

After changing visibility:

1. create a `main` ruleset that requires pull requests, the `Required` check,
   resolved review threads, linear history, and deletion protection;
2. enable secret scanning, push protection, code scanning, the dependency
   graph, Dependabot alerts, and Dependabot security updates through the
   [security settings];
3. add the supported [Nix ecosystem] to Dependabot for `flake.lock`;
4. verify release immutability, full-SHA action pinning, and the selected-action
   allowlist;
5. verify Issues, Discussions, security advisories, merge policy, topic labels,
   the community profile, and the unauthenticated README links;
6. run the public CI and download the resulting release as an anonymous user.

GitHub disables push rulesets during a private-to-public visibility change.
Create and verify the public ruleset after the change. The private Free
repository uses maintainer review of the aggregate check because
[private rulesets] require a paid plan.

## 5. Record the decision

Open one launch issue containing the qualified commit, evidence links, known
boundaries, maintainer approval, and publication time. Close it only after the
anonymous installation and release-download checks pass.

The routine release process remains in [Qualify a release](release.md).

[Nix ecosystem]: https://docs.github.com/en/code-security/reference/supply-chain-security/supported-ecosystems-and-repositories
[private rulesets]: https://docs.github.com/en/repositories/configuring-branches-and-merges-in-your-repository/managing-rulesets
[security settings]: https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/enabling-features-for-your-repository/managing-security-and-analysis-settings-for-your-repository
[visibility-change warnings]: https://docs.github.com/en/repositories/managing-your-repositorys-settings-and-features/managing-repository-settings/setting-repository-visibility
