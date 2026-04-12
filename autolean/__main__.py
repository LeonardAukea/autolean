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
      autolean prove "1 + 1 = 2"       # prove a theorem
      autolean challenge collatz        # attempt an open problem
      autolean verify <arxiv-url>       # verify a paper
      autolean run                      # prove all sorry targets
      autolean build-library "topology" # create missing definitions

    \b
    Open problems:
      autolean challenge               # list 11 famous unsolved problems
      autolean challenge goldbach       # attempt Goldbach's conjecture
      autolean challenge --difficulty accessible
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
@click.option("--target", "-t", type=str, default=None,
              help="Only process targets matching this name (e.g., 'one_plus_one').")
def run(
    program: Path, dry_run: bool, verbose: bool,
    model: str | None, max_cycles: int | None,
    resume: bool, backend: str | None, target: str | None,
) -> None:
    """Start the autonomous proof agent loop.

    \b
    Runs continuously until all targets are proved or you press Ctrl+C.
    Use --max-cycles to set a limit. Self-correction, data collection,
    and skill learning are always active.

    \b
    The edit-build-evaluate loop:
      1. Pick highest-priority sorry target
      2. Extract goal state (hole-punch method)
      3. Query LLM for a proof
      4. Apply proof to .lean file
      5. Run `lake build` to verify
      6. Keep (git commit) or revert
      7. Collect training data + learn skill
      8. Repeat

    Press Ctrl+C once to stop gracefully. Twice to force quit.
    """
    from autolean.agent import AutoLeanAgent
    from autolean.llm_client import LLMConfig, create_llm_client
    from autolean.models import resolve_profile

    agent = AutoLeanAgent(
        program_path=program, dry_run=dry_run,
        verbose=verbose, resume=resume,
        target_filter=target,
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

    # Default: run continuously (max_cycles=0). User sets --max-cycles to limit.
    if max_cycles is not None:
        agent.config.max_cycles = max_cycles
    else:
        agent.config.max_cycles = 0  # unlimited by default
        agent.config.max_retries_per_sorry = 100  # generous retry budget
        agent.resume = True  # auto-resume from previous session

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
# prove — natural language theorem → formalize → prove
# ---------------------------------------------------------------------------


@main.command()
@click.argument("statement")
@click.option("--model", "-m", type=str, default=None, help="Model to use.")
@click.option("--max-attempts", type=int, default=10, help="Max proof attempts.")
@click.option("--program", "-p", type=click.Path(exists=True, path_type=Path),
              default="program.md", help="Path to program.md.")
def prove(statement: str, model: str | None, max_attempts: int, program: Path) -> None:
    """Prove a theorem from natural language.

    \b
    Takes a math statement in plain English, formalizes it as Lean 4,
    and attempts to prove it automatically.

    \b
    Examples:
      autolean prove "1 + 1 = 2"
      autolean prove "for all n, n + 0 = n"
      autolean prove "if P implies Q and Q implies R then P implies R"
      autolean prove "the square root of 2 is irrational"
    """
    from autolean.agent import AutoLeanAgent, parse_program
    from autolean.llm_client import LLMConfig, create_llm_client
    from autolean.models import resolve_profile
    from autolean.paper import formalize_claim, Claim

    cfg = parse_program(program)

    # Setup LLM
    if model:
        profile = resolve_profile(model)
        if profile:
            llm_cfg = LLMConfig(model=profile.model, base_url=profile.base_url,
                                temperature=profile.temperature, num_predict=profile.num_predict,
                                backend=profile.backend)
        else:
            llm_cfg = LLMConfig(model=model)
    else:
        llm_cfg = LLMConfig(model=cfg.model, temperature=cfg.temperature)

    llm = create_llm_client(llm_cfg)
    if not llm.ping():
        console.print("[red]Cannot connect to LLM. Is Ollama running?[/]")
        return

    # Step 1: Formalize
    console.print(f"[bold]Formalizing:[/] {statement}\n")
    claim = Claim(label="User", statement=statement, lean_name="user_theorem")
    with console.status("[dim]Formalizing..."):
        formalize_claim(claim, llm.generate)

    if not claim.lean_code:
        console.print("[red]Could not formalize. Try a more precise statement.[/]")
        llm.close()
        return

    console.print(f"[cyan]Lean 4 formalization:[/]")
    for line in claim.lean_code.split("\n")[:10]:
        console.print(f"  {line}")
    console.print()

    # Step 2: Write to workspace
    lean_root = program.parent / cfg.lean_project_path
    target_file = lean_root / "AutoLean" / "UserTheorems.lean"
    target_file.parent.mkdir(parents=True, exist_ok=True)

    # Append to file (don't overwrite previous theorems)
    existing = target_file.read_text() if target_file.exists() else "-- User theorems (auto-generated by autolean prove)\n\n"
    new_content = existing.rstrip() + "\n\n" + claim.lean_code + "\n"
    target_file.write_text(new_content)
    console.print(f"[green]Wrote to {target_file}[/]\n")

    # Step 3: Run agent on ONLY this target (not all sorries)
    console.print(f"[bold]Attempting proof ({max_attempts} attempts)...[/]\n")
    llm.close()

    # Extract the Lean declaration name from the formalized code
    import re as _re
    _decl_match = _re.search(r"\b(?:theorem|lemma|def)\s+(\S+)", claim.lean_code)
    target_name = _decl_match.group(1).split(":")[0].split("(")[0].strip() if _decl_match else None

    agent = AutoLeanAgent(
        program_path=program, verbose=True,
        target_filter=target_name,  # Only target the user's theorem
    )
    if model:
        profile = resolve_profile(model)
        if profile:
            from autolean.llm_client import LLMConfig as _C, create_llm_client as _f
            agent.llm = _f(_C(model=profile.model, base_url=profile.base_url,
                              temperature=profile.temperature, num_predict=profile.num_predict,
                              backend=profile.backend))
    agent.config.max_cycles = max_attempts
    agent.config.max_retries_per_sorry = max_attempts
    agent.run()


# ---------------------------------------------------------------------------
# verify — verify a paper (alias for verify-paper with auto-prove)
# ---------------------------------------------------------------------------


@main.command()
@click.argument("source")
@click.option("--pages", type=str, default=None, help="Page range (e.g., '1-5').")
@click.option("--max-cycles", type=int, default=20, help="Proof attempts after formalization.")
@click.option("--model", "-m", type=str, default=None, help="Model to use.")
@click.option("--program", "-p", type=click.Path(exists=True, path_type=Path),
              default="program.md", help="Path to program.md.")
def verify(source: str, pages: str | None, max_cycles: int, model: str | None, program: Path) -> None:
    """Verify a paper: extract claims, formalize, and attempt proofs.

    \b
    SOURCE can be a PDF path, arXiv URL, or arXiv ID.
    Automatically attempts proofs after formalization.

    \b
    Examples:
      autolean verify https://arxiv.org/abs/1811.04311
      autolean verify paper.pdf --pages 1-5
      autolean verify 2404.12534
    """
    # Delegate to verify-paper for extraction, then auto-run
    from click.testing import CliRunner
    runner = CliRunner()

    # Run verify-paper
    args = [source]
    if pages:
        args.extend(["--pages", pages])
    if model:
        args.extend(["--model", model])
    args.extend(["--program", str(program)])

    result = runner.invoke(main, ["verify-paper"] + args, catch_exceptions=False)
    console.print(result.output)

    # Now run the agent to attempt proofs
    if result.exit_code == 0:
        console.print(f"\n[bold]Attempting proofs on formalized claims...[/]\n")
        from autolean.agent import AutoLeanAgent
        agent = AutoLeanAgent(program_path=program, verbose=True)
        agent.config.max_cycles = max_cycles
        if model:
            from autolean.models import resolve_profile
            profile = resolve_profile(model)
            if profile:
                from autolean.llm_client import LLMConfig as _C, create_llm_client as _f
                agent.llm = _f(_C(model=profile.model, base_url=profile.base_url,
                                  temperature=profile.temperature, num_predict=profile.num_predict,
                                  backend=profile.backend))
        agent.run()


# ---------------------------------------------------------------------------
# verify-paper — extract and formalize claims from a PDF or arXiv link
# ---------------------------------------------------------------------------


@main.command("verify-paper", hidden=True)  # keep as alias
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
# build-library — create missing types/structures for a mathematical field
# ---------------------------------------------------------------------------


@main.command("build-library")
@click.argument("topic")
@click.option("--output", "-o", type=click.Path(path_type=Path), default=None,
              help="Output .lean file path.")
@click.option("--model", "-m", type=str, default=None, help="Model to use.")
@click.option("--prove", is_flag=True, help="Immediately attempt proofs after generating.")
@click.option("--program", "-p", type=click.Path(exists=True, path_type=Path),
              default="program.md", help="Path to program.md.")
def build_library(
    topic: str, output: Path | None, model: str | None,
    prove: bool, program: Path,
) -> None:
    """Build a local Lean 4 library for a mathematical topic.

    \b
    Creates definitions, structures, and basic lemmas that supplement
    mathlib for a specific domain. Fills gaps that mathlib doesn't cover.

    \b
    Examples:
      autolean build-library "differential geometry"
      autolean build-library "graph theory" --prove
      autolean build-library "category theory basics" -o MyLib.lean
      autolean build-library "finite automata"
      autolean build-library "tropical geometry"
    """
    import re as _re
    from autolean.agent import parse_program
    from autolean.library import generate_library_file
    from autolean.llm_client import LLMConfig, create_llm_client
    from autolean.models import resolve_profile

    cfg = parse_program(program)

    # Setup LLM
    if model:
        profile = resolve_profile(model)
        llm_cfg = LLMConfig(
            model=profile.model if profile else model,
            base_url=profile.base_url if profile else "http://localhost:11434",
            temperature=0.3,
            num_predict=32768,
            backend=profile.backend if profile else "ollama",
        )
    else:
        llm_cfg = LLMConfig(model=cfg.model, temperature=0.3, num_predict=32768)

    llm = create_llm_client(llm_cfg)
    if not llm.ping():
        console.print("[red]Cannot connect to LLM.[/]")
        return

    # Generate output path
    safe_topic = _re.sub(r"[^a-zA-Z0-9]", "", topic.title().replace(" ", ""))
    lean_root = program.parent / cfg.lean_project_path
    output = output or lean_root / "AutoLean" / f"Lib{safe_topic}.lean"

    console.print(f"[bold]Building library for:[/] {topic}")
    console.print(f"[bold]Output:[/] {output}\n")

    with console.status(f"[dim]Generating {topic} library..."):
        path = generate_library_file(topic, output, llm.generate)

    # Count generated definitions and sorrys
    content = path.read_text()
    n_defs = len(_re.findall(r"\b(?:def|structure|class|instance|theorem|lemma)\b", content))
    n_sorrys = len(_re.findall(r"\bsorry\b", content))

    console.print(f"[green]Generated {n_defs} definitions/theorems ({n_sorrys} sorry targets)[/]")
    console.print(f"  File: {path}\n")

    # Show preview
    for line in content.split("\n")[:20]:
        console.print(f"  [dim]{line}[/]")
    if len(content.split("\n")) > 20:
        console.print(f"  [dim]... ({len(content.split(chr(10))) - 20} more lines)[/]")

    llm.close()

    if prove and n_sorrys > 0:
        console.print(f"\n[bold]Attempting to prove {n_sorrys} sorry targets...[/]\n")
        from autolean.agent import AutoLeanAgent
        agent = AutoLeanAgent(program_path=program, verbose=True)
        agent.config.max_cycles = n_sorrys * 3
        agent.run()
    elif n_sorrys > 0:
        console.print(f"\n  Next: [cyan]uv run autolean run[/] to attempt {n_sorrys} proofs")


# ---------------------------------------------------------------------------
# improve — simplify/deepen/beautify an existing proof
# ---------------------------------------------------------------------------


@main.command()
@click.argument("file_path", type=click.Path(exists=True, path_type=Path))
@click.argument("theorem_name")
@click.option("--goal", type=click.Choice(["shorter", "elegant", "faster", "readable"]),
              default="elegant", help="What to optimize for.")
@click.option("--model", "-m", type=str, default=None, help="Model to use.")
@click.option("--max-attempts", type=int, default=5, help="Max improvement attempts.")
@click.option("--program", "-p", type=click.Path(exists=True, path_type=Path),
              default="program.md", help="Path to program.md.")
def improve(
    file_path: Path, theorem_name: str, goal: str,
    model: str | None, max_attempts: int, program: Path,
) -> None:
    """Improve an existing proof — make it shorter, more elegant, or faster.

    \b
    Takes a .lean file and theorem name, reads the current proof,
    asks the LLM to improve it, verifies the new version compiles,
    and replaces the original if successful.

    \b
    Examples:
      autolean improve workspace/AutoLean/Medium.lean medium_add_comm
      autolean improve workspace/AutoLean/Medium.lean medium_add_comm --goal shorter
      autolean improve my_project/Foo.lean my_theorem --goal elegant
    """
    import re
    from autolean.agent import parse_program
    from autolean.lean_interface import LeanProject
    from autolean.llm_client import LLMConfig, create_llm_client
    from autolean.models import resolve_profile
    from autolean.prompts import PROOF_GOLF_USER

    cfg = parse_program(program)
    file_path = file_path.resolve()

    # Find the theorem and its proof in the file
    content = file_path.read_text(encoding="utf-8")
    lines = content.split("\n")

    # Locate the theorem declaration
    theorem_line = None
    for i, line in enumerate(lines):
        if re.search(rf"\b{re.escape(theorem_name)}\b", line):
            if re.match(r"\s*(theorem|lemma|def)\s+", line):
                theorem_line = i
                break

    if theorem_line is None:
        console.print(f"[red]Theorem '{theorem_name}' not found in {file_path}[/]")
        return

    # Extract the current proof (everything after `:= by` until the next declaration)
    proof_start = None
    proof_end = None
    for i in range(theorem_line, len(lines)):
        if "by" in lines[i] and proof_start is None:
            proof_start = i + 1
        elif proof_start is not None and i > proof_start:
            stripped = lines[i].strip()
            if stripped and not stripped.startswith("--") and not lines[i].startswith(" ") and not lines[i].startswith("\t"):
                proof_end = i
                break
    if proof_start is None:
        console.print(f"[red]No tactic proof found for '{theorem_name}'[/]")
        return
    if proof_end is None:
        proof_end = len(lines)

    current_proof = "\n".join(lines[proof_start:proof_end])
    decl_line = "\n".join(lines[theorem_line:proof_start])

    console.print(f"[bold]Improving:[/] {theorem_name}")
    console.print(f"[bold]Goal:[/] {goal}")
    console.print(f"[bold]Current proof:[/]")
    for line in current_proof.split("\n")[:10]:
        console.print(f"  [dim]{line}[/]")
    console.print()

    # Setup LLM
    if model:
        profile = resolve_profile(model)
        if profile:
            llm_cfg = LLMConfig(model=profile.model, base_url=profile.base_url,
                                temperature=profile.temperature, num_predict=profile.num_predict,
                                backend=profile.backend)
        else:
            llm_cfg = LLMConfig(model=model)
    else:
        llm_cfg = LLMConfig(model=cfg.model, temperature=cfg.temperature)

    llm = create_llm_client(llm_cfg)
    if not llm.ping():
        console.print("[red]Cannot connect to LLM.[/]")
        return

    # Find project root
    lean_root = file_path.parent
    while lean_root != lean_root.parent:
        if (lean_root / "lakefile.lean").exists() or (lean_root / "lakefile.toml").exists():
            break
        lean_root = lean_root.parent
    project = LeanProject(lean_root)

    goal_prompts = {
        "shorter": "Make this proof as SHORT as possible. Minimize the number of tactics and lines.",
        "elegant": "Make this proof more ELEGANT and mathematically beautiful. Use clean, idiomatic Lean 4.",
        "faster": "Make this proof FASTER for the Lean kernel to check. Avoid slow tactics like simp on large goals.",
        "readable": "Make this proof more READABLE. Use descriptive names, add comments, structure clearly.",
    }

    system = (
        "You are a Lean 4 proof golf expert. "
        f"{goal_prompts[goal]} "
        "Output ONLY the improved tactic block. No explanation, no markdown."
    )

    for attempt in range(1, max_attempts + 1):
        console.print(f"[bold]Attempt {attempt}/{max_attempts}...[/]")

        context = f"{decl_line}\n{current_proof}"
        user_prompt = PROOF_GOLF_USER.format(
            file_context=context,
            decl_name=theorem_name,
            line=theorem_line + 1,
            current_proof=current_proof,
        )

        with console.status("[dim]Generating improved proof..."):
            response = llm.generate(system, user_prompt)

        from autolean.agent import clean_llm_proof
        new_proof = clean_llm_proof(response.text, tactic_mode=True)

        if not new_proof or new_proof.strip() == current_proof.strip():
            console.print(f"  [yellow]No improvement generated.[/]")
            continue

        console.print(f"  [cyan]New proof:[/]")
        for line in new_proof.split("\n")[:8]:
            console.print(f"    [cyan]{line}[/]")

        # Try to apply the new proof
        new_lines = lines.copy()
        # Replace proof lines
        indent = "  "
        replacement = "\n".join(indent + l.strip() if l.strip() else "" for l in new_proof.split("\n"))
        new_lines[proof_start:proof_end] = replacement.split("\n")
        new_content = "\n".join(new_lines)

        # Write and build
        file_path.write_text(new_content, encoding="utf-8")
        with console.status("[dim]Building..."):
            build = project.check_file(file_path, timeout=120)

        if build.success:
            old_len = len(current_proof.strip().split("\n"))
            new_len = len(new_proof.strip().split("\n"))
            console.print(f"  [bold green]Improved![/] {old_len} lines -> {new_len} lines")
            if new_len < old_len:
                console.print(f"  [green]Reduced by {old_len - new_len} lines ({(old_len-new_len)/old_len*100:.0f}%)[/]")
            llm.close()
            return
        else:
            # Revert
            file_path.write_text(content, encoding="utf-8")
            err = build.errors[0].message[:100] if build.errors else "unknown error"
            console.print(f"  [red]Build failed:[/] {err}")

    console.print(f"[yellow]Could not improve after {max_attempts} attempts.[/]")
    llm.close()


# ---------------------------------------------------------------------------
# challenge — attempt an open mathematical problem
# ---------------------------------------------------------------------------


@main.command()
@click.argument("problem_id", required=False)
@click.option("--field", type=str, default=None, help="Filter by field (e.g., 'number theory').")
@click.option("--difficulty", type=click.Choice(["accessible", "hard", "very-hard", "millennium"]),
              default=None, help="Filter by difficulty.")
@click.option("--max-cycles", type=int, default=50, help="Max proof attempts.")
@click.option("--program", "-p", type=click.Path(exists=True, path_type=Path),
              default="program.md", help="Path to program.md.")
def challenge(
    problem_id: str | None, field: str | None, difficulty: str | None,
    max_cycles: int, program: Path,
) -> None:
    """Take on an open mathematical problem.

    \b
    Without arguments, lists all available problems.
    With a problem ID, generates the formalization and starts proving.

    \b
    Examples:
      autolean challenge                        # list all problems
      autolean challenge collatz                 # attempt Collatz conjecture
      autolean challenge goldbach --max-cycles 100
      autolean challenge --field "number theory" # filter by field
      autolean challenge --difficulty accessible # show easiest problems
    """
    from autolean.challenges import (
        OPEN_PROBLEMS, generate_challenge_file, print_problems_table,
    )

    if problem_id is None:
        print_problems_table(filter_field=field, filter_difficulty=difficulty)
        return

    # Find the problem
    problem = next((p for p in OPEN_PROBLEMS if p.id == problem_id), None)
    if not problem:
        # Try partial match
        matches = [p for p in OPEN_PROBLEMS if problem_id.lower() in p.id.lower() or problem_id.lower() in p.name.lower()]
        if len(matches) == 1:
            problem = matches[0]
        elif matches:
            console.print(f"[yellow]Multiple matches for '{problem_id}':[/]")
            for m in matches:
                console.print(f"  {m.id}: {m.name}")
            return
        else:
            console.print(f"[red]Problem '{problem_id}' not found.[/]")
            console.print("Run [cyan]autolean challenge[/] to see all problems.")
            return

    # Display problem info
    diff_colors = {"accessible": "green", "hard": "yellow", "very-hard": "red", "millennium": "bold magenta"}
    dc = diff_colors.get(problem.difficulty, "white")

    console.print(Panel(
        f"[bold]{problem.name}[/bold]\n"
        f"Field:      {problem.field}\n"
        f"Difficulty: [{dc}]{problem.difficulty}[/{dc}]\n"
        f"\n{problem.description}\n"
        + (f"\nSub-results: {len(problem.sub_results)} provable lemma(s)" if problem.sub_results else "")
        + (f"\nRef: {problem.references[0]}" if problem.references else ""),
        title=f"Challenge: {problem.id}",
        border_style=dc.split()[-1] if " " in dc else dc,
        width=75,
    ))

    # Generate the challenge file
    path = generate_challenge_file(problem)
    console.print(f"\n[green]Generated:[/] {path}")

    # Count sorry targets
    content = open(path).read()
    n_sorry = content.count("sorry")
    console.print(f"[cyan]{n_sorry} sorry target(s)[/] (main conjecture + {len(problem.sub_results)} sub-results)")

    # Ask if they want to start proving
    console.print(f"\n[bold]Starting proof attempts ({max_cycles} cycles)...[/]\n")

    from autolean.agent import AutoLeanAgent
    agent = AutoLeanAgent(program_path=program, verbose=True)
    agent.config.max_cycles = max_cycles
    agent.run()


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
