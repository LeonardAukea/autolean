# Security policy

## Supported code

Security fixes target `main` and the latest GitHub release. Older releases may
receive a fix when the affected boundary remains compatible.

## Report a vulnerability

Use a [private security advisory][advisory]. Include:

- the affected commit or release;
- the expected and observed trust boundary;
- a minimal reproduction;
- the operating system, sandbox, Lean toolchain, and model backend;
- the impact on source, credentials, network access, or proof acceptance.

Do not open a public issue for a suspected vulnerability. The maintainer will
acknowledge a complete report within seven days and coordinate validation,
repair, disclosure, and credit with the reporter.

Security-sensitive defects include sandbox escape, generated-source policy
bypass, credential exposure, provenance mismatch, and acceptance of a proof
whose declaration or axiom set was not audited. A disagreement about the
mathematical statement is a source-fidelity issue unless it crosses one of
these boundaries.

[advisory]: https://github.com/LeonardAukea/autolean/security/advisories/new
