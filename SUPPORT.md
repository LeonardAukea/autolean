# Support

Use [GitHub Discussions] for setup questions, model configuration, mathematical
workflow questions, and early design ideas. Search existing discussions before
opening a new one.

Use the [bug report] for a reproducible defect. Include the complete command,
session ID, observed output, and these diagnostics:

```console
autolean doctor
autolean environment --project workspace --json
```

Attach source only when it is safe to share. Paper text, model responses,
generated proofs, logs, and environment records can contain private material.

Report a suspected security defect through the private process in
[SECURITY.md](SECURITY.md). That process covers sandbox escape, credential
exposure, source-policy bypass, and proof-provenance mismatch.

Use the [paper profile] form for a reviewed paper revision. It requires exact
artifact hashes, a numbered source inventory, Lean mappings, acceptance
evidence, and the remaining source-fidelity boundary.

[GitHub Discussions]: https://github.com/LeonardAukea/autolean/discussions
[bug report]: https://github.com/LeonardAukea/autolean/issues/new?template=bug.yml
[paper profile]: https://github.com/LeonardAukea/autolean/issues/new?template=paper.yml
