# AutoLean

Autonomous Lean 4 proof agent. Point it at a project, let it run overnight,
wake up to machine-verified proofs.

Inspired by [autoresearch](https://github.com/karpathy/autoresearch) and
[autokernel](https://github.com/RightNow-AI/autokernel).

```
edit .lean file -> lake build -> evaluate -> keep/revert -> log -> repeat
```

## Quick Start

```bash
# 1. Enter the Nix dev shell (or install lean, ollama, uv manually)
nix develop

# 2. Install Python dependencies
uv sync

# 3. Start Ollama and verify
ollama serve &
uv run autolean check

# 4. Run the agent
uv run autolean run
```

## What It Does

AutoLean scans your Lean 4 project for `sorry` placeholders, queries a local
LLM for proofs, applies them, verifies with `lake build`, and commits
successes to git. Each cycle takes 5-30 seconds.

```
Cycle 1 | trivial_rfl | attempt 1/5
  Proof (1 lines): rfl
  PROVED! trivial_rfl (0.9s build)
  [████░░░░░░░] 6/36 (17%) | 30 left | 136.9/hr
```

## Models

AutoLean ships with profiles for Lean 4 specialized models. List them:

```bash
uv run autolean models
```

| Profile | Model | Size | Best For |
|---------|-------|------|----------|
| `gemma4` | Gemma 4 26B | 17 GB | General-purpose (default) |
| `gemma4-31b` | Gemma 4 31B | 19 GB | Harder reasoning |
| `deepseek-prover` | DeepSeek Prover V2 7B | 14 GB | Lean 4 proofs (88.9% miniF2F) |
| `bfs-prover` | BFS-Prover V2 7B | 8 GB | Single-tactic prediction |
| `ntpctx` | LeanDojo NTP-ctx 8B | 8.5 GB | Tactic prediction with retrieval |
| `leanstral` | Mistral Leanstral 119B | 68 GB | SOTA Lean 4 (requires vLLM) |

Install a model and use it:

```bash
ollama pull yinyaowenhua1314/deepseek-prover-v2-7b
uv run autolean run --model deepseek-prover
```

Or use any OpenAI-compatible server (vLLM, llama.cpp, LM Studio):

```bash
vllm serve deepseek-ai/DeepSeek-Prover-V2-7B --port 8000
uv run autolean run --backend openai_compat --model deepseek-prover
```

## Use Cases

### Sorry Elimination (core)

The default mode. Scans for `sorry`, fills proofs, verifies, commits.

```bash
uv run autolean scan              # see what needs proving
uv run autolean run               # start the agent
uv run autolean run --resume      # continue previous session
uv run autolean results           # view experiment log
uv run autolean diff              # see what was proved
```

### Paper Verification

Extract theorems from a math paper and try to formalize them:

```bash
# From a local PDF
uv run autolean verify-paper paper.pdf

# From arXiv (downloads automatically)
uv run autolean verify-paper https://arxiv.org/abs/2404.12534
uv run autolean verify-paper 2404.12534 --pages 3-7

# Just extract claims without formalizing
uv run autolean verify-paper paper.pdf --extract-only
```

This creates a `.lean` file with sorry'd theorem statements, then you run
the agent to attempt proofs.

### Distributed Systems Verification

The workspace includes a Veil-inspired Two-Phase Commit formalization:

```bash
uv run autolean scan   # shows 6 protocol invariant targets
uv run autolean run    # agent attempts safety proofs
```

### Open Problem Research

The Gromov workspace formalizes sub-results of the Polynomial Growth Gap
Conjecture. The agent attempts ALL targets, including the open problem:

```bash
uv run autolean scan   # shows RESEARCH targets
uv run autolean run --model deepseek-prover --max-cycles 50
```

### New Project Setup

```bash
uv run autolean init my_project --mathlib
cd my_project && lake update
uv run autolean run
```

## Overnight / Long-Running Mode

AutoLean is designed to run unattended for hours:

```bash
# Simple: just let it run (Ctrl+C to stop with full report)
uv run autolean run --overnight --verbose

# In tmux (survives terminal close):
tmux new-session -d -s autolean \
  'cd /path/to/autolean && caffeinate -s uv run autolean run --overnight --verbose 2>&1 | tee workspace/overnight.log'

# Check on it:
tmux attach -t autolean         # live view (Ctrl+B, D to detach)
tail -f workspace/overnight.log  # log follow
uv run autolean results          # experiment table
uv run autolean diff             # what was proved
```

`--overnight` enables:
- **Unlimited cycles** — runs until interrupted
- **100 retries per sorry** — extensive exploration per target
- **Epoch resets** — when all retries exhausted, resets counters with higher temperature and tries again
- **Auto-resume** — picks up where it left off if restarted

When stopped (Ctrl+C or max-cycles), you get a full report:
- Summary table with success rate, coverage, token efficiency
- List of proved theorems with timings
- List of remaining targets
- Suggested next steps

## Configuration

Edit `program.md` to control the agent. All fields have sane defaults.

```markdown
## Mode
sorry-elimination            # sorry-elimination | autoformalize | proof-golf

## Lean Project Path
workspace                    # relative to program.md location

## Strategy Hints
- Try simp, omega, ring first
- For inductive types, try cases or induction
- For algebraic goals, try ring or field_simp then ring

## LLM Configuration
model: gemma4:26b            # model name or profile
temperature: 0.4             # base temperature (escalates on retries)
max_retries_per_sorry: 5     # attempts per target before skipping
cycle_timeout_seconds: 120   # max build time per cycle
max_cycles: 0                # 0 = unlimited
```

### Key defaults

| Setting | Default | What it does |
|---------|---------|-------------|
| `model` | `gemma4:26b` | LLM to use (see `autolean models` for profiles) |
| `temperature` | `0.4` | Starting temperature; +0.1 per retry, capped at 1.0 |
| `max_retries_per_sorry` | `5` | Attempts per sorry before moving to next target |
| `cycle_timeout_seconds` | `120` | Max time for `lake build` per cycle |
| `max_cycles` | `0` | Total cycles; 0 = unlimited (use `--max-cycles` to override) |
| `num_predict` | `-1` | Token limit; -1 = unlimited (model stops naturally) |
| `timeout` | `1800` | HTTP timeout in seconds (30 min for thinking models) |

Override any setting via CLI flags:

```bash
uv run autolean run --model gemma4-31b --max-cycles 50
uv run autolean run --overnight --model deepseek-prover
```

## CLI Reference

| Command | Description |
|---------|-------------|
| `autolean run` | Start the autonomous proof loop |
| `autolean run --overnight` | Run all night (unlimited, epoch resets) |
| `autolean scan` | List sorry targets (grouped by difficulty) |
| `autolean check` | Verify Ollama + Lean connectivity |
| `autolean models` | List model profiles and installation status |
| `autolean results` | Display experiment log with summary stats |
| `autolean diff` | Show what the agent proved (git diff) |
| `autolean verify-paper` | Extract and formalize paper claims |
| `autolean init` | Scaffold a new AutoLean project |

### `autolean run` Options

| Flag | Description |
|------|-------------|
| `--model`, `-m` | Model profile name or raw model string |
| `--max-cycles` | Limit experiment cycles (0 = unlimited) |
| `--overnight` | Unlimited cycles, 100 retries, epoch resets, auto-resume |
| `--resume`, `-r` | Continue from previous session's results.tsv |
| `--dry-run`, `-n` | Query LLM but don't modify files |
| `--verbose`, `-v` | Show generated proofs, goal states, build errors |
| `--backend`, `-b` | `ollama` or `openai_compat` |

## Architecture

```
program.md (goals, model, constraints)
       |
       v
 ┌─────────────────────┐
 │   Agent Loop (LLM)  │
 │                      │
 │ 1. Scan for sorry    │
 │ 2. Extract goal (?_) │
 │ 3. Query LLM         │  <-- Gemma4 / DeepSeek / BFS-Prover / Leanstral
 │ 4. Apply proof        │
 │ 5. lake build         │  <-- Lean 4 kernel verification
 │ 6. Keep or revert     │
 │ 7. Log to results.tsv │
 └─────────────────────┘
       |
  ┌────┼─────┐
  v    v     v
 git  TSV  progress
```

## Metrics

The agent tracks:
- **Sorry coverage**: proved / total (the goal metric)
- **First-attempt success rate**: measures prompt quality
- **Tokens per proof**: efficiency
- **Error category breakdown**: guides tuning
- **Proofs per hour**: throughput

## Project Structure

```
autolean/
├── program.md              # Agent configuration (you edit this)
├── flake.nix               # Nix dev shell
├── pyproject.toml           # Python project (uv-managed)
├── autolean/
│   ├── agent.py            # Core autonomous loop
│   ├── models.py           # Model profiles (Gemma, DeepSeek, BFS, etc.)
│   ├── paper.py            # PDF paper verification
│   ├── llm_client.py       # LLM backends (Ollama + OpenAI-compat)
│   ├── lean_interface.py   # Lean 4 build + goal extraction
│   ├── scanner.py          # Sorry scanner + difficulty prioritization
│   ├── tracker.py          # Git + TSV experiment tracking
│   ├── error_classifier.py # Error categorization for smarter retries
│   ├── prompts.py          # LLM prompt templates
│   └── __main__.py         # CLI
├── tests/                  # 117 tests
└── workspace/              # Example Lean project (36 sorry targets)
    ├── AutoLean/Trivial.lean      # Single-tactic proofs
    ├── AutoLean/Easy.lean         # 2-3 step proofs
    ├── AutoLean/Medium.lean       # Induction, case analysis
    ├── AutoLean/Gromov.lean       # Growth theory + Gap Conjecture
    └── AutoLean/Veil/             # Distributed systems (2PC)
```

## Prerequisites

- **Nix** (recommended): `nix develop` gives you everything
- **Lean 4**: via [elan](https://github.com/leanprover/elan)
- **Ollama**: [ollama.com](https://ollama.com) with a model pulled
- **uv**: `pip install uv` or via Nix
- **pymupdf** (optional, for paper verification): `uv pip install pymupdf`

## License

MIT
