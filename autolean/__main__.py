"""CLI entry point — `uv run autolean` or `python -m autolean`."""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.console import Console
from rich.panel import Panel
from rich.text import Text

from autolean import __version__
from autolean.llm import BACKEND_NAMES, LLMBackend, LLMError, create_llm_client
from autolean.provenance import ProofEnvironmentError

if TYPE_CHECKING:
    from autolean.agent import AutoLeanAgent
    from autolean.lean_interface import LeanProject
    from autolean.program import ProgramConfig
    from autolean.provenance import ProofEnvironment

console = Console()

DEFAULT_LEAN_TOOLCHAIN = "leanprover/lean4:v4.33.0"
LEAN_LIBRARY_RELEASE = "v4.33.0"

#: Difficulty score → label, and label → display colour.
DIFFICULTY_LABELS = {
    0: "trivial",
    1: "basic",
    2: "easy",
    5: "medium",
    6: "hard",
    7: "hard",
    8: "advanced",
    9: "research",
    10: "research",
}
DIFFICULTY_STYLES = {
    "trivial": "green",
    "basic": "green",
    "easy": "cyan",
    "medium": "yellow",
    "hard": "red",
    "advanced": "red",
    "research": "magenta",
}

COMMAND_ALIASES = {
    "check": "doctor",
    "diff": "changes",
    "run": "solve",
    "scan": "targets",
    "ui": "workbench",
}
COMMAND_SECTIONS = (
    ("Interactive", ("workbench",)),
    ("Proof workflows", ("solve", "prove", "improve", "verify", "challenge", "build-library")),
    ("Understand", ("targets", "inspect", "changes", "results")),
    ("Project", ("doctor", "models", "environment", "init")),
    ("Training", ("export-training", "finetune-config")),
)


class AutoLeanGroup(click.Group):
    """Click group with stable aliases and task-oriented help sections."""

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        return super().get_command(ctx, COMMAND_ALIASES.get(cmd_name, cmd_name))

    def format_commands(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        listed: set[str] = set()
        for heading, names in COMMAND_SECTIONS:
            rows: list[tuple[str, str]] = []
            for name in names:
                command = super().get_command(ctx, name)
                if command is None or command.hidden:
                    continue
                rows.append((name, command.get_short_help_str()))
                listed.add(name)
            if rows:
                with formatter.section(heading):
                    formatter.write_dl(rows)

        remaining = []
        for name in self.list_commands(ctx):
            command = super().get_command(ctx, name)
            if name not in listed and command is not None and not command.hidden:
                remaining.append((name, command.get_short_help_str()))
        if remaining:
            with formatter.section("Other"):
                formatter.write_dl(remaining)


# ---------------------------------------------------------------------------
# Shared option plumbing
# ---------------------------------------------------------------------------

model_option = click.option(
    "--model",
    "-m",
    type=str,
    default=None,
    help="Model profile, alias, or raw model string (see `autolean models`).",
)
backend_option = click.option(
    "--backend",
    "-b",
    type=click.Choice(BACKEND_NAMES),
    default=None,
    help="Override the backend the model profile selects.",
)
program_option = click.option(
    "--program",
    "-p",
    type=click.Path(exists=True, path_type=Path),
    default="program.md",
    help="Path to program.md.",
)


def _llm_for(
    model: str | None,
    backend: str | None,
    program_config: ProgramConfig,
    *,
    timeout: float | None = None,
) -> LLMBackend:
    """Build the backend a command should use.

    Precedence is CLI flag, then program.md, then the model profile's own
    defaults. This is the only place any command decides which model to talk
    to, so `--model` behaves identically everywhere.
    """
    from autolean.models import resolve_llm_config

    config = resolve_llm_config(
        model or program_config.model,
        backend=backend or program_config.backend,
        base_url=program_config.endpoint,
        temperature=program_config.temperature,
        timeout=timeout if timeout is not None else program_config.llm_timeout_seconds,
        max_output_tokens=program_config.max_output_tokens,
        effort=program_config.effort,
    )
    return create_llm_client(config)


def _connected_llm(
    model: str | None,
    backend: str | None,
    program_config: ProgramConfig,
    *,
    timeout: float | None = None,
) -> LLMBackend:
    """Build a backend and require its local preflight, or abort with advice.

    The caller owns the returned backend — use it as a context manager, or
    call `close()` explicitly.
    """
    llm = _llm_for(model, backend, program_config, timeout=timeout)
    console.print(f"[dim]Model:[/] {llm.config.model} [dim]via[/] {llm.config.backend}")
    if llm.config.model_revision:
        console.print(f"[dim]Revision:[/] {llm.config.model_revision}")
    if llm.config.model_artifact_sha256:
        console.print(f"[dim]Weight SHA-256:[/] {llm.config.model_artifact_sha256}")
    if llm.config.seed is not None:
        console.print(f"[dim]Sampling seed:[/] {llm.config.seed}")
    try:
        reachable = llm.ping()
    except LLMError as e:
        llm.close()
        raise click.ClickException(str(e)) from e
    if not reachable:
        llm.close()
        raise click.ClickException(
            f"Backend '{llm.config.backend}' did not pass preflight. "
            "Run `autolean models` to see what is ready."
        )
    context = click.get_current_context(silent=True)
    if context is not None:
        context.call_on_close(llm.close)
    return llm


def _agent_for(
    program: Path,
    *,
    model: str | None = None,
    backend: str | None = None,
    dry_run: bool = False,
    verbose: bool = False,
    resume: bool = False,
    target_filter: str | None = None,
    target_file: Path | None = None,
) -> AutoLeanAgent:
    """Build one agent with the shared program and model precedence rules."""
    from autolean.agent import AutoLeanAgent

    agent: AutoLeanAgent | None = None
    try:
        agent = AutoLeanAgent(
            program_path=program,
            dry_run=dry_run,
            verbose=verbose,
            resume=resume,
            target_filter=target_filter,
            target_file=target_file,
        )
        if model is not None or backend is not None:
            agent.llm.close()
            agent.llm = _llm_for(model, backend, agent.config)
        return agent
    except (LLMError, OSError, ValueError) as error:
        if agent is not None:
            agent.close()
        raise click.ClickException(f"Agent configuration failed: {error}") from error


def _run_agent(agent: AutoLeanAgent) -> None:
    """Run an owned agent and translate its terminal status for Click."""
    try:
        result = agent.run()
    finally:
        agent.close()
    if not result.successful:
        raise click.ClickException(result.message or "Agent run failed.")


def _accept_generated_source(
    lean_root: Path,
    output: Path,
    content: str,
    *,
    timeout: int = 120,
    expected_content: str | None = None,
) -> Path:
    """Compile generated source in isolation, then install the exact bytes."""
    from autolean.lean_interface import LeanProject

    try:
        project = LeanProject(lean_root)
        if expected_content is None and output.exists():
            raise OSError(f"refusing to overwrite existing generated source: {output}")
        result = project.accept_candidate(
            output,
            content,
            timeout=timeout,
            expected_content=expected_content,
            require_absent=expected_content is None,
        )
    except (OSError, ValueError) as e:
        raise click.ClickException(f"Generated Lean output could not be accepted: {e}") from e
    if result.success:
        return output

    detail = result.errors[0].message if result.errors else result.stderr.strip() or result.stdout.strip()
    detail = " ".join(detail.split())[:500] if detail else "Lean rejected the source"
    raise click.ClickException(f"Generated Lean failed sandboxed compilation: {detail}")


@click.group(cls=AutoLeanGroup, invoke_without_command=True)
@click.version_option(version=__version__)
@click.pass_context
def main(ctx: click.Context) -> None:
    """AutoLean — Autonomous Lean 4 proof agent.

    \b
    Sandboxed sorry elimination, autoformalization, and proof improvement.

    \b
    Quick start:
      autolean workbench                 # choose a model and proof target
      autolean prove "1 + 1 = 2"       # prove a theorem
      autolean challenge collatz        # attempt an open problem
      autolean verify <arxiv-url>       # verify a paper
      autolean solve                    # prove all sorry targets
      autolean build-library "topology" # create missing definitions

    \b
    Open problems:
      autolean challenge               # list 11 famous unsolved problems
      autolean challenge goldbach       # attempt Goldbach's conjecture
      autolean challenge --difficulty accessible
    """
    if ctx.invoked_subcommand is None:
        click.echo(ctx.get_help())


@main.command()
@program_option
def workbench(program: Path) -> None:
    """Open the interactive model and proof workbench."""
    from autolean.workbench import run_workbench

    try:
        run_workbench(program)
    except (OSError, ValueError) as error:
        raise click.ClickException(str(error)) from error


# ---------------------------------------------------------------------------
# solve — the main agent loop
# ---------------------------------------------------------------------------


@main.command("solve")
@program_option
@click.option("--dry-run", "-n", is_flag=True, help="Query LLM but don't modify files.")
@click.option("--verbose", "-v", is_flag=True, help="Show detailed output.")
@model_option
@backend_option
@click.option(
    "--max-cycles", type=click.IntRange(min=0), default=None, help="Max experiment cycles (0 = unlimited)."
)
@click.option("--resume", "-r", is_flag=True, help="Resume from previous session.")
@click.option(
    "--overnight", is_flag=True, help="Unlimited cycles, 100 retries per sorry, epoch resets, auto-resume."
)
@click.option(
    "--target",
    "-t",
    type=str,
    default=None,
    help="Only process targets matching this name (e.g., 'one_plus_one').",
)
def solve(
    program: Path,
    dry_run: bool,
    verbose: bool,
    model: str | None,
    backend: str | None,
    max_cycles: int | None,
    resume: bool,
    overnight: bool,
    target: str | None,
) -> None:
    """Start the autonomous proof agent loop.

    \b
    Runs continuously until all targets are proved or you press Ctrl+C.
    Use --max-cycles to set a limit. Self-correction, data collection,
    and skill learning are always active.

    \b
    The proof experiment loop:
      1. Pick highest-priority sorry target
      2. Extract the Lean goal and Tree-sitter structure
      3. Assemble bounded search, skill, and failure context
      4. Query the configured model
      5. Validate the candidate in the OS sandbox and Lean kernel
      6. Accept the exact validated bytes and record provenance
      7. Repeat

    Press Ctrl+C once to stop gracefully. Twice to force quit.
    """
    agent = _agent_for(
        program,
        model=model,
        backend=backend,
        dry_run=dry_run,
        verbose=verbose,
        resume=resume,
        target_filter=target,
    )

    if max_cycles is not None:
        agent.config.max_cycles = max_cycles
    if overnight:
        agent.config.max_cycles = 0  # unlimited; the loop resets epochs itself
        agent.config.max_retries_per_sorry = 100
        agent.resume = True

    _run_agent(agent)


# ---------------------------------------------------------------------------
# targets — find sorry targets
# ---------------------------------------------------------------------------


@main.command("targets")
@click.option(
    "--project",
    "-d",
    type=click.Path(exists=True, path_type=Path),
    default="workspace",
    help="Path to Lean project root.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(("table", "json"), case_sensitive=False),
    default="table",
    show_default=True,
    help="Output format.",
)
@click.option("--json", "legacy_json", is_flag=True, hidden=True)
def targets(project: Path, output_format: str, legacy_json: bool) -> None:
    """Scan a Lean project for sorry targets (ordered by difficulty)."""
    from autolean.scanner import difficulty_score, prioritize_targets, scan_project

    project = project.resolve()
    targets = scan_project(project)
    targets = prioritize_targets(targets)

    if output_format == "json" or legacy_json:
        import json

        data = [
            {
                "file": str(t.file.relative_to(project)),
                "line": t.line,
                "col": t.col,
                "decl_name": t.decl_name,
                "qualified_decl_name": t.qualified_decl_name,
                "id": t.id,
                "tactic_mode": t.tactic_mode,
                "difficulty": difficulty_score(t),
            }
            for t in targets
        ]
        click.echo(json.dumps(data, indent=2))
    else:
        console.print(f"[bold]Found {len(targets)} sorry target(s) in {project}[/]\n")
        current_diff = -1
        for t in targets:
            diff = difficulty_score(t)
            if diff != current_diff:
                label = DIFFICULTY_LABELS.get(diff, f"level-{diff}")
                style = DIFFICULTY_STYLES.get(label, "white")
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
# inspect — show the exact structural context for one target
# ---------------------------------------------------------------------------


@main.command("inspect")
@click.argument("target_query")
@click.option(
    "--project",
    "-d",
    type=click.Path(exists=True, path_type=Path),
    default="workspace",
    help="Path to Lean project root.",
)
@click.option(
    "--format",
    "output_format",
    type=click.Choice(("text", "json"), case_sensitive=False),
    default="text",
    show_default=True,
    help="Output format.",
)
@click.option(
    "--goal-state",
    is_flag=True,
    help="Ask Lean for the elaborated goal at the placeholder.",
)
def inspect_target(
    target_query: str,
    project: Path,
    output_format: str,
    goal_state: bool,
) -> None:
    """Inspect Tree-sitter and optional Lean context for TARGET_QUERY."""
    import json

    from autolean.lean_interface import LeanProject
    from autolean.scanner import scan_project
    from autolean.structure import LeanStructureProvider

    project = project.resolve()
    targets = scan_project(project)
    exact = [
        target
        for target in targets
        if target_query in {target.id, target.decl_name, target.qualified_decl_name}
    ]
    matches = exact or [
        target
        for target in targets
        if target_query in target.id
        or target_query in target.decl_name
        or target_query in target.qualified_decl_name
    ]
    if not matches:
        raise click.ClickException(f"No sorry target matches '{target_query}'.")
    if len(matches) > 1:
        choices = "\n  ".join(target.id for target in matches[:12])
        raise click.ClickException(f"Target '{target_query}' is ambiguous. Use one of:\n  {choices}")

    target = matches[0]
    source = target.file.read_text(encoding="utf-8")
    structure = LeanStructureProvider().inspect(
        target.file,
        source,
        line=target.line,
        col=target.col,
        declaration_name=target.decl_name,
    )
    goal = None
    if goal_state:
        goal = LeanProject(project).get_goal_via_hole_punch(
            target.file,
            target.line,
            target.col,
        )

    if output_format == "json":
        payload = {
            "target": {
                "id": target.id,
                "file": str(target.file.relative_to(project)),
                "line": target.line,
                "col": target.col,
                "declaration": target.decl_name,
                "qualified_declaration": target.qualified_decl_name,
                "tactic_mode": target.tactic_mode,
            },
            "structure": structure.as_dict(),
            "goal_state": goal,
        }
        click.echo(json.dumps(payload, indent=2, sort_keys=True))
        return

    rel_path = target.file.relative_to(project)
    console.print(
        Panel(
            structure.render(),
            title=f"{target.qualified_decl_name or target.decl_name}",
            subtitle=f"{rel_path}:{target.line}:{target.col}",
            border_style="cyan",
        )
    )
    if goal_state:
        console.print(
            Panel(
                goal or "Goal state unavailable.",
                title="Lean goal state",
                border_style="green" if goal else "yellow",
            )
        )


# ---------------------------------------------------------------------------
# models — list available LLM profiles
# ---------------------------------------------------------------------------


@main.command()
def models() -> None:
    """List available model profiles and check installation status."""
    from autolean.models import print_models_table

    print_models_table()


# ---------------------------------------------------------------------------
# doctor — verify connectivity
# ---------------------------------------------------------------------------


def _doctor_preflight(llm: LLMBackend) -> str | None:
    """Check one backend without generating a completion."""
    try:
        ready = llm.ping()
    except LLMError as error:
        console.print(f"  [red]FAIL[/] Preflight: {error}")
        return f"backend preflight: {error}"
    if ready:
        console.print("  [green]OK[/] Preflight passed")
        return None
    console.print("  [red]FAIL[/] Backend preflight did not pass — see `autolean models`.")
    return "backend preflight did not pass"


def _doctor_generate_proof(llm: LLMBackend) -> str:
    """Request and normalize the backend smoke proof."""
    from autolean.agent import clean_llm_proof
    from autolean.generated_code import validate_generated_proof
    from autolean.provenance import sha256_text

    response = llm.generate(
        system=(
            "You are a Lean 4 proof assistant. Return only a tactic "
            "proof body with no markdown or explanation."
        ),
        user="Fill the proof of `theorem AutoLeanBackendSmoke : True := by sorry`.",
    )
    console.print("  [green]OK[/] Response: ", Text(response.text[:60]), sep="")
    proof = validate_generated_proof(clean_llm_proof(response.text, tactic_mode=True))
    console.print(f"  [green]OK[/] Proof SHA-256: {sha256_text(proof)}")
    if llm.capabilities.token_counts and response.output_tokens:
        console.print(
            f"  [green]OK[/] {response.output_tokens} tokens "
            f"in {response.duration_seconds:.1f}s "
            f"({response.tokens_per_second:.1f} tok/s)"
        )
    return proof


def _doctor_model(
    config: ProgramConfig,
    model: str | None,
    backend: str | None,
) -> tuple[str, list[str]]:
    """Check the selected backend and return one normalized proof."""
    from autolean.generated_code import GeneratedCodeError

    failures: list[str] = []
    console.print("[bold]Checking the model backend...[/]")
    llm = _llm_for(model, backend, config)
    console.print(f"  Model:   {llm.config.model}")
    console.print(f"  Backend: {llm.config.backend}")
    if llm.config.base_url:
        console.print(f"  Endpoint: {llm.config.base_url}")
    if llm.config.model_revision:
        console.print(f"  Revision: {llm.config.model_revision}")
    if llm.config.model_artifact_sha256:
        console.print(f"  Weight SHA-256: {llm.config.model_artifact_sha256}")
    if llm.config.seed is not None:
        console.print(f"  Seed:    {llm.config.seed}")

    with llm:
        preflight_failure = _doctor_preflight(llm)
        if preflight_failure:
            return "", [preflight_failure]
        try:
            return _doctor_generate_proof(llm), failures
        except (GeneratedCodeError, LLMError) as error:
            failures.append(f"model generation: {error}")
            console.print(f"  [red]FAIL[/] Generation: {error}")
            return "", failures


def _doctor_validate_proof(
    project: LeanProject,
    environment: ProofEnvironment,
    proof: str,
) -> str | None:
    """Validate and display the exact Lean source built from a model proof."""
    indented_proof = "\n".join(f"  {line}" for line in proof.splitlines())
    smoke_source = f"import Mathlib\n\ntheorem AutoLeanBackendSmoke : True := by\n{indented_proof}\n"
    console.print(Panel(Text(smoke_source), title="Lean kernel candidate", border_style="cyan"))
    smoke = project.validate_candidate(
        project.root / "AutoLeanBackendSmoke.lean",
        smoke_source,
        timeout=120,
        declaration="AutoLeanBackendSmoke",
        declaration_line=3,
        expected_environment=environment.sha256,
    )
    if smoke.success:
        console.print(f"  [green]OK[/] Model proof passed sandboxed Lean ({smoke.duration_seconds:.1f}s)")
        return None
    detail = smoke.stderr or (smoke.errors[0].message if smoke.errors else "Lean rejected the model proof")
    console.print(f"  [red]FAIL[/] Model proof: {detail[:300]}")
    return f"model proof validation: {detail[:300]}"


def _doctor_lean(program: Path, config: ProgramConfig, model_proof: str) -> list[str]:
    """Check the pinned proof environment, candidate, and trusted project."""
    from autolean.lean_interface import LeanProject

    failures: list[str] = []
    console.print("\n[bold]Checking Lean 4...[/]")
    lean_root = program.parent / config.lean_project_path
    try:
        project = LeanProject(lean_root)
        console.print(f"  [green]OK[/] Project: {project.root}")
        console.print(f"  [green]OK[/] Lean files: {len(project.lean_files())}")
        environment = project.proof_environment()
        console.print(f"  [green]OK[/] Environment: sha256:{environment.sha256}")
        if model_proof:
            proof_failure = _doctor_validate_proof(project, environment, model_proof)
            if proof_failure:
                failures.append(proof_failure)
        console.print("  Building...")
        build = project.build(timeout=120)
        if build.success:
            console.print(f"  [green]OK[/] Build succeeded ({build.duration_seconds:.1f}s)")
        else:
            failures.append("Lean project build failed")
            console.print(f"  [red]FAIL[/] Build errors: {len(build.errors)}")
    except (FileNotFoundError, OSError, ProofEnvironmentError) as error:
        failures.append(f"Lean toolchain: {error}")
        console.print(f"  [red]FAIL[/] {error}")
    return failures


@main.command("doctor")
@program_option
@model_option
@backend_option
def doctor(program: Path, model: str | None, backend: str | None) -> None:
    """Verify that the configured model and the Lean toolchain both work."""
    from autolean.program import parse_program

    config = parse_program(program)
    model_proof, failures = _doctor_model(config, model, backend)
    failures.extend(_doctor_lean(program, config, model_proof))

    if failures:
        raise click.ClickException("Checks failed: " + "; ".join(failures))


@main.command("environment")
@click.option(
    "--project",
    "-d",
    type=click.Path(exists=True, path_type=Path),
    default="workspace",
    help="Path to Lean project root.",
)
@click.option("--json", "as_json", is_flag=True, help="Output machine-readable JSON.")
def environment_command(project: Path, as_json: bool) -> None:
    """Identify the complete Lean proof environment by content."""
    import json

    from autolean.lean_interface import LeanProject

    try:
        environment = LeanProject(project).proof_environment(refresh=True)
    except (FileNotFoundError, OSError, ProofEnvironmentError) as e:
        raise click.ClickException(f"Proof environment identification failed: {e}") from e

    if as_json:
        click.echo(json.dumps(environment.as_dict(), indent=2, sort_keys=True))
        return

    console.print(f"[bold]Proof environment[/] {project.resolve()}")
    console.print(f"  SHA-256:       {environment.sha256}")
    console.print(f"  Lean:          {environment.lean_version}")
    console.print(f"  Toolchain:     {environment.lean_toolchain}")
    console.print(f"  Manifest:      {environment.manifest_sha256}")
    console.print(f"  Artifacts:     {environment.artifact_count:,}")
    console.print("  Dependencies:")
    for dependency in environment.dependencies:
        console.print(f"    {dependency}")


# ---------------------------------------------------------------------------
# results — show experiment log
# ---------------------------------------------------------------------------


@main.command()
@click.option(
    "--file",
    "-f",
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

    with open(file, encoding="utf-8") as f:
        reader = csv.DictReader(f, delimiter="\t")
        rows = list(reader)

    if not rows:
        console.print("[yellow]No experiments recorded yet.[/]")
        return

    # Summary
    total = len(rows)
    successes = sum(1 for r in rows if r.get("outcome") == "success")
    console.print(
        f"[bold]Summary:[/] {successes}/{total} attempts succeeded ({successes / total * 100:.0f}%)\n"
        if total
        else ""
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
@model_option
@backend_option
@click.option(
    "--max-attempts",
    type=click.IntRange(min=0),
    default=0,
    help="Max proof attempts (0 = unlimited).",
)
@program_option
def prove(
    statement: str,
    model: str | None,
    backend: str | None,
    max_attempts: int,
    program: Path,
) -> None:
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
    from autolean.paper import Claim, formalize_claim
    from autolean.program import parse_program

    cfg = parse_program(program)

    # Step 1: Formalize
    console.print(f"[bold]Formalizing:[/] {statement}\n")
    claim = Claim(label="User", statement=statement, lean_name="user_theorem")
    with _connected_llm(model, backend, cfg) as llm, console.status("[dim]Formalizing..."):
        formalize_claim(claim, llm.generate)

    if not claim.lean_code:
        raise click.ClickException("Could not formalize the statement; provide a more precise statement.")

    console.print("[cyan]Lean 4 formalization:[/]")
    for line in claim.lean_code.split("\n")[:10]:
        console.print(f"  {line}")
    console.print()

    # Step 2: Write to workspace
    lean_root = program.parent / cfg.lean_project_path
    target_file = lean_root / "AutoLean" / "UserTheorems.lean"

    # Append to file (don't overwrite previous theorems)
    header = "-- User theorems (auto-generated by autolean prove)\n\n"
    existing = target_file.read_text(encoding="utf-8") if target_file.exists() else header

    # The target owns its imports, so a declaration cannot select new ones.
    import re as _re

    code_lines = [
        line for line in claim.lean_code.split("\n") if not line.strip().startswith(("import ", "-- import"))
    ]
    clean_code = "\n".join(code_lines).strip()

    # Extract the Lean declaration name from the formalized code
    _decl_match = _re.search(r"\b(?:theorem|lemma|def)\s+(\S+)", clean_code)
    target_name = _decl_match.group(1).split(":")[0].split("(")[0].strip() if _decl_match else None

    # Deduplicate: if a declaration with the same name already exists, suffix _N
    if target_name and _re.search(rf"\b(?:theorem|lemma|def)\s+{_re.escape(target_name)}\b", existing):
        n = 2
        while _re.search(rf"\b(?:theorem|lemma|def)\s+{_re.escape(target_name)}_{n}\b", existing):
            n += 1
        new_name = f"{target_name}_{n}"
        clean_code = _re.sub(
            rf"\b(theorem|lemma|def)\s+{_re.escape(target_name)}\b",
            rf"\1 {new_name}",
            clean_code,
            count=1,
        )
        console.print(f"[yellow]Name '{target_name}' already exists, using '{new_name}'[/]")
        target_name = new_name

    new_content = existing.rstrip() + "\n\n" + clean_code + "\n"
    target_file = _accept_generated_source(
        lean_root,
        target_file,
        new_content,
        expected_content=existing if target_file.exists() else None,
    )
    console.print(f"[green]Accepted {target_file}[/]\n")

    # Step 3: Run agent on ONLY this target (not all sorries)
    attempts_label = "unlimited" if max_attempts == 0 else str(max_attempts)
    console.print(f"[bold]Attempting proof ({attempts_label} attempts)...[/]\n")
    agent = _agent_for(
        program,
        model=model,
        backend=backend,
        verbose=True,
        target_filter=target_name,
        target_file=target_file,
    )
    agent.config.max_cycles = max_attempts  # 0 = unlimited
    if max_attempts > 0:
        agent.config.max_retries_per_sorry = max_attempts
    _run_agent(agent)


# ---------------------------------------------------------------------------
# Paper extraction and formalization
# ---------------------------------------------------------------------------


def _prepare_paper(
    source: str,
    *,
    pages: str | None,
    extract_only: bool,
    output: Path | None,
    model: str | None,
    backend: str | None,
    program: Path,
) -> tuple[Path | None, ProgramConfig]:
    """Extract, display, and optionally formalize one paper."""
    import re as _re

    from autolean.paper import (
        analyze_paper_structure,
        extract_claims_via_llm,
        formalize_claim,
        read_paper,
        read_paper_text,
        render_verification_source,
    )
    from autolean.program import parse_program

    cfg = parse_program(program)
    llm: LLMBackend | None = None
    extracted_input_sha256 = ""

    def connected_llm() -> LLMBackend:
        nonlocal llm
        if llm is None:
            llm = _connected_llm(model, backend, cfg, timeout=600.0)
        return llm

    console.print(f"[bold]Analyzing paper: {source}[/]\n")
    try:
        try:
            claims, paper_title = read_paper(source, pages=pages)
        except (OSError, ValueError, RuntimeError) as e:
            raise click.ClickException(f"Paper extraction failed: {e}") from e

        if not claims:
            console.print("[bold]Using model-based extraction fallback...[/]")
            try:
                text, paper_title = read_paper_text(source, pages=pages)
            except (OSError, ValueError, RuntimeError) as e:
                raise click.ClickException(f"Paper text extraction failed: {e}") from e
            if text and len(text.strip()) > 100:
                from autolean.provenance import sha256_text

                extracted_input_sha256 = sha256_text(text)
                claims = extract_claims_via_llm(text, connected_llm().generate)

        if not claims:
            raise click.ClickException("No claims were extracted; select a page range or another source.")
        if extracted_input_sha256:
            for claim in claims:
                claim.input_ref = source
                claim.input_sha256 = extracted_input_sha256

        structure = analyze_paper_structure(claims)
        console.print(f"\n[bold]Found {len(claims)} claims:[/]")
        for kind, count in sorted(structure["by_kind"].items()):
            console.print(f"  {kind}: {count}")
        console.print()
        for index, claim in enumerate(claims, 1):
            proof_marker = " [dim](has proof)[/]" if claim.proof_sketch else ""
            console.print(f"  {index}. [bold]{claim.label}[/]: {claim.statement[:100]}...{proof_marker}")

        if extract_only:
            return None, cfg

        to_formalize = [claim for claim in claims if claim.kind != "remark"]
        console.print(f"\n[bold]Formalizing {len(to_formalize)} claims...[/]")
        formalizer = connected_llm()
        for claim in to_formalize:
            with console.status(f"[dim]Formalizing {claim.label}..."):
                formalize_claim(claim, formalizer.generate)
            if claim.lean_code:
                console.print(f"  [green]OK[/] {claim.label} -> {claim.lean_name}")
            else:
                console.print(f"  [yellow]SKIP[/] {claim.label}")

        formalized = sum(bool(claim.lean_code) for claim in to_formalize)
        if formalized == 0:
            raise click.ClickException("No claims could be formalized.")

        safe_title = _re.sub(
            r"[^a-zA-Z0-9_]",
            "_",
            (paper_title or "Untitled").replace(" ", "_"),
        )
        lean_root = program.parent / cfg.lean_project_path
        if output is None:
            output_path = lean_root / "AutoLean" / f"Paper_{safe_title}.lean"
        elif output.is_absolute():
            output_path = output
        else:
            output_path = lean_root / output
        content = render_verification_source(
            to_formalize,
            paper_title=paper_title,
        )
        output_path = _accept_generated_source(
            lean_root,
            output_path,
            content,
            timeout=300,
        )
        console.print(f"\n[bold green]Accepted {output_path}[/]")
        console.print(f"  {formalized} declarations ready for proving")
        return output_path, cfg
    finally:
        if llm is not None:
            llm.close()


@main.command()
@click.argument("source")
@click.option("--pages", type=str, default=None, help="Page range (e.g., '1-5').")
@click.option(
    "--max-cycles",
    type=click.IntRange(min=0),
    default=20,
    help="Proof attempts after formalization.",
)
@model_option
@backend_option
@program_option
def verify(
    source: str,
    pages: str | None,
    max_cycles: int,
    model: str | None,
    backend: str | None,
    program: Path,
) -> None:
    """Extract, formalize, and attempt the claims in a paper."""
    output, _ = _prepare_paper(
        source,
        pages=pages,
        extract_only=False,
        output=None,
        model=model,
        backend=backend,
        program=program,
    )
    if output is None:  # pragma: no cover - fixed by extract_only=False
        raise click.ClickException("Paper formalization produced no output file.")
    if max_cycles == 0:
        return

    console.print(f"\n[bold]Attempting proofs ({max_cycles} cycles)...[/]\n")
    agent = _agent_for(
        program,
        model=model,
        backend=backend,
        verbose=True,
        target_file=output,
    )
    agent.config.max_cycles = max_cycles
    _run_agent(agent)


@main.command("verify-paper", hidden=True)
@click.argument("source")
@click.option("--pages", type=str, default=None, help="Page range (e.g., '1-5').")
@click.option("--extract-only", is_flag=True, help="Extract claims only.")
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Output Lean file.",
)
@model_option
@backend_option
@program_option
def verify_paper(
    source: str,
    pages: str | None,
    extract_only: bool,
    output: Path | None,
    model: str | None,
    backend: str | None,
    program: Path,
) -> None:
    """Extract or formalize the claims in a paper."""
    artifact, _ = _prepare_paper(
        source,
        pages=pages,
        extract_only=extract_only,
        output=output,
        model=model,
        backend=backend,
        program=program,
    )
    if artifact is not None:
        console.print("\n  Next: [cyan]uv run autolean solve[/] to attempt proofs")


# ---------------------------------------------------------------------------
# init — scaffold a new AutoLean project
# ---------------------------------------------------------------------------


def _lean_project_name(path: Path) -> str:
    name = re.sub(r"[^A-Za-z0-9_]", "_", path.name).strip("_")
    name = re.sub(r"_+", "_", name) or "AutoLeanProject"
    return f"Project_{name}" if name[0].isdigit() else name


def _render_lakefile(project_name: str, *, mathlib: bool, cslib: bool) -> str:
    content = (
        "import Lake\n"
        "open Lake DSL\n\n"
        f"package {project_name.lower()} where\n"
        "  leanOptions := #[\n    ⟨`autoImplicit, false⟩\n  ]\n\n"
    )
    if mathlib:
        content += (
            "require mathlib from git\n"
            '  "https://github.com/leanprover-community/mathlib4" '
            f'@ "{LEAN_LIBRARY_RELEASE}"\n\n'
        )
    if cslib:
        content += (
            f'require cslib from git\n  "https://github.com/leanprover/cslib" @ "{LEAN_LIBRARY_RELEASE}"\n\n'
        )
    return content + f'@[default_target]\nlean_lib {project_name} where\n  srcDir := "."\n'


def _render_example(project_name: str, *, mathlib: bool, cslib: bool) -> str:
    content = (
        f"/-! # {project_name} — AutoLean Project\n\nEdit this file and add theorems with `sorry`.\n-/\n\n"
    )
    imports = [name for enabled, name in ((mathlib, "Mathlib"), (cslib, "Cslib")) if enabled]
    if imports:
        content += "".join(f"import {name}\n" for name in imports) + "\n"
    return content + (
        "-- Example: replace sorry with a proof\n"
        "theorem example_1 : 1 + 1 = 2 := by\n  sorry\n\n"
        "theorem example_2 (P : Prop) (h : P) : P := by\n  sorry\n"
    )


def _create_program(path: Path) -> bool:
    content = (
        "# AutoLean Program\n\n"
        "## Mode\n\n"
        "sorry-elimination\n\n"
        "## Lean Project Path\n\n"
        f"{path}\n\n"
        "## LLM Configuration\n\n"
        "model: opus\n"
        "effort: high\n"
        "max_output_tokens: 32768\n"
        "max_retries_per_sorry: 5\n"
        "cycle_timeout_seconds: 120\n"
        "max_cycles: 0\n"
    )
    try:
        with Path("program.md").open("x", encoding="utf-8") as handle:
            handle.write(content)
    except FileExistsError:
        return False
    return True


@main.command()
@click.argument("path", type=click.Path(path_type=Path))
@click.option(
    "--mathlib/--no-mathlib",
    default=True,
    help="Include the pinned Mathlib proof environment.",
)
@click.option(
    "--cslib/--no-cslib",
    default=True,
    help="Include the pinned computer-science library.",
)
@click.option("--toolchain", default=DEFAULT_LEAN_TOOLCHAIN, help="Lean toolchain.")
def init(path: Path, mathlib: bool, cslib: bool, toolchain: str) -> None:
    """Initialize a new Lean project for AutoLean.

    \b
    Creates:
      <path>/lakefile.lean
      <path>/lean-toolchain
      <path>/MyProject.lean (with example sorry targets)
      program.md (in current directory)
    """
    path = path.resolve()
    if path.exists() and not path.is_dir():
        raise click.ClickException(f"Project path is not a directory: {path}")

    project_name = _lean_project_name(path)

    managed_files = (
        path / "lean-toolchain",
        path / "lakefile.lean",
        path / f"{project_name}.lean",
    )
    conflicts = [candidate.name for candidate in managed_files if candidate.exists()]
    if conflicts:
        joined = ", ".join(conflicts)
        raise click.ClickException(f"Refusing to overwrite existing project files: {joined}")
    path.mkdir(parents=True, exist_ok=True)

    (path / "lean-toolchain").write_text(f"{toolchain}\n", encoding="utf-8")
    (path / "lakefile.lean").write_text(
        _render_lakefile(project_name, mathlib=mathlib, cslib=cslib),
        encoding="utf-8",
    )
    (path / f"{project_name}.lean").write_text(
        _render_example(project_name, mathlib=mathlib, cslib=cslib),
        encoding="utf-8",
    )
    program_created = _create_program(path)

    console.print(f"[green]Initialized AutoLean project at {path}[/]")
    libraries = [name for enabled, name in ((mathlib, "Mathlib"), (cslib, "CSLib")) if enabled]
    console.print(f"  lakefile.lean ({', '.join(libraries) or 'Lean core'})")
    if cslib:
        console.print("  CSLib: enabled")
    console.print(f"  lean-toolchain: {toolchain}")
    console.print(f"  {project_name}.lean (2 example sorry targets)")
    if program_created:
        console.print("  program.md (created)")
    else:
        console.print("  program.md (preserved; update Lean Project Path to select this project)")
    console.print("\n  Next:")
    console.print(f"    cd {path} && lake update && lake exe cache get && lake build")
    console.print(f"    uv run autolean targets -d {path}")
    console.print("    uv run autolean solve")


# ---------------------------------------------------------------------------
# changes — show what the agent has changed
# ---------------------------------------------------------------------------


@main.command("changes")
@click.option(
    "--project",
    "-d",
    type=click.Path(exists=True, path_type=Path),
    default="workspace",
    help="Path to Lean project root.",
)
def diff(project: Path) -> None:
    """Show what the agent has changed (git diff of .lean files)."""
    project = project.resolve()

    try:
        result = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "diff", "--stat", "--", "*.lean"],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise click.ClickException(f"Could not inspect Git changes: {e}") from e
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise click.ClickException(f"Git diff failed: {detail or 'no detail'}")
    if result.stdout.strip():
        console.print("[bold]Uncommitted Lean changes:[/]\n")
        console.print(result.stdout)
    else:
        console.print("[dim]No uncommitted Lean changes.[/]")

    try:
        log_result = subprocess.run(
            [
                "git",
                "-c",
                "core.fsmonitor=false",
                "log",
                "-n",
                "50",
                "--format=%s",
                "--grep=^proof: Prove ",
            ],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise click.ClickException(f"Could not inspect proof history: {e}") from e
    if log_result.returncode != 0:
        detail = (log_result.stderr or log_result.stdout).strip()
        raise click.ClickException(f"Git log failed: {detail or 'no detail'}")
    proved = [entry for entry in log_result.stdout.strip().split("\n") if entry]
    if proved:
        console.print(f"\n[bold green]{len(proved)} recent proofs:[/]")
        for entry in proved:
            name = entry.removeprefix("proof: Prove ")
            console.print(f"  [green]OK[/] {name}")


# ---------------------------------------------------------------------------
# export-training — export collected training data
# ---------------------------------------------------------------------------


@main.command("export-training")
@click.option(
    "--project",
    "-d",
    type=click.Path(exists=True, path_type=Path),
    default="workspace",
    help="Path to Lean project root.",
)
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
    project = project.resolve()

    # Read from existing training data files
    td = project / "training_data"
    if not td.exists() or not any(td.glob("*.jsonl")):
        console.print("[yellow]No training data found. Run the agent first:[/]")
        console.print("  uv run autolean solve --max-cycles 20")
        return

    # Show existing files
    console.print("[bold]Training data files:[/]\n")
    for f in sorted(td.glob("*.jsonl")):
        with open(f, encoding="utf-8") as handle:
            lines = sum(1 for _ in handle)
        size = f.stat().st_size / 1024
        console.print(f"  {f.name} ({lines} examples, {size:.1f} KB)")

    # Show stats
    console.print("\n[bold]Usage:[/]")
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
@click.option(
    "--project",
    "-d",
    type=click.Path(exists=True, path_type=Path),
    default="workspace",
    help="Path to Lean project root.",
)
@click.option("--model", "-m", default="google/gemma-4-E2B", help="Base model for fine-tuning.")
@click.option(
    "--framework",
    type=click.Choice(["unsloth", "axolotl", "trl"]),
    default="axolotl",
    help="Training framework.",
)
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
                "q_proj",
                "k_proj",
                "v_proj",
                "o_proj",
                "gate_proj",
                "up_proj",
                "down_proj",
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
        config_path.write_text(
            yaml.dump(config, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
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
        config_path.write_text(
            yaml.dump(config, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        console.print(f"[green]Generated Unsloth config:[/] {config_path}")
        console.print("\n  pip install unsloth")
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
        config_path.write_text(
            yaml.dump(config, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        console.print(f"[green]Generated TRL DPO config:[/] {config_path}")
        console.print("\n  pip install trl")
        console.print(f"  Use DPOTrainer with config from {config_path}")

    # Summary
    console.print("\n[bold]Self-improving loop:[/]")
    console.print("  1. Run agent:      uv run autolean solve --overnight")
    console.print("  2. Export data:     uv run autolean export-training")
    console.print(f"  3. Fine-tune:      {framework} train ...")
    console.print("  4. Import model:   ollama create autolean-v1 -f Modelfile")
    console.print("  5. Run again:      uv run autolean solve --model autolean-v1")


# ---------------------------------------------------------------------------
# build-library — create missing types/structures for a mathematical field
# ---------------------------------------------------------------------------


@main.command("build-library")
@click.argument("topic")
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Output path inside the configured Lean project.",
)
@model_option
@backend_option
@click.option("--prove", is_flag=True, help="Immediately attempt proofs after generating.")
@program_option
def build_library(
    topic: str,
    output: Path | None,
    model: str | None,
    backend: str | None,
    prove: bool,
    program: Path,
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

    from autolean.library import generate_library_source
    from autolean.program import parse_program

    cfg = parse_program(program)
    llm = _connected_llm(model, backend, cfg, timeout=600.0)

    # Generate output path
    safe_topic = _re.sub(r"[^a-zA-Z0-9]", "", topic.title().replace(" ", ""))
    lean_root = program.parent / cfg.lean_project_path
    if output is None:
        output = lean_root / "AutoLean" / f"Lib{safe_topic}.lean"
    elif not output.is_absolute():
        output = lean_root / output

    console.print(f"[bold]Building library for:[/] {topic}")
    console.print(f"[bold]Output:[/] {output}\n")

    try:
        with console.status(f"[dim]Generating {topic} library..."):
            content = generate_library_source(topic, llm.generate)
    except LLMError as e:
        raise click.ClickException(str(e)) from e
    finally:
        llm.close()
    path = _accept_generated_source(lean_root, output, content, timeout=300)

    from autolean.scanner import count_sorries

    # Count generated declarations and proof targets.
    n_defs = len(_re.findall(r"\b(?:def|structure|class|instance|theorem|lemma)\b", content))
    n_sorrys = count_sorries(content)

    console.print(f"[green]Generated {n_defs} definitions/theorems ({n_sorrys} sorry targets)[/]")
    console.print(f"  File: {path}\n")

    # Show preview
    for line in content.split("\n")[:20]:
        console.print(f"  [dim]{line}[/]")
    if len(content.split("\n")) > 20:
        console.print(f"  [dim]... ({len(content.split(chr(10))) - 20} more lines)[/]")

    if prove and n_sorrys > 0:
        console.print(f"\n[bold]Attempting to prove {n_sorrys} sorry targets...[/]\n")
        agent = _agent_for(
            program,
            model=model,
            backend=backend,
            verbose=True,
            target_file=path,
        )
        agent.config.max_cycles = n_sorrys * 3
        _run_agent(agent)
    elif n_sorrys > 0:
        console.print(f"\n  Next: [cyan]uv run autolean solve[/] to attempt {n_sorrys} proofs")


# ---------------------------------------------------------------------------
# improve — simplify/deepen/beautify an existing proof
# ---------------------------------------------------------------------------


@main.command()
@click.argument("file_path", type=click.Path(exists=True, path_type=Path))
@click.argument("theorem_name")
@click.option(
    "--goal",
    type=click.Choice(["shorter", "elegant", "faster", "readable"]),
    default="elegant",
    help="What to optimize for.",
)
@model_option
@backend_option
@click.option(
    "--max-attempts",
    type=click.IntRange(min=1),
    default=5,
    help="Max improvement attempts.",
)
@program_option
def improve(
    file_path: Path,
    theorem_name: str,
    goal: str,
    model: str | None,
    backend: str | None,
    max_attempts: int,
    program: Path,
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

    from autolean.lean_interface import LeanProject
    from autolean.program import parse_program
    from autolean.prompts import PROOF_GOLF_USER

    cfg = parse_program(program)
    file_path = file_path.resolve()

    # Find the theorem and its proof in the file
    content = file_path.read_text(encoding="utf-8")
    lines = content.split("\n")

    # Locate the theorem declaration
    theorem_line = None
    for i, line in enumerate(lines):
        if re.search(rf"\b{re.escape(theorem_name)}\b", line) and re.match(
            r"\s*(theorem|lemma|def)\s+", line
        ):
            theorem_line = i
            break

    if theorem_line is None:
        raise click.ClickException(f"Theorem '{theorem_name}' was not found in {file_path}.")

    # The current proof ends at the next declaration.
    proof_start = None
    proof_end = None
    for i in range(theorem_line, len(lines)):
        if "by" in lines[i] and proof_start is None:
            proof_start = i + 1
        elif proof_start is not None and i > proof_start:
            stripped = lines[i].strip()
            unindented = not lines[i].startswith((" ", "\t"))
            if stripped and not stripped.startswith("--") and unindented:
                proof_end = i
                break
    if proof_start is None:
        raise click.ClickException(f"No tactic proof found for '{theorem_name}'.")
    if proof_end is None:
        proof_end = len(lines)

    current_proof = "\n".join(lines[proof_start:proof_end])
    decl_line = "\n".join(lines[theorem_line:proof_start])

    console.print(f"[bold]Improving:[/] {theorem_name}")
    console.print(f"[bold]Goal:[/] {goal}")
    console.print("[bold]Current proof:[/]")
    for line in current_proof.split("\n")[:10]:
        console.print(f"  [dim]{line}[/]")
    console.print()

    # Find project root
    lean_root = file_path.parent
    while lean_root != lean_root.parent:
        if (lean_root / "lakefile.lean").exists() or (lean_root / "lakefile.toml").exists():
            break
        lean_root = lean_root.parent
    project = LeanProject(lean_root)
    try:
        proof_environment = project.proof_environment(refresh=True)
    except (OSError, ProofEnvironmentError) as e:
        raise click.ClickException(f"Proof environment identification failed: {e}") from e

    from autolean.scanner import _find_enclosing_decl_details, _mask_lean_noncode

    local_name, qualified_name, _ = _find_enclosing_decl_details(
        _mask_lean_noncode(content).split("\n"),
        theorem_line + 1,
    )
    if local_name != theorem_name or not qualified_name:
        raise click.ClickException(f"Theorem '{theorem_name}' has no auditable source name.")

    import textwrap

    proof_indents = [len(line) - len(line.lstrip()) for line in lines[proof_start:proof_end] if line.strip()]
    if not proof_indents:
        raise click.ClickException(f"No multiline tactic proof found for '{theorem_name}'.")
    proof_indent = " " * min(proof_indents)

    goal_prompts = {
        "shorter": "Make this proof as SHORT as possible. Minimize the number of tactics and lines.",
        "elegant": "Make this proof more ELEGANT and mathematically beautiful. Use clean, idiomatic Lean 4.",
        "faster": "Make this proof FASTER for the Lean kernel to check. "
        "Avoid slow tactics like simp on large goals.",
        "readable": "Make this proof more READABLE. Use descriptive names, add comments, structure clearly.",
    }

    system = (
        "You are a Lean 4 proof golf expert. "
        f"{goal_prompts[goal]} "
        "Output ONLY the improved tactic block. No explanation, no markdown."
    )

    from autolean.agent import clean_llm_proof
    from autolean.generated_code import GeneratedCodeError, validate_generated_proof
    from autolean.provenance import sha256_text

    with _connected_llm(model, backend, cfg) as llm:
        for attempt in range(1, max_attempts + 1):
            console.print(f"[bold]Attempt {attempt}/{max_attempts}...[/]")

            context = f"{decl_line}\n{current_proof}"
            user_prompt = PROOF_GOLF_USER.format(
                file_context=context,
                decl_name=theorem_name,
                line=theorem_line + 1,
                current_proof=current_proof,
            )

            try:
                with console.status("[dim]Generating improved proof..."):
                    response = llm.generate(system, user_prompt)
            except LLMError as e:
                raise click.ClickException(f"Proof improvement failed: {e}") from e

            new_proof = clean_llm_proof(response.text, tactic_mode=True)
            try:
                new_proof = validate_generated_proof(new_proof)
            except GeneratedCodeError as e:
                console.print(f"  [red]Generated proof rejected:[/] {e}")
                continue

            if not new_proof or textwrap.dedent(new_proof).strip() == textwrap.dedent(current_proof).strip():
                console.print("  [yellow]No improvement generated.[/]")
                continue

            console.print("  [cyan]New proof:[/]")
            for line in new_proof.split("\n")[:8]:
                console.print(f"    [cyan]{line}[/]")

            normalized_lines = textwrap.dedent(new_proof).strip("\n").split("\n")
            replacement = [f"{proof_indent}{line}" if line else "" for line in normalized_lines]
            new_lines = lines.copy()
            new_lines[proof_start:proof_end] = replacement
            new_content = "\n".join(new_lines)

            with console.status("[dim]Verifying proof and axioms..."):
                build = project.validate_candidate(
                    file_path,
                    new_content,
                    timeout=120,
                    declaration=qualified_name,
                    declaration_line=theorem_line + 1,
                    expected_environment=proof_environment.sha256,
                )

            if build.success:
                try:
                    project.write_file(
                        file_path,
                        new_content,
                        expected_content=content,
                    )
                except OSError as e:
                    raise click.ClickException(f"Source changed during proof improvement: {e}") from e
                old_len = len(current_proof.strip().split("\n"))
                new_len = len(normalized_lines)
                axioms = ", ".join(build.axioms) if build.axioms else "none"
                console.print(f"  [bold green]Improved![/] {old_len} lines -> {new_len} lines")
                console.print(f"  Environment: sha256:{proof_environment.sha256}")
                console.print(f"  Proof:       sha256:{sha256_text(new_proof)}")
                console.print(f"  Axioms:      {axioms}")
                if new_len < old_len:
                    pct = (old_len - new_len) / old_len * 100
                    console.print(f"  [green]Reduced by {old_len - new_len} lines ({pct:.0f}%)[/]")
                return

            detail = build.errors[0].message if build.errors else build.stderr or "unknown error"
            console.print(f"  [red]Build failed:[/] {' '.join(detail.split())[:160]}")

    raise click.ClickException(f"Could not improve after {max_attempts} attempts.")


# ---------------------------------------------------------------------------
# challenge — attempt an open mathematical problem
# ---------------------------------------------------------------------------


@main.command()
@click.argument("problem_id", required=False)
@click.option("--field", type=str, default=None, help="Filter by field (e.g., 'number theory').")
@click.option(
    "--difficulty",
    type=click.Choice(["accessible", "hard", "very-hard", "millennium"]),
    default=None,
    help="Filter by difficulty.",
)
@click.option(
    "--max-cycles",
    type=click.IntRange(min=0),
    default=50,
    help="Max proof attempts (0 = unlimited).",
)
@model_option
@backend_option
@click.option(
    "--program",
    "-p",
    type=click.Path(exists=True, path_type=Path),
    default="program.md",
    help="Path to program.md.",
)
def challenge(
    problem_id: str | None,
    field: str | None,
    difficulty: str | None,
    max_cycles: int,
    model: str | None,
    backend: str | None,
    program: Path,
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
        OPEN_PROBLEMS,
        print_problems_table,
        render_challenge_source,
    )
    from autolean.program import parse_program

    if problem_id is None:
        print_problems_table(filter_field=field, filter_difficulty=difficulty)
        return

    # Find the problem
    problem = next((p for p in OPEN_PROBLEMS if p.id == problem_id), None)
    if not problem:
        # Try partial match
        needle = problem_id.lower()
        matches = [p for p in OPEN_PROBLEMS if needle in p.id.lower() or needle in p.name.lower()]
        if len(matches) == 1:
            problem = matches[0]
        elif matches:
            console.print(f"[yellow]Multiple matches for '{problem_id}':[/]")
            for m in matches:
                console.print(f"  {m.id}: {m.name}")
            raise click.ClickException(f"Problem ID '{problem_id}' is ambiguous.")
        else:
            raise click.ClickException(
                f"Problem '{problem_id}' was not found; run `autolean challenge` to list IDs."
            )

    # Display problem info
    diff_colors = {"accessible": "green", "hard": "yellow", "very-hard": "red", "millennium": "bold magenta"}
    dc = diff_colors.get(problem.difficulty, "white")

    console.print(
        Panel(
            f"[bold]{problem.name}[/bold]\n"
            f"Field:      {problem.field}\n"
            f"Difficulty: [{dc}]{problem.difficulty}[/{dc}]\n"
            f"Formalization: {problem.formalization_status}\n"
            f"\n{problem.description}\n"
            + (f"\nBoundary: {problem.limitations}" if problem.limitations else "")
            + (f"\nSub-results: {len(problem.sub_results)} provable lemma(s)" if problem.sub_results else "")
            + (f"\nRef: {problem.references[0]}" if problem.references else ""),
            title=f"Challenge: {problem.id}",
            border_style=dc.split()[-1] if " " in dc else dc,
            width=75,
        )
    )

    if problem.formalization_status != "formalized":
        raise click.ClickException(
            "This challenge is a formalization scaffold. A source-faithful Lean "
            "statement is required before proof attempts can start."
        )

    # Generate the challenge file
    cfg = parse_program(program)
    lean_root = program.parent / cfg.lean_project_path
    filename = f"Challenge_{problem.id.replace('-', '_').title()}.lean"
    path = lean_root / "AutoLean" / filename
    path = _accept_generated_source(
        lean_root,
        path,
        render_challenge_source(problem),
        timeout=300,
    )
    console.print(f"\n[green]Accepted:[/] {path}")

    # Count sorry targets
    from autolean.scanner import count_sorries

    n_sorry = count_sorries(path.read_text(encoding="utf-8"))
    console.print(f"[cyan]{n_sorry} auditable sorry target(s)[/]")

    # Ask if they want to start proving
    console.print(f"\n[bold]Starting proof attempts ({max_cycles} cycles)...[/]\n")

    agent = _agent_for(
        program,
        model=model,
        backend=backend,
        verbose=True,
        target_file=path,
    )
    agent.config.max_cycles = max_cycles
    _run_agent(agent)


# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
