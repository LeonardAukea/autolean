<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)"
            srcset="docs/assets/banner-dark.svg">
    <img src="docs/assets/banner-light.svg" alt="AutoLean" width="900">
  </picture>
</p>

<p align="center">
  <a href="https://github.com/LeonardAukea/autolean/actions/workflows/ci.yml"><img src="https://github.com/LeonardAukea/autolean/actions/workflows/ci.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/leanprover/lean4/releases/tag/v4.33.0"><img src="https://img.shields.io/badge/Lean-4.33.0-0d9488" alt="Lean 4.33.0"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-2563eb.svg" alt="License: MIT"></a>
</p>

AutoLean turns a mathematical goal into a reviewable research plan, a Lean 4
declaration, and a kernel-checked proof. It treats the model as an untrusted
programmer: generated source is accepted only after a source policy, an
operating-system sandbox, elaboration by a pinned Lean toolchain, and a
declaration and axiom audit. What survives is committed with its provenance —
model, prompt, environment, and axiom report.

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

The recording audits a real formalization paper (arXiv:2506.18616v5) with the
configured Claude backend: provider-planned claim extraction, human revision,
sandboxed elaboration of every mapped declaration, and a standalone Lean and
LaTeX export. The tape holds the live command; the manifest identifies the
run.

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
provider — a Claude or Codex subscription, a hosted API, or a local model.
See [Choose and switch models](docs/how-to/choose-a-model.md).

Use `autolean workbench` for the interactive interface. The
[first-proof tutorial](docs/tutorials/first-proof.md) walks each step and ends
with a standalone Lean project and companion LaTeX paper. CI replays that
tutorial against a scripted model on every change, so the documented path
stays the working path.

## Workflows

- `autolean plan STATEMENT` develops a mathematical strategy without changing
  source.
- `autolean prove STATEMENT` formalizes one claim and starts a bounded proof
  session.
- `autolean solve` works through existing `sorry` targets; `autolean resume`
  continues saved work.
- `autolean verify SOURCE` extracts and formalizes claims from arXiv HTML,
  PDF, or a local paper.
- `autolean problems` searches curated open problems and creates bounded work.
- `autolean export OUTPUT` writes a standalone Lean project, provenance
  manifest, and companion LaTeX paper.

Every mutating workflow records a resumable session and runs under an explicit
cycle budget. The [research loop](docs/explanation/research-loop.md) explains
how plans, experiments, and failure evidence drive each cycle.

## How a proof gets accepted

```mermaid
flowchart LR
    goal["goal +<br/>bounded context"] --> model["model<br/>proposal"]
    model --> policy["source<br/>policy"]
    policy --> sandbox["OS sandbox<br/>no network"]
    sandbox --> lean["pinned Lean<br/>elaboration"]
    lean --> audit["declaration +<br/>axiom audit"]
    audit --> commit["exact<br/>source commit"]
    lean -. "diagnostics become<br/>next-cycle evidence" .-> model
```

The Lean project and pinned dependencies are trusted inputs. Model responses,
paper text, search results, and learned skills are untrusted data. Lean's
parser, elaborator, and kernel decide acceptance; everything before them can
only reject early. The
[trust boundary](docs/explanation/trust-boundary.md) states the complete
security and data-handling model, including
[why elaboration runs in an OS sandbox](docs/explanation/trust-boundary.md#why-an-operating-system-sandbox).

## Documentation

The documentation follows the four-part [Diátaxis](https://diataxis.fr/)
structure; the [documentation index](docs/README.md) lists every page. Start
with the [first-proof tutorial](docs/tutorials/first-proof.md), the
[architecture](docs/explanation/architecture.md), and the
[trust boundary](docs/explanation/trust-boundary.md).

## Requirements

- macOS on Apple Silicon, AArch64 Linux, or x86-64 Linux
- Nix with flakes enabled
- a supported model subscription, hosted API, or local inference server

The Python package supports Python 3.11 through 3.14; the Nix development
shell is the release-qualified environment. The core is MIT licensed; PDF
extraction is an explicit extra with its own license terms. The
[dependency reference](docs/reference/dependencies.md) lists installation
profiles and the license boundary.

## Contributing and security

Read [CONTRIBUTING.md](CONTRIBUTING.md) before changing code or documentation.
Report vulnerabilities through the private process in
[SECURITY.md](SECURITY.md). [SUPPORT.md](SUPPORT.md) routes questions and bug
reports. [GOVERNANCE.md](GOVERNANCE.md) states ownership and decisions.
User-visible changes are recorded in [CHANGELOG.md](CHANGELOG.md).

AutoLean is available under the [MIT License](LICENSE).
