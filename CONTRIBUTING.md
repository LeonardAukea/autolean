# Contributing to AutoLean

AutoLean accepts focused changes with a testable contract. A change should make
one subsystem easier to understand without weakening the proof boundary.

## Before you start

Search the [issues](https://github.com/LeonardAukea/autolean/issues) before
starting broad work. Open a proposal when a change affects commands, stored
data, provider contracts, proof acceptance, or release policy.

Report security problems through the process in [SECURITY.md](SECURITY.md).

## Development environment

Enter the pinned development shell and install the locked Python environment:

```console
nix develop
uv sync --all-extras --all-groups
```

The shell supplies Lean, Lake, solvers, sandbox tools, and the `autolean`
command. The uv environment supplies Python development tools and optional
model SDKs.

Run a focused test while working. Run the repository gates before opening a
pull request:

```console
uv run ruff check .
uv run ruff format --check .
uv run mypy autolean
uv run pytest -q
nix flake check .
```

Changes to generated-code policy, process isolation, or Lean validation also
run the host containment suite:

```console
AUTOLEAN_RUN_SANDBOX_E2E=1 \
  uv run pytest -q -p no:cacheprovider tests/test_lean_sandbox_e2e.py
```

## Design rules

The [engineering discipline](docs/explanation/engineering.md) defines the
contract, failure, readability, debugging, testing, and review model used by
this repository.

- Give each invariant one owner.
- Keep provider code behind the common backend protocol.
- Keep model output outside the trusted boundary until Lean accepts it.
- Treat Tree-sitter structure as prompt context. Lean decides correctness.
- Preserve exact source, environment, model, and validation provenance.
- Prefer small data types and functions with explicit inputs and results.
- Return errors at the boundary that can act on them.

Tests should cover success, rejection, and the edge that separates them. A test
total is evidence of execution; its assertions carry the contract.

AutoLean writes learned skills, proof attempts, generated sources, and training
records under `workspace/`. These are local research state and do not belong in
source-control changes.

## Documentation

Documentation follows [Diátaxis](https://diataxis.fr/); the
[documentation index](docs/README.md) states where each kind of information
belongs. Put each fact in one place and link to it elsewhere. Write current behaviour in
plain sentences. Wrap prose at 80 columns.

The terminal demo is reproducible:

```console
python scripts/record_paper_demo.py
vhs docs/demos/ionescu-tulcea.tape
```

## Commits and pull requests

Use an imperative subject prefixed by the subsystem:

```text
Sandbox: Bind candidate source to its audit
```

The body explains the problem and the constraint that shapes the change. Keep
the subject near 50 columns and wrap the body at 72.

A pull request should state its invariant, user-visible effect, verification,
and remaining qualification boundary. Keep unrelated changes in separate pull
requests.
