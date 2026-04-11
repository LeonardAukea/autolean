# AutoLean

Autonomous Lean 4 proof agent — overnight sorry elimination, autoformalization,
and proof golf.

Inspired by [autoresearch](https://github.com/karpathy/autoresearch) and
[autokernel](https://github.com/RightNow-AI/autokernel).

## Concept

Point AutoLean at a Lean 4 project, edit `program.md` to describe your goals,
and let it run overnight. The agent enters a tight loop:

```
edit .lean file → lake build → evaluate → keep (git commit) / revert → log → repeat
```

Each cycle takes ~30-120 seconds, yielding 30-120+ experiments overnight.

## Prerequisites

- **Nix** (recommended): `nix develop` activates the full environment
- **Lean 4**: via elan (`elan default stable`)
- **Ollama**: with a model pulled (`ollama pull gemma4:26b`)
- **uv**: Python package manager (`pip install uv` or via Nix)

## Quick Start

```bash
# Enter the Nix dev shell (provides lean, ollama, uv, git)
nix develop

# Install Python deps
uv sync

# Start Ollama (if not already running)
ollama serve &

# Check connectivity
uv run autolean check

# Scan for sorry targets
uv run autolean scan

# Run the agent (Ctrl+C to stop gracefully)
uv run autolean run

# Run with options
uv run autolean run --verbose --model gemma4:31b --max-cycles 50
uv run autolean run --dry-run  # preview without modifying files
```

## How It Works

### The Loop

1. **Scan** — Find all `sorry` placeholders in `.lean` files
2. **Prioritize** — Files with fewer sorries first (low-hanging fruit)
3. **Query** — Send goal state + context to Gemma 4 via Ollama
4. **Apply** — Replace `sorry` with the generated proof
5. **Build** — Run `lake build` to verify
6. **Keep/Revert** — Git commit on success, revert on failure
7. **Log** — Append to `results.tsv`
8. **Repeat** — Until interrupted or budget exhausted

### Steering via `program.md`

Edit `program.md` to control the agent — just like autoresearch:

```markdown
## Mode
sorry-elimination

## LLM Configuration
model: gemma4:26b
temperature: 0.4
max_retries_per_sorry: 5

## Strategy Hints
- Try `simp`, `omega`, `ring` first
- For inductive types, try `cases` or `induction`
```

### Modes

| Mode | What It Does |
|------|-------------|
| `sorry-elimination` | Fill in `sorry` placeholders with valid proofs |
| `autoformalize` | Translate informal math to Lean 4 (planned) |
| `proof-golf` | Shorten existing proofs (planned) |

## Project Structure

```
autolean/
├── program.md           # Agent instructions (you edit this)
├── flake.nix            # Nix dev environment
├── pyproject.toml       # Python project (uv-managed)
├── autolean/
│   ├── agent.py         # The autonomous loop
│   ├── lean_interface.py # Lean 4 build + file ops
│   ├── llm_client.py    # Ollama/Gemma client
│   ├── scanner.py       # Sorry target scanner
│   ├── tracker.py       # Git + TSV experiment tracking
│   ├── prompts.py       # LLM system prompts
│   └── __main__.py      # CLI entry point
└── workspace/           # Example Lean project with sorry targets
    ├── lakefile.lean
    ├── lean-toolchain
    └── AutoLean/
        ├── Trivial.lean # Single-tactic targets
        ├── Easy.lean    # 2-3 step proofs
        └── Medium.lean  # Induction, case analysis
```

## Results

Experiments are logged to `workspace/results.tsv`:

```
cycle  target_id        outcome    attempt  duration_s  llm_tokens
1      Trivial.lean:9   success    1        12.3        45
2      Trivial.lean:13  success    1        8.7         23
3      Easy.lean:10     fail_build 1        34.1        112
4      Easy.lean:10     success    2        28.5        89
```

Git history shows each successful proof as a commit on the `autolean/` branch.

## License

MIT
