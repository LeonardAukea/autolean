# AutoLean

AutoLean is an autonomous Lean 4 proof agent. It finds `sorry` targets,
requests candidate proofs, validates them with Lean inside an operating-system
sandbox, and keeps only candidates accepted by the kernel.

## Quick start

```bash
nix develop
uv sync --all-extras --all-groups

# Authenticate the default Claude subscription backend once.
claude                    # enter /login in the interactive session

autolean doctor
autolean environment
autolean workbench
```

The shipped default is the `opus` subscription profile. Use
`autolean models` to see every profile and the setup state AutoLean can observe
locally. The Nix development shell installs the command directly. Use
`uv run autolean` while iterating on Python source or optional API extras in the
uv environment.

## Mental model

The Lean project and its pinned dependencies are trusted inputs. Nearby source
comments, search results, paper text, and every model completion are untrusted.

```text
program.md + selected target
             |
             v
 bounded Tree-sitter outline
    + Lean goal state
             |
             v
       LLMBackend protocol
             |
             v
   generated-source policy
             |
             v
  scratch copy + minimal env
             |
             v
 sandbox-exec (macOS) / Bubblewrap (Linux)
        no network, scratch-only writes
             |
             v
       pinned Lean compiler
             |
 source-range + axiom audit
             v
 proof SHA + environment SHA
             |
        success only
             v
 exact source edit -> scoped Git commit
```

The agent never validates generated source with `lake build`. It invokes the
pinned Lean binary directly, gives it compiled dependencies from `.lake`, and
places the candidate and compiler outputs in a temporary directory. A candidate
does not enter the project tree until this check succeeds.

The source policy rejects placeholders, axiom escapes, hidden bidirectional
controls, command injection, elaborator hooks, and explicit I/O forms before
Lean runs. This policy is defense in depth; the OS sandbox is the authority
boundary because Lean elaboration can execute metaprograms.

Target discovery uses a bounded source scanner. Final acceptance imports the
compiled candidate into a fresh Lean audit module and requires Lean's recorded
declaration range to enclose the exact target line. The same audit collects
transitive axioms from the checked environment. AutoLean allows Lean's
foundational `propext`, `Quot.sound`, and `Classical.choice` axioms. It rejects
`sorryAx`, compiler-trust axioms, project axioms, and every unrecognized
dependency. Anonymous `example` targets remain untouched because they have no
declaration name that Lean can audit.

Lean's extensible parser and elaborator are the canonical syntax authority.
AutoLean also parses each target with
[`tree-sitter-lean`](https://github.com/Julian/tree-sitter-lean) to give the
model a bounded outline: imports, namespace, declaration span, syntax path,
referenced earlier declarations, and neighbouring declarations. The outline
includes the grammar and parser versions, source SHA-256, recovery status, and
recovered error spans. It is cached by source hash and rendered within a fixed
6,000-character budget.

Tree-sitter context is advisory. Lean projects can extend syntax and macros at
runtime, beyond the scope of an editor grammar. A recovered or unavailable
outline remains visible in the prompt and never replaces the Lean goal state
or kernel check. The
[`lean4-tree-sitter`](https://github.com/predictable-machines/lean4-tree-sitter)
project supplies a useful design reference for typed schema extraction from
Lean; AutoLean uses the Python grammar bundle because the agent itself is a
Python application.

`autolean environment` hashes the selected Lean executable, the exact Lake
configuration and manifest, the toolchain's compiled library, and every
importable project and dependency `.olean` or native library. Each successful
record contains this environment SHA-256, the exact proof-text SHA-256, the
served model and backend, and the axiom report. The environment is hashed
before and after final elaboration, so a dependency change makes acceptance
fail closed.

Accepted proof edits replace exactly one selected `sorry` and reduce the
file's placeholder count by one. Git setup and commits fail closed, verify the
active proof branch, and commit only the proven file. Existing staged,
modified, and untracked work remains outside the proof commit.

`--dry-run` keeps the complete project tree unchanged: source, logs, results,
skills, and training data remain in memory or are skipped. It still sends the
selected context to the configured model, constructs the exact candidate in
memory, and runs the generated-code policy and sandboxed Lean audit. A passing
candidate is reported as `validated`; it is never installed or committed.

`autolean inspect TARGET` prints the exact structural context used by the
agent. `--format json` exposes the same source and context hashes to tooling,
and `--goal-state` adds Lean's elaborated goal.

`autolean doctor` is a trusted-project diagnostic. It asks the selected model
to prove `True`, validates that exact completion in the generated-code sandbox,
checks its declaration range and axioms, and then runs the project's normal
build. Invoke it only for a Lean project whose existing source and dependencies
you trust.

## Reproducible Lean environment

The repository pins Lean 4.33.0, Mathlib 4.33.0, and CSLib 4.33.0. The checked
in `lake-manifest.json` resolves every transitive Lake dependency to a Git
commit. `lake exe cache get` installs Mathlib's matching compiled cache, and
`lake build Cslib` builds CSLib against the same toolchain. The workspace smoke
module imports both libraries and is part of the default build.

The Nix flake fetches the official Lean 4.33.0 release archive for each
supported platform by SHA-256. A flake check executes that binary and rejects a
version mismatch. The Nix shell and packaged CLI therefore use the same Lean
release as `lean-toolchain` on Apple Silicon, AArch64 Linux, and x86-64 Linux.
The Tree-sitter runtime wheels and platform grammar bundles are also fetched by
SHA-256. The packaged CLI loads the Lean grammar directly from the Nix store,
so parsing needs no runtime download or writable grammar cache.

`autolean init` enables Mathlib and CSLib by default. A core-only experiment
can select `--no-mathlib --no-cslib`. A complete setup is:

```bash
uv run autolean init my_project
cd my_project
lake update
lake exe cache get
lake build
cd ..
uv run autolean environment --project my_project
```

The lock and content hashes make validation replayable. Hosted model aliases
do not identify immutable weights, and providers can vary a completion even
with the same request. AutoLean therefore treats generation as nondeterministic
proposal search and makes acceptance deterministic, local, content-addressed,
and kernel checked.

## Models and backends

Seven backends implement one `LLMBackend` protocol:

- `claude_cli`: Claude through a Claude subscription.
- `codex_cli`: GPT through a ChatGPT subscription.
- `anthropic`: Claude through hosted API credit.
- `openai`: GPT through hosted API credit.
- `ollama`: local inference.
- `openai_compat`: a self-hosted OpenAI-compatible server.
- `muse_glimmer`: Muse Glimmer through llama.cpp or vLLM.

### Subscription profiles

The current subscription profiles are:

- `fable`, `opus`, and `sonnet` for Claude Fable 5, Opus 5, and Sonnet 5.
- `codex`, `codex-terra`, and `codex-luna` for GPT-5.6 Sol, Terra, and
  Luna.

```bash
uv run autolean solve --model opus
uv run autolean solve --model codex-luna
```

AutoLean requires Claude authentication reported as `claude.ai` with a
subscription type, or Codex authentication reported as ChatGPT login. Provider
API key variables are removed from subscription subprocesses, so an API-key
login cannot silently satisfy the subscription profile. The preflight verifies
the binary, credential, and billing mode; the first generation establishes
current quota and provider availability.

Each request starts in an empty temporary directory. Claude uses safe mode,
an empty built-in tool set, an empty strict MCP configuration, disabled skills,
and no session persistence. Codex ignores user configuration and project rules,
uses an ephemeral read-only sandbox, and disables every current action and
customization surface through strict configuration. Codex exposes one initial
instruction channel, so AutoLean places its system rules and target request in
that channel with an explicit separator.

The subscription CLIs expose reasoning effort and token accounting. Their
non-interactive interfaces do not expose temperature, stop sequences, or a
hard output-token ceiling. AutoLean's capability record prevents those request
fields from being presented as effective controls.

### Hosted API profiles

```bash
export ANTHROPIC_API_KEY=...
uv run autolean solve --model opus-api

export OPENAI_API_KEY=...
uv run autolean solve --model gpt-api
```

Hosted profiles append `-api` to the Claude profile names and use `gpt-api`,
`gpt-terra-api`, or `gpt-luna-api` for OpenAI. Anthropic requests use the
official SDK, adaptive thinking, streaming, refusal handling, and the current
server-side refusal fallback. OpenAI requests use the official Responses API,
reasoning effort, `store=false`, and completed-response validation. Both
backends reject empty, refused, malformed, and output-limited completions.

### Muse Glimmer

`muse-glimmer` is the native Apple Silicon and llama.cpp profile. It pins the
official GGUF repository revision and the exact 17 GB weight artifact:

```text
repository revision: 93769bc7ab5ad1e9cd22d857e3138cf5d977ae81
weight SHA-256:
  7e9b74b7c8875e9e265695df9613bf6290f2392e479ce740495a129019c488d8
```

Download and verify the file before serving it:

```bash
hf download meta-models/Muse-Glimmer-30B-GGUF \
  --revision 93769bc7ab5ad1e9cd22d857e3138cf5d977ae81 \
  --include muse-glimmer-30B-kquant-17gb.gguf \
  --local-dir muse-glimmer

shasum -a 256 muse-glimmer/muse-glimmer-30B-kquant-17gb.gguf
```

Build llama.cpp at the cookbook-qualified revision and start a loopback-only
server:

```bash
git clone https://github.com/ggml-org/llama.cpp
git -C llama.cpp checkout dd1ea524333b1e697489067d7a4c39c60d32beee
cmake -S llama.cpp -B llama.cpp/build \
  -DGGML_METAL=ON -DGGML_CCACHE=OFF -DCMAKE_BUILD_TYPE=Release
cmake --build llama.cpp/build --target llama-server -j

llama.cpp/build/bin/llama-server \
  --model muse-glimmer/muse-glimmer-30B-kquant-17gb.gguf \
  --alias muse-glimmer --host 127.0.0.1 --port 8080 \
  --ctx-size 32768 --parallel 1 --n-gpu-layers 99 \
  --jinja --reasoning-format deepseek
```

AutoLean sends Muse's `reasoning_strength` chat-template control, permits the
model's `low`, `medium`, `high`, and `xhigh` levels, and ships with `low` for
bounded validate-and-retry loops. It uses temperature zero and seed zero, and
keeps `<|eom|>` available for the model's internal turn state.
Run the semantic smoke test before a proof session:

```bash
uv run autolean doctor --model muse-glimmer
uv run autolean solve --model muse-glimmer --target TARGET --dry-run
```

`muse-glimmer-bf16` pins the official BF16 revision
`f84ecc3a0ea984a4c04542a84269e3d065350a6e` and targets a vLLM server on port
8000. The model profile records the revision but does not claim a single-file
weight hash because the BF16 snapshot contains multiple artifacts.

Generation can still vary across inference engines, hardware, kernels, and
thread schedules. AutoLean records the requested seed, model revision, weight
hash, prompt hash, structural-context hash, and measured token counts. Proof
acceptance remains deterministic because the pinned Lean environment checks
the exact returned text.

### Other local profiles

`gemma4`, `gemma4-31b`, `deepseek-prover`, `bfs-prover`, `bfs-prover-32b`,
and `ntpctx` use Ollama. `leanstral` uses an OpenAI-compatible server. Raw
model strings are also accepted:

```bash
ollama pull yinyaowenhua1314/deepseek-prover-v2-7b
uv run autolean solve --model deepseek-prover

uv run autolean solve \
  --backend openai_compat \
  --model my-server-model
```

## Interactive workbench

`autolean workbench` is the mathematician-facing interface. It keeps proof
targets, model selection, and validation evidence on one screen. A profile can
be selected from the registry, or a raw model ID can be paired with a backend
and HTTP endpoint. Reasoning effort, output limit, and experiment cycles are
session controls. Provider credentials stay in the environment and never
enter the interface or its temporary program file.

The default `Validate` action runs the complete model-to-sandboxed-kernel loop
with dry-run project semantics. `Accept proof` opens a confirmation dialog and
then runs the same `autolean solve` command without `--dry-run`. System checks
and structural goal inspection call `autolean doctor` and `autolean inspect`.
The TUI is therefore a composition layer over the scriptable CLI, with one
proof and configuration semantics.

Keyboard controls are visible in the footer: `/` filters targets, `Ctrl+D`
checks the system, `Ctrl+I` inspects the selected goal, `Ctrl+V` validates a
candidate, and `Ctrl+S` requests proof acceptance. The interface uses
Textual's headless interaction harness at both compact and wide terminal sizes.

## Configuration

`program.md` has two kinds of controls:

- Typed settings are validated and enforced before the run begins.
- Goals, constraints, and strategy hints are advisory context included in
  every model request.

The autonomous agent supports `sorry-elimination` mode. Formalization, proof
golf, paper verification, and library generation are explicit CLI workflows.

```markdown
## Mode

sorry-elimination

## Lean Project Path

workspace

## Goals

1. Produce complete proofs for the selected targets.

## Constraints

- Preserve the mathematical intent of each declaration.

## LLM Configuration

model: opus
temperature: 0.0
max_output_tokens: 32768
max_retries_per_sorry: 5
cycle_timeout_seconds: 120
llm_timeout_seconds: 600
max_proof_lines: 30

## Experiment Budget

max_cycles: 0
```

`temperature`, `effort`, stop sequences, seeds, and output limits apply only
when the selected backend advertises each control. A model profile supplies its
tuned reasoning effort unless `program.md` explicitly overrides it.
`num_predict` remains an accepted alias for local output limits.

CLI `--model` and `--backend` values take precedence over `program.md` and
behave identically across commands. `endpoint` configures a self-hosted HTTP
server; credentials remain in provider environment variables. Invalid
endpoints, timeouts, limits, efforts, and retry budgets fail before provider
or project work begins.

## Agent loop and experiment ledger

The loop uses a fixed experiment budget, stable target ordering, and explicit
terminal outcomes. Each attempt records `success`, `fail_build`, or `skipped`
with its proof, prompt, structural-context, environment, model, and artifact
identities. Token input and output are separate measurements. This follows the
small, measurable experiment discipline described by
[`autoresearch`](https://github.com/karpathy/autoresearch).

`LLMBackend` is the narrow provider boundary. Stable system rules, configured
project guidance, and ephemeral target context enter the request in distinct
layers. Skills and search results have fixed budgets, and the model sees the
same context that the experiment ledger hashes. This applies the bounded loop
and progressive-disclosure principles documented by
[`hermes-agent`](https://github.com/NousResearch/hermes-agent).

Candidate failure is data, not project state. The loop records the error,
updates the next bounded request, and leaves the source tree unchanged until a
candidate passes the generated-code policy, sandboxed elaboration, source-range
audit, axiom audit, and exact compare-and-swap edit.

## Commands

- `autolean workbench`: choose a model and one proof target interactively.
- `autolean solve`: run the selected proof loop.
- `autolean solve --overnight`: use unlimited cycles and epoch resets.
- `autolean targets`: list prioritized `sorry` targets.
- `autolean inspect`: show the model-bound structural context for one target.
- `autolean doctor`: prove a smoke theorem and run a trusted full build.
- `autolean models`: show profiles and locally observable setup state.
- `autolean environment`: print the installed proof-closure identity.
- `autolean prove`: formalize a plain-English statement and prove that target.
- `autolean verify`: extract, formalize, and prove claims from a paper.
- `autolean build-library`: generate a scoped mathematical library file.
- `autolean improve`: shorten or clarify one existing proof.
- `autolean challenge`: generate and attempt one research challenge file.
- `autolean results`: show persisted experiment records.
- `autolean changes`: show proof changes.
- `autolean init`: create a Lean project.

`ui`, `run`, `scan`, `check`, and `diff` remain accepted as compatibility
aliases. Help and diagnostics use the canonical task names above.

Generated workflows pass an exact target file to the agent. `prove` also uses
an exact declaration name. Their model and backend overrides are carried into
the proof phase, so unrelated project placeholders remain untouched.

Authentication, quota, sandbox, Git, and provider-schema failures produce a
non-zero command exit. Expected candidate proof failures remain experiment
records and participate in the configured retry policy. Authentication and
quota failures stop immediately; transient provider failures use bounded
backoff.

Challenge entries whose Lean statements are semantic sketches are labeled
`scaffold` and cannot enter the proof loop. A successful Lean proof is evidence
for the exact Lean declaration, so a named conjecture requires a source-faithful
formalization first.

## Paper ingestion and semantic boundary

arXiv native HTML is parsed as a DOM, including structured theorem and proof
environments and MathML `alttext`. PDF fallback uses PyMuPDF4LLM 1.28.2 for
layout-aware Markdown, reading order, tables, and selective OCR. The Nix shell
includes Tesseract for scanned documents. The exact HTML bytes or extracted
Markdown sent to claim extraction are recorded by SHA-256 in the generated
Lean source.

Lean proves the generated formal statement. It does not establish that a PDF,
natural-language claim, or model-produced formalization faithfully represents
the author's mathematics. Paper verification therefore has two separate
obligations: source-to-Lean review by a human or a dedicated semantic audit,
followed by kernel-checked proof of the reviewed Lean declaration.

## Data handling

Hosted and subscription profiles transmit the selected declaration, nearby
source context, goal state, failed attempts, configured guidance, learned
skills, and search results to the provider. Choose `muse-glimmer`, `ollama`, or
`openai_compat` when this context must stay on infrastructure you control.

Successful and failed attempts can be stored locally under the Lean project's
`training_data`, `skills`, `logs`, and `results.tsv` paths. These files may
contain source text and model output. Dry-run mode does not create or update
them. OpenAI hosted requests disable Responses API storage; provider-side data
handling outside that control follows the selected service's terms.

## Development and qualification

```bash
nix develop
uv sync --all-extras --all-groups

uv lock --check
uv run --frozen pytest -q
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen mypy autolean
uv build

cd workspace
lake build Cslib
lake build
cd ..

nix flake check path:. --no-build --all-systems
nix flake check path:.
nix build path:.#default
```

### CI and releases

GitHub Actions runs the Python suite on Python 3.11 through 3.14, audits the
complete locked dependency graph, builds the Python distributions twice, runs
the Nix sandbox checks, builds CSLib and the full Lean workspace, and records
the proof-environment identity. The stable `Required` check covers every one
of these obligations.

Each qualified `main` commit produces a private GitHub release. Its
[Hashver](https://miniscruff.github.io/hashver/) identity has the form
`YYYY.MM.DD+<12-character-commit>`. The commit timestamp supplies the date, so
one commit always has one release identity. `release-manifest.json` records
the full commit and SHA-256 of the wheel, source distribution, dependency
SBOM, and Lean proof-environment record. The Python API version remains the
compatibility contract for downstream packages.

Dependabot maintains the uv dependency graph and exact GitHub Actions commit
pins. Nix inputs remain locked by `flake.lock`; run `nix flake update` in a
reviewed change because GitHub does not update Nix inputs for private
repositories.

The host sandbox tests are opt-in because they exercise OS policy and start a
loopback server:

```bash
AUTOLEAN_RUN_SANDBOX_E2E=1 \
  uv run --frozen pytest -q tests/test_lean_sandbox_e2e.py
```

The canonical Linux sandbox check is a Nix derivation that runs the generated
code tests under Bubblewrap. It proves that generated Lean cannot inherit a
parent sentinel, read or write host test data, or reach a loopback service:

```bash
nix build path:.#checks.aarch64-linux.generated-code-sandbox
```

Linux systems also expose
`packages.<system>.generated-code-sandbox-vm`, which runs the same suite in a
minimal NixOS guest. The VM check is useful when KVM is available; the direct
derivation is suitable for portable CI and remote Linux builders.

[`nixos-shell`](https://github.com/Mic92/nixos-shell) is useful for interactive
Linux exploration, including Linux-on-macOS with a suitable builder. Its
convenience defaults mount the user's home and Nix profile and leave the
firewall disabled. It is therefore a developer exploration tool, while the
non-interactive Nix derivation above is the canonical qualification boundary.
AutoLean does not require `nixos-shell` at runtime.

## License

MIT
