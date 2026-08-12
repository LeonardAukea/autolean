# AutoLean

[![CI](https://github.com/LeonardAukea/autolean/actions/workflows/ci.yml/badge.svg)](https://github.com/LeonardAukea/autolean/actions/workflows/ci.yml)
[![Lean 4.33.0](https://img.shields.io/badge/Lean-4.33.0-0d9488)](https://github.com/leanprover/lean4/releases/tag/v4.33.0)
[![License: MIT](https://img.shields.io/badge/license-MIT-2563eb.svg)](LICENSE)

```text
 ______     __  __     ______   ______     __         ______     ______     __   __
/\  __ \   /\ \/\ \   /\__  _\ /\  __ \   /\ \       /\  ___\   /\  __ \   /\ "-.\ \
\ \  __ \  \ \ \_\ \  \/_/\ \/ \ \ \/\ \  \ \ \____  \ \  __\   \ \  __ \  \ \ \-.  \
 \ \_\ \_\  \ \_____\    \ \_\  \ \_____\  \ \_____\  \ \_____\  \ \_\ \_\  \ \_\\"\_\
  \/_/\/_/   \/_____/     \/_/   \/_____/   \/_____/   \/_____/   \/_/\/_/   \/_/ \/_/
```

AutoLean turns a mathematical goal into a reviewable plan, a Lean 4
declaration, and a kernel-checked proof. Model output is untrusted source code.
AutoLean accepts it only after source-policy checks, operating-system
isolation, Lean elaboration, declaration-range checks, and an axiom audit.

AutoLean is alpha research software. A successful run proves the exact Lean
statement shown in the result. It does not prove that a generated statement
faithfully expresses an informal claim.

<p align="center">
  <img
    src="docs/assets/autolean-ionescu-tulcea.gif"
    alt="AutoLean audits the Ionescu-Tulcea formalization paper"
    width="960"
  >
</p>

<p align="center">
  <a href="docs/assets/autolean-ionescu-tulcea.mp4">MP4</a>
  ·
  <a href="docs/demos/ionescu-tulcea.tape">VHS source</a>
  ·
  <a href="docs/demos/ionescu-tulcea.json">Run manifest</a>
</p>

The recording uses the configured Claude backend and the exact PDF for
arXiv:2506.18616v5. It shows provider-generated planning, human revision, all
25 paper items, 33 mapped Lean declarations, sandboxed elaboration, durable
session state, and a standalone Lean and LaTeX export. The tape contains the
live command and explicit human review guidance. Provider responses enter the
generated provenance records at runtime.

## Start here

Nix supplies the pinned Python, Lean, Mathlib, CSLib, and sandbox tools.

```bash
git clone https://github.com/LeonardAukea/autolean.git
cd autolean
nix develop

autolean models
claude                    # enter /login
# or: codex login
autolean doctor
autolean prove "the Pythagorean theorem" --review-plan --max-attempts 5
```

The automatic default selects the strongest profile for an authenticated
provider. See [Choose and switch models](docs/how-to/choose-a-model.md) to pin
a provider or model.

Use `autolean workbench` for the interactive interface. The
[first-proof tutorial](docs/tutorials/first-proof.md) explains each step and
ends with a standalone Lean project and companion LaTeX paper.

## Workflows

- `autolean plan STATEMENT` develops a mathematical strategy without changing
  source.
- `autolean prove STATEMENT` formalizes one claim and starts a bounded proof
  session.
- `autolean solve` works through existing `sorry` targets; `autolean resume`
  continues saved work.
- `autolean verify SOURCE` extracts and formalizes claims from arXiv HTML, PDF,
  or a local paper.
- `autolean problems` searches curated open problems and creates bounded work.
- `autolean export OUTPUT` writes a standalone Lean project, provenance
  manifest, and companion LaTeX paper.

Every mutating workflow records a resumable session. Five proof cycles is the
default budget. Unlimited work requires `--overnight` or an explicit zero
budget.

## Trust boundary

The Lean project and pinned dependencies are trusted inputs. Model responses,
paper text, nearby comments, search results, and learned skills are untrusted.

```text
goal + bounded context
        |
        v
   model proposal
        |
        v
 source policy -> OS sandbox -> pinned Lean
                                  |
                                  v
                     declaration + axiom audit
                                  |
                             success only
                                  v
                         exact source edit
```

Tree-sitter supplies advisory structure for prompts. Lean's parser,
elaborator, and kernel decide acceptance. The
[trust-boundary explanation](docs/explanation/trust-boundary.md) states the
complete security and data-handling model.

## Documentation

The documentation follows the four-part
[Diátaxis](https://diataxis.fr/) structure.

### Tutorials

- [Prove a first theorem](docs/tutorials/first-proof.md)

### How-to guides

- [Choose and switch models](docs/how-to/choose-a-model.md)
- [Run a research session](docs/how-to/run-a-research-session.md)
- [Verify a paper](docs/how-to/verify-a-paper.md)
- [Qualify and publish a release](docs/how-to/release.md)
- [Open the repository](docs/how-to/open-the-repository.md)

### Reference

- [Command-line interface](docs/reference/cli.md)
- [`program.md` configuration](docs/reference/program.md)
- [Proof environments and provenance](docs/reference/environment.md)
- [Research artifact records](docs/reference/research-artifacts.md)

### Explanation

- [Trust boundary](docs/explanation/trust-boundary.md)
- [Research and proof loop](docs/explanation/research-loop.md)

The [documentation index](docs/README.md) describes where each kind of
information belongs.

## Requirements

- macOS on Apple Silicon, AArch64 Linux, or x86-64 Linux
- Nix with flakes enabled
- a supported model subscription, hosted API, or local inference server

The Python package supports Python 3.11 through 3.14. The Nix development
shell is the release-qualified path because it also pins Lean and native
containment tools.

## Contributing and security

Read [CONTRIBUTING.md](CONTRIBUTING.md) before changing code or documentation.
Report vulnerabilities through the private process in
[SECURITY.md](SECURITY.md). [SUPPORT.md](SUPPORT.md) routes questions and bug
reports. [GOVERNANCE.md](GOVERNANCE.md) states ownership and decisions.
User-visible changes are recorded in [CHANGELOG.md](CHANGELOG.md).

AutoLean is available under the [MIT License](LICENSE).
