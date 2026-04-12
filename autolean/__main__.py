"""CLI entry point — `uv run autolean` or `python -m autolean`."""

from __future__ import annotations

import subprocess
from pathlib import Path

import click
from rich.console import Console

from autolean import __version__

console = Console()


@click.group(invoke_without_command=True)
@click.version_option(version=__version__)
@click.pass_context
def main(ctx: click.Context) -> None:
    """AutoLean — Autonomous Lean 4 proof agent.

    \b
    Overnight sorry elimination, autoformalization, and proof golf.
    Inspired by autoresearch and autokernel.

    \b
    Quick start:
      uv run autolean run              # start the agent loop
      uv run autolean scan             # scan for sorry targets
      uv run autolean models           # list available LLM profiles
      uv run autolean check            # verify connectivity
      uv run autolean verify-paper <pdf-or-arxiv-url>

    \b
    Model selection:
      uv run autolean run --model deepseek-prover
      uv run autolean run --model gemma4-31b
      uv run autolean run --model bfs-prover
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# ---------------------------------------------------------------------------
# run — the main agent loop
# ---------------------------------------------------------------------------


@main.command()
@click.option("--program", "-p", type=click.Path(exists=True, path_type=Path),
              default="program.md", help="Path to program.md.")
@click.option("--dry-run", "-n", is_flag=True, help="Query LLM but don't modify files.")
@click.option("--verbose", "-v", is_flag=True, help="Show detailed output.")
@click.option("--model", "-m", type=str, default=None,
              help="Model profile name or raw model (e.g., deepseek-prover, gemma4:31b).")
@click.option("--max-cycles", type=int, default=None,
              help="Max experiment cycles (0 = unlimited).")
@click.option("--resume", "-r", is_flag=True, help="Resume from previous session.")
@click.option("--backend", "-b", type=click.Choice(["ollama", "openai_compat"]),
              default=None, help="LLM backend override.")
@click.option("--overnight", is_flag=True,
              help="Run all night (unlimited cycles, robust error recovery).")
def run(
    program: Path, dry_run: bool, verbose: bool,
    model: str | None, max_cycles: int | None,
    resume: bool, backend: str | None, overnight: bool,
) -> None:
    """Start the autonomous proof agent loop.

    \b
    The edit-build-evaluate loop:
      1. Pick highest-priority sorry target
      2. Extract goal state (hole-punch method)
      3. Query LLM for a proof
      4. Apply proof to .lean file
      5. Run `lake build` to verify
      6. Keep (git commit) or revert
      7. Log to results.tsv, repeat

    \b
    Overnight mode (--overnight):
      Runs with unlimited cycles, auto-resume if results.tsv exists,
      and robust error recovery. Designed to run unattended for 8+ hours.

    Press Ctrl+C once to stop gracefully. Twice to force quit.
    """
    from autolean.agent import AutoLeanAgent
    from autolean.llm_client import LLMConfig, create_llm_client
    from autolean.models import resolve_profile

    agent = AutoLeanAgent(
        program_path=program, dry_run=dry_run,
        verbose=verbose, resume=resume,
    )

    # Model override: check for profile name first, then raw model string
    if model:
        profile = resolve_profile(model)
        if profile:
            console.print(f"[cyan]Using profile:[/] {profile.name} — {profile.description}")
            cfg = LLMConfig(
                model=profile.model,
                base_url=profile.base_url,
                temperature=profile.temperature,
                num_predict=profile.num_predict,
                backend=profile.backend,
            )
            agent.llm = create_llm_client(cfg)
            agent.config.model = profile.model
            agent.config.temperature = profile.temperature
        else:
            agent.config.model = model
            agent.llm.config.model = model

    if backend:
        agent.llm.config.backend = backend
        from autolean.llm_client import create_llm_client as _create
        agent.llm = _create(agent.llm.config)

    if overnight:
        # Overnight mode: unlimited cycles, massive retry budget, auto-resume.
        # Resets and starts new epochs when all retries exhausted.
        agent.config.max_cycles = 0
        agent.config.max_retries_per_sorry = 100
        agent.resume = True
        console.print("[bold magenta]OVERNIGHT MODE[/] — unlimited cycles, 100 retries/sorry, auto-resume, Ctrl+C to stop")
    elif max_cycles is not None:
        agent.config.max_cycles = max_cycles

    agent.run()


# ---------------------------------------------------------------------------
# scan — find sorry targets
# ---------------------------------------------------------------------------


@main.command()
@click.option("--project", "-d", type=click.Path(exists=True, path_type=Path),
              default="workspace", help="Path to Lean project root.")
@click.option("--json", "as_json", is_flag=True, help="Output as JSON.")
def scan(project: Path, as_json: bool) -> None:
    """Scan a Lean project for sorry targets (ordered by difficulty)."""
    from autolean.scanner import _difficulty_score, prioritize_targets, scan_project

    project = project.resolve()
    targets = scan_project(project)
    targets = prioritize_targets(targets)

    if as_json:
        import json
        data = [
            {"file": str(t.file.relative_to(project)), "line": t.line,
             "col": t.col, "decl_name": t.decl_name, "id": t.id,
             "tactic_mode": t.tactic_mode, "difficulty": _difficulty_score(t)}
            for t in targets
        ]
        click.echo(json.dumps(data, indent=2))
    else:
        _DIFF_LABELS = {0: "trivial", 1: "basic", 2: "easy", 5: "medium",
                        6: "hard", 7: "hard", 8: "advanced", 9: "research", 10: "research"}
        _STYLES = {"trivial": "green", "basic": "green", "easy": "cyan",
                   "medium": "yellow", "hard": "red", "advanced": "red",
                   "research": "magenta"}

        console.print(f"[bold]Found {len(targets)} sorry target(s) in {project}[/]\n")
        current_diff = -1
        for t in targets:
            diff = _difficulty_score(t)
            if diff != current_diff:
                label = _DIFF_LABELS.get(diff, f"level-{diff}")
                style = _STYLES.get(label, "white")
                console.print(f"\n  [{style}]--- {label.upper()} ---[/{style}]")
                current_diff = diff
            try:
                rel = t.file.relative_to(project)
            except ValueError:
                rel = t.file
            mode = "tactic" if t.tactic_mode else "term"
            console.print(f"    {rel}:{t.line} — [cyan]{t.decl_name}[/] [{mode}]")

        if not targets:
            console.print("  [green]No sorries found![/]")
        console.print()


# ---------------------------------------------------------------------------
# models — list available LLM profiles
# ---------------------------------------------------------------------------


@main.command()
def models() -> None:
    """List available model profiles and check installation status."""
    from autolean.models import print_models_table
    print_models_table()


# ---------------------------------------------------------------------------
# check — verify connectivity
# ---------------------------------------------------------------------------


@main.command()
@click.option("--program", "-p", type=click.Path(exists=True, path_type=Path),
              default="program.md", help="Path to program.md.")
def check(program: Path) -> None:
    """Verify Ollama + Lean connectivity."""
    from autolean.agent import parse_program
    from autolean.lean_interface import LeanProject
    from autolean.llm_client import LLMConfig, OllamaClient

    cfg = parse_program(program)

    console.print("[bold]Checking Ollama...[/]")
    llm = OllamaClient(config=LLMConfig(model=cfg.model))
    if llm.ping():
        console.print(f"  [green]OK[/] Connected to {llm.config.base_url}")
        console.print(f"  [green]OK[/] Model: {cfg.model}")
        try:
            resp = llm.generate(
                system="You are a Lean 4 expert.",
                user="What tactic closes the goal True? Reply with ONE word.",
            )
            console.print(f"  [green]OK[/] Response: {resp.text[:50]}")
            console.print(f"  [green]OK[/] {resp.tokens_per_second:.1f} tok/s")
        except Exception as e:
            console.print(f"  [red]FAIL[/] Generation: {e}")
    else:
        console.print(f"  [red]FAIL[/] Cannot reach Ollama")

    console.print("\n[bold]Checking Lean 4...[/]")
    lean_root = program.parent / cfg.lean_project_path
    try:
        proj = LeanProject(lean_root)
        console.print(f"  [green]OK[/] Project: {proj.root}")
        files = proj.lean_files()
        console.print(f"  [green]OK[/] Lean files: {len(files)}")
        console.print("  Building...")
        build = proj.build(timeout=120)
        if build.success:
            console.print(f"  [green]OK[/] Build succeeded ({build.duration_seconds:.1f}s)")
        else:
            console.print(f"  [yellow]WARN[/] Build errors: {len(build.errors)}")
    except FileNotFoundError as e:
        console.print(f"  [red]FAIL[/] {e}")

    llm.close()


# ---------------------------------------------------------------------------
# results — show experiment log
# ---------------------------------------------------------------------------


@main.command()
@click.option("--file", "-f", type=click.Path(path_type=Path),
              default="workspace/results.tsv", help="Path to results.tsv.")
@click.option("--tail", "-n", type=int, default=20, help="Show last N records.")
def results(file: Path, tail: int) -> None:
    """Display experiment results from results.tsv."""
    import csv
    from rich.table import Table

    if not file.exists():
        console.print(f"[yellow]No results file at {file}[/]")
        return

    with open(file) as f:
        reader = csv.DictReader(f, delimiter="\t")
        rows = list(reader)

    if not rows:
        console.print("[yellow]No experiments recorded yet.[/]")
        return

    # Summary
    total = len(rows)
    successes = sum(1 for r in rows if r.get("outcome") == "success")
    console.print(
        f"[bold]Summary:[/] {successes}/{total} attempts succeeded "
        f"({successes / total * 100:.0f}%)\n" if total else ""
    )

    table = Table(title=f"Last {min(tail, len(rows))} experiments")
    table.add_column("#", style="dim")
    table.add_column("Target")
    table.add_column("Outcome")
    table.add_column("Attempt")
    table.add_column("Time")
    table.add_column("Tokens")
    table.add_column("Error", max_width=40)

    for row in rows[-tail:]:
        outcome = row.get("outcome", "?")
        style = "green" if outcome == "success" else "red"
        table.add_row(
            row.get("cycle", ""),
            row.get("decl_name", ""),
            f"[{style}]{outcome}[/{style}]",
            row.get("attempt", ""),
            f"{row.get('duration_s', '?')}s",
            row.get("llm_tokens", ""),
            (row.get("error_category", "") or "")[:40],
        )
    console.print(table)


# ---------------------------------------------------------------------------
# verify-paper — extract and formalize claims from a PDF or arXiv link
# ---------------------------------------------------------------------------


@main.command("verify-paper")
@click.argument("source")  # PDF path or arXiv URL/ID
@click.option("--pages", type=str, default=None, help="Page range (e.g., '1-5').")
@click.option("--extract-only", is_flag=True, help="Extract claims without formalizing.")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None,
              help="Output .lean file path.")
@click.option("--model", "-m", type=str, default=None, help="Model for extraction/formalization.")
@click.option("--program", "-p", type=click.Path(exists=True, path_type=Path),
              default="program.md", help="Path to program.md (for LLM config).")
def verify_paper(
    source: str, pages: str | None, extract_only: bool,
    output: Path | None, model: str | None, program: Path,
) -> None:
    """Extract theorems from a paper and formalize in Lean 4.

    \b
    SOURCE can be:
      - A local PDF file path
      - An arXiv URL (https://arxiv.org/abs/2404.12534)
      - An arXiv ID (2404.12534)

    \b
    Examples:
      uv run autolean verify-paper paper.pdf
      uv run autolean verify-paper https://arxiv.org/abs/2404.12534
      uv run autolean verify-paper 2404.12534 --pages 3-7
      uv run autolean verify-paper paper.pdf --extract-only
    """
    from autolean.agent import parse_program
    from autolean.llm_client import LLMConfig, create_llm_client
    from autolean.models import resolve_profile
    from autolean.paper import (
        Claim, create_verification_file, extract_claims,
        fetch_arxiv, formalize_claim, read_pdf,
    )

    # Resolve PDF source
    source_path = Path(source)
    if source_path.exists() and source_path.suffix == ".pdf":
        pdf_path = source_path
        paper_title = source_path.stem
    elif "arxiv" in source or source.replace(".", "").replace("/", "").isdigit():
        console.print(f"[bold]Fetching from arXiv...[/]")
        pdf_path = fetch_arxiv(source)
        paper_title = f"arXiv:{source.split('/')[-1].removesuffix('.pdf')}"
    else:
        console.print(f"[red]Cannot resolve source: {source}[/]")
        console.print("  Provide a PDF path, arXiv URL, or arXiv ID.")
        return

    # Read PDF
    console.print(f"[bold]Reading {pdf_path.name}...[/]")
    text = read_pdf(pdf_path, pages=pages)
    console.print(f"  Extracted {len(text):,} characters from {pdf_path.name}")

    # Setup LLM
    cfg = parse_program(program)
    if model:
        profile = resolve_profile(model)
        if profile:
            llm_cfg = LLMConfig(model=profile.model, base_url=profile.base_url,
                                temperature=profile.temperature, num_predict=profile.num_predict,
                                backend=profile.backend)
        else:
            llm_cfg = LLMConfig(model=model)
    else:
        # Paper extraction needs more tokens than proof generation (long prompts
        # cause thinking models to exhaust budget before producing content)
        llm_cfg = LLMConfig(model=cfg.model, temperature=cfg.temperature, num_predict=32768)

    llm = create_llm_client(llm_cfg)
    if not llm.ping():
        console.print("[red]Cannot connect to LLM. Is Ollama running?[/]")
        return

    # Extract claims
    console.print(f"\n[bold]Extracting claims with {llm_cfg.model}...[/]")
    claims = extract_claims(text, llm.generate)
    console.print(f"  Found [cyan]{len(claims)}[/] theorem/lemma statements:\n")
    for i, c in enumerate(claims, 1):
        console.print(f"  {i}. [bold]{c.label}[/]: {c.statement[:100]}...")

    if extract_only:
        llm.close()
        return

    if not claims:
        console.print("[yellow]No claims extracted. Try different pages or a clearer PDF.[/]")
        llm.close()
        return

    # Formalize each claim
    console.print(f"\n[bold]Formalizing {len(claims)} claims...[/]")
    for i, c in enumerate(claims):
        with console.status(f"[dim]Formalizing {c.label}..."):
            formalize_claim(c, llm.generate)
        if c.lean_code:
            console.print(f"  [green]OK[/] {c.label} -> {c.lean_name}")
        else:
            console.print(f"  [yellow]SKIP[/] {c.label} (could not formalize)")

    # Write output file
    # Sanitize filename: remove characters that break Lake module paths
    import re as _re
    safe_title = _re.sub(r"[^a-zA-Z0-9_]", "_", paper_title.replace(" ", "_"))
    output = output or Path(f"workspace/AutoLean/Paper_{safe_title}.lean")
    output.parent.mkdir(parents=True, exist_ok=True)
    create_verification_file(claims, output, paper_title=paper_title)
    console.print(f"\n[bold green]Wrote {output}[/]")
    console.print(f"  {len([c for c in claims if c.lean_code])} theorems ready for proving")
    console.print(f"\n  Next: [cyan]uv run autolean run[/] to attempt proofs")

    llm.close()


# ---------------------------------------------------------------------------
# init — scaffold a new AutoLean project
# ---------------------------------------------------------------------------


@main.command()
@click.argument("path", type=click.Path(path_type=Path))
@click.option("--mathlib", is_flag=True, help="Add mathlib4 dependency.")
@click.option("--toolchain", default="leanprover/lean4:v4.29.0", help="Lean toolchain.")
def init(path: Path, mathlib: bool, toolchain: str) -> None:
    """Initialize a new Lean project for AutoLean.

    \b
    Creates:
      <path>/lakefile.lean
      <path>/lean-toolchain
      <path>/MyProject.lean (with example sorry targets)
      program.md (in current directory)
    """
    path = path.resolve()
    path.mkdir(parents=True, exist_ok=True)

    # lean-toolchain
    (path / "lean-toolchain").write_text(f"{toolchain}\n")

    # lakefile.lean
    lakefile = "import Lake\nopen Lake DSL\n\n"
    lakefile += f"package {path.name.lower()} where\n"
    lakefile += "  leanOptions := #[\n    ⟨`autoImplicit, false⟩\n  ]\n\n"
    if mathlib:
        lakefile += 'require mathlib from git\n  "https://github.com/leanprover-community/mathlib4" @ "v4.29.0"\n\n'
    lakefile += f"@[default_target]\nlean_lib {path.name} where\n  srcDir := \".\"\n"
    (path / "lakefile.lean").write_text(lakefile)

    # Example .lean file
    example = f"/-! # {path.name} — AutoLean Project\n\nEdit this file and add theorems with `sorry`.\n-/\n\n"
    example += "-- Example: replace sorry with a proof\n"
    example += "theorem example_1 : 1 + 1 = 2 := by\n  sorry\n\n"
    example += "theorem example_2 (P : Prop) (h : P) : P := by\n  sorry\n"
    (path / f"{path.name}.lean").write_text(example)

    # program.md in CWD
    if not Path("program.md").exists():
        program = f"# AutoLean Program\n\n## Mode\n\nsorry-elimination\n\n"
        program += f"## Lean Project Path\n\n{path}\n\n"
        program += "## LLM Configuration\n\nmodel: gemma4:26b\ntemperature: 0.4\n"
        program += "max_retries_per_sorry: 5\ncycle_timeout_seconds: 120\nmax_cycles: 0\n"
        Path("program.md").write_text(program)

    console.print(f"[green]Initialized AutoLean project at {path}[/]")
    console.print(f"  lakefile.lean ({'with mathlib' if mathlib else 'standalone'})")
    console.print(f"  lean-toolchain: {toolchain}")
    console.print(f"  {path.name}.lean (2 example sorry targets)")
    if not Path("program.md").exists():
        console.print(f"  program.md (created)")
    console.print(f"\n  Next:")
    console.print(f"    cd {path} && lake update")
    console.print(f"    uv run autolean scan -d {path}")
    console.print(f"    uv run autolean run")


# ---------------------------------------------------------------------------
# diff — show what the agent has changed
# ---------------------------------------------------------------------------


@main.command()
@click.option("--project", "-d", type=click.Path(exists=True, path_type=Path),
              default="workspace", help="Path to Lean project root.")
def diff(project: Path) -> None:
    """Show what the agent has changed (git diff of .lean files)."""
    project = project.resolve()

    # Git diff
    result = subprocess.run(
        ["git", "diff", "--stat", "HEAD~10", "--", "*.lean"],
        cwd=project, capture_output=True, text=True,
    )
    if result.stdout.strip():
        console.print("[bold]Changes to .lean files:[/]\n")
        console.print(result.stdout)
    else:
        console.print("[dim]No changes to .lean files.[/]")

    # Count proved theorems from git log
    log_result = subprocess.run(
        ["git", "log", "--oneline", "--grep=autolean: prove", "--format=%s"],
        cwd=project, capture_output=True, text=True,
    )
    proved = [l for l in log_result.stdout.strip().split("\n") if l]
    if proved:
        console.print(f"\n[bold green]{len(proved)} theorems proved:[/]")
        for p in proved:
            name = p.replace("autolean: prove ", "").split(" (")[0]
            console.print(f"  [green]OK[/] {name}")


# ---------------------------------------------------------------------------
# export-training — export collected training data
# ---------------------------------------------------------------------------


@main.command("export-training")
@click.option("--project", "-d", type=click.Path(exists=True, path_type=Path),
              default="workspace", help="Path to Lean project root.")
def export_training(project: Path) -> None:
    """Export training data from previous runs (SFT, ShareGPT, DPO).

    \b
    Uses results.tsv + cached proof data to generate:
      - SFT JSONL (instruction tuning with successful proofs)
      - ShareGPT JSONL (Hermes/Axolotl compatible)
      - DPO JSONL (preference pairs: good proof vs bad proof)

    \b
    Use the exported data to fine-tune Gemma or other models:
      pip install unsloth
      # See workspace/training_data/finetune_config.yaml
    """
    from autolean.collector import TrainingDataCollector

    project = project.resolve()
    collector = TrainingDataCollector(output_dir=project / "training_data")

    # Read from existing training data files
    td = project / "training_data"
    if not td.exists() or not any(td.glob("*.jsonl")):
        console.print("[yellow]No training data found. Run the agent first:[/]")
        console.print("  uv run autolean run --max-cycles 20")
        return

    # Show existing files
    console.print("[bold]Training data files:[/]\n")
    for f in sorted(td.glob("*.jsonl")):
        lines = sum(1 for _ in open(f))
        size = f.stat().st_size / 1024
        console.print(f"  {f.name} ({lines} examples, {size:.1f} KB)")

    # Show stats
    console.print(f"\n[bold]Usage:[/]")
    console.print("  Fine-tune with Unsloth:")
    console.print(f"    unsloth train --data {td}/sft_*.jsonl --model gemma4:26b")
    console.print("  Fine-tune with Axolotl:")
    console.print(f"    axolotl train {td}/finetune_config.yaml")
    console.print("  DPO training:")
    console.print(f"    Use {td}/dpo_*.jsonl with TRL DPOTrainer")


# ---------------------------------------------------------------------------
# finetune-config — generate training configuration
# ---------------------------------------------------------------------------


@main.command("finetune-config")
@click.option("--project", "-d", type=click.Path(exists=True, path_type=Path),
              default="workspace", help="Path to Lean project root.")
@click.option("--model", "-m", default="google/gemma-4-E2B",
              help="Base model for fine-tuning.")
@click.option("--framework", type=click.Choice(["unsloth", "axolotl", "trl"]),
              default="axolotl", help="Training framework.")
def finetune_config(project: Path, model: str, framework: str) -> None:
    """Generate a fine-tuning config for Lean 4 proof models.

    \b
    Creates a ready-to-use config file for the specified framework.
    Supports: Axolotl, Unsloth, HuggingFace TRL.
    """
    import yaml

    project = project.resolve()
    td = project / "training_data"
    td.mkdir(parents=True, exist_ok=True)

    sft_files = sorted(td.glob("sft_*.jsonl"))
    dpo_files = sorted(td.glob("dpo_*.jsonl"))

    if framework == "axolotl":
        config = {
            "base_model": model,
            "model_type": "AutoModelForCausalLM",
            "tokenizer_type": "AutoTokenizer",
            "load_in_4bit": True,
            "adapter": "qlora",
            "lora_r": 64,
            "lora_alpha": 64,
            "lora_dropout": 0.0,
            "lora_target_modules": [
                "q_proj", "k_proj", "v_proj", "o_proj",
                "gate_proj", "up_proj", "down_proj",
            ],
            "datasets": [
                {
                    "path": str(sft_files[-1]) if sft_files else "training_data/sft.jsonl",
                    "type": "sharegpt",
                    "conversation": "chatml",
                },
            ],
            "sequence_len": 8192,
            "micro_batch_size": 1,
            "gradient_accumulation_steps": 8,
            "num_epochs": 3,
            "learning_rate": 2e-4,
            "lr_scheduler": "cosine",
            "warmup_ratio": 0.1,
            "optimizer": "adamw_8bit",
            "bf16": True,
            "gradient_checkpointing": True,
            "output_dir": str(td / "output"),
            "logging_steps": 10,
            "save_strategy": "epoch",
            "wandb_project": "autolean-finetune",
        }
        config_path = td / "axolotl_config.yaml"
        config_path.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))
        console.print(f"[green]Generated Axolotl config:[/] {config_path}")
        console.print(f"\n  Run: accelerate launch -m axolotl.cli.train {config_path}")

    elif framework == "unsloth":
        config = {
            "model_name": model,
            "max_seq_length": 8192,
            "load_in_4bit": True,
            "lora_r": 64,
            "lora_alpha": 64,
            "dataset": str(sft_files[-1]) if sft_files else "training_data/sft.jsonl",
            "dataset_type": "messages",
            "num_epochs": 3,
            "learning_rate": 2e-4,
            "batch_size": 1,
            "gradient_accumulation": 8,
            "output_dir": str(td / "output"),
        }
        config_path = td / "unsloth_config.yaml"
        config_path.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))
        console.print(f"[green]Generated Unsloth config:[/] {config_path}")
        console.print(f"\n  pip install unsloth")
        console.print(f"  python -m unsloth.train --config {config_path}")

    elif framework == "trl":
        config = {
            "model_name": model,
            "dataset_path": str(dpo_files[-1]) if dpo_files else "training_data/dpo.jsonl",
            "lora_r": 64,
            "lora_alpha": 64,
            "beta": 0.1,
            "num_epochs": 1,
            "learning_rate": 5e-6,
            "batch_size": 1,
            "gradient_accumulation_steps": 8,
            "output_dir": str(td / "dpo_output"),
        }
        config_path = td / "trl_dpo_config.yaml"
        config_path.write_text(yaml.dump(config, default_flow_style=False, sort_keys=False))
        console.print(f"[green]Generated TRL DPO config:[/] {config_path}")
        console.print(f"\n  pip install trl")
        console.print(f"  Use DPOTrainer with config from {config_path}")

    # Summary
    console.print(f"\n[bold]Self-improving loop:[/]")
    console.print(f"  1. Run agent:      uv run autolean run --overnight")
    console.print(f"  2. Export data:     uv run autolean export-training")
    console.print(f"  3. Fine-tune:      {framework} train ...")
    console.print(f"  4. Import model:   ollama create autolean-v1 -f Modelfile")
    console.print(f"  5. Run again:      uv run autolean run --model autolean-v1")


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
