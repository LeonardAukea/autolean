"""CLI entry point — `uv run autolean` or `python -m autolean`."""

from __future__ import annotations

import sys
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

    Overnight sorry elimination, autoformalization, and proof golf.
    Inspired by autoresearch and autokernel.

    \b
    Quick start:
      uv run autolean run              # start the agent loop
      uv run autolean run --dry-run    # preview without modifying files
      uv run autolean scan             # just scan for sorry targets
      uv run autolean check            # verify LLM + Lean connectivity
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


# ---------------------------------------------------------------------------
# run — the main agent loop
# ---------------------------------------------------------------------------


@main.command()
@click.option(
    "--program", "-p",
    type=click.Path(exists=True, path_type=Path),
    default="program.md",
    help="Path to program.md (agent instructions).",
)
@click.option(
    "--dry-run", "-n",
    is_flag=True,
    default=False,
    help="Preview mode — query LLM but don't modify files.",
)
@click.option(
    "--verbose", "-v",
    is_flag=True,
    default=False,
    help="Show detailed output (LLM responses, build logs).",
)
@click.option(
    "--model", "-m",
    type=str,
    default=None,
    help="Override the Ollama model (e.g., gemma4:31b).",
)
@click.option(
    "--max-cycles",
    type=int,
    default=None,
    help="Override max experiment cycles (0 = unlimited).",
)
@click.option(
    "--resume", "-r",
    is_flag=True,
    default=False,
    help="Resume from previous session (reads results.tsv).",
)
@click.option(
    "--backend", "-b",
    type=click.Choice(["ollama", "openai_compat"]),
    default=None,
    help="LLM backend (default: ollama).",
)
def run(
    program: Path,
    dry_run: bool,
    verbose: bool,
    model: str | None,
    max_cycles: int | None,
    resume: bool,
    backend: str | None,
) -> None:
    """Start the autonomous proof agent loop.

    \b
    The agent reads program.md, scans for sorry targets,
    and enters the edit-build-evaluate loop:
      1. Pick highest-priority sorry target
      2. Query LLM (Gemma 4 via Ollama by default) for a proof
      3. Apply the proof to the .lean file
      4. Run `lake build` to check
      5. Keep (git commit) or revert
      6. Log to results.tsv
      7. Repeat

    Press Ctrl+C once to stop after the current cycle.
    Press Ctrl+C twice to force quit.
    """
    from autolean.agent import AutoLeanAgent

    agent = AutoLeanAgent(
        program_path=program,
        dry_run=dry_run,
        verbose=verbose,
        resume=resume,
    )

    # CLI overrides
    if model:
        agent.config.model = model
        agent.llm.config.model = model
    if backend:
        agent.llm.config.backend = backend
        # Recreate client with new backend
        from autolean.llm_client import create_llm_client
        agent.llm = create_llm_client(agent.llm.config)
    if max_cycles is not None:
        agent.config.max_cycles = max_cycles

    agent.run()


# ---------------------------------------------------------------------------
# scan — just find sorry targets
# ---------------------------------------------------------------------------


@main.command()
@click.option(
    "--project", "-d",
    type=click.Path(exists=True, path_type=Path),
    default="workspace",
    help="Path to Lean project root.",
)
@click.option(
    "--json", "as_json",
    is_flag=True,
    default=False,
    help="Output as JSON.",
)
def scan(project: Path, as_json: bool) -> None:
    """Scan a Lean project for sorry targets."""
    from autolean.scanner import prioritize_targets, scan_project

    project = project.resolve()
    targets = scan_project(project)
    targets = prioritize_targets(targets)

    if as_json:
        import json

        data = [
            {
                "file": str(t.file.relative_to(project)),
                "line": t.line,
                "col": t.col,
                "decl_name": t.decl_name,
                "id": t.id,
            }
            for t in targets
        ]
        click.echo(json.dumps(data, indent=2))
    else:
        console.print(f"[bold]Found {len(targets)} sorry target(s) in {project}[/]\n")
        for t in targets:
            try:
                rel = t.file.relative_to(project)
            except ValueError:
                rel = t.file
            console.print(f"  {rel}:{t.line} — [cyan]{t.decl_name}[/]")

        if not targets:
            console.print("  [green]No sorries found![/]")


# ---------------------------------------------------------------------------
# check — verify connectivity
# ---------------------------------------------------------------------------


@main.command()
@click.option(
    "--program", "-p",
    type=click.Path(exists=True, path_type=Path),
    default="program.md",
    help="Path to program.md.",
)
def check(program: Path) -> None:
    """Verify Ollama + Lean connectivity."""
    from autolean.agent import parse_program
    from autolean.lean_interface import LeanProject
    from autolean.llm_client import LLMConfig, OllamaClient

    cfg = parse_program(program)

    # Check Ollama
    console.print("[bold]Checking Ollama...[/]")
    llm = OllamaClient(config=LLMConfig(model=cfg.model))
    if llm.ping():
        console.print(f"  [green]✓[/] Connected to {llm.config.base_url}")
        console.print(f"  [green]✓[/] Model: {cfg.model}")

        # Quick generation test
        console.print("  Testing generation...")
        try:
            resp = llm.generate(
                system="You are a Lean 4 expert.",
                user="What tactic closes the goal `⊢ True`? Reply with ONE word.",
            )
            console.print(f"  [green]✓[/] Response: {resp.text[:50]}")
            console.print(f"  [green]✓[/] {resp.tokens_per_second:.1f} tok/s")
        except Exception as e:
            console.print(f"  [red]✗[/] Generation failed: {e}")
    else:
        console.print(f"  [red]✗[/] Cannot reach Ollama")

    # Check Lean
    console.print("\n[bold]Checking Lean 4...[/]")
    lean_root = program.parent / cfg.lean_project_path
    try:
        proj = LeanProject(lean_root)
        console.print(f"  [green]✓[/] Project: {proj.root}")

        files = proj.lean_files()
        console.print(f"  [green]✓[/] Lean files: {len(files)}")

        console.print("  Building (may take a moment)...")
        build = proj.build(timeout=120)
        if build.success:
            console.print(
                f"  [green]✓[/] Build succeeded ({build.duration_seconds:.1f}s)"
            )
        else:
            console.print(
                f"  [yellow]⚠[/] Build has errors ({len(build.errors)} error(s))"
            )
            for d in build.errors[:3]:
                console.print(f"    {d}")
    except FileNotFoundError as e:
        console.print(f"  [red]✗[/] {e}")

    llm.close()


# ---------------------------------------------------------------------------
# results — show experiment log
# ---------------------------------------------------------------------------


@main.command()
@click.option(
    "--file", "-f",
    type=click.Path(path_type=Path),
    default="workspace/results.tsv",
    help="Path to results.tsv.",
)
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

    table = Table(title=f"Last {min(tail, len(rows))} experiments")
    table.add_column("#", style="dim")
    table.add_column("Target")
    table.add_column("Outcome")
    table.add_column("Attempt")
    table.add_column("Time")
    table.add_column("Tokens")

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
        )

    console.print(table)


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
