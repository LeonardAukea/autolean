"""CLI entry point — `uv run autolean` or `python -m autolean`."""

from __future__ import annotations

import re
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import click
from rich.panel import Panel
from rich.text import Text

from autolean import __version__, cli_runtime, ui
from autolean.cli_sessions import register_commands as _register_session_commands
from autolean.cli_workflows import register_commands as _register_workflow_commands
from autolean.llm import LLMBackend, LLMError
from autolean.provenance import ProofEnvironmentError
from autolean.ui import console

if TYPE_CHECKING:
    from autolean.lean_interface import LeanProject
    from autolean.paper import PreparedPaper
    from autolean.program import ProgramConfig
    from autolean.provenance import ProofEnvironment
    from autolean.strategy import PlanAttempt, ProofPlan


_accept_generated_source = cli_runtime.accept_generated_source
_agent_for = cli_runtime.agent_for
_configure_escalation = cli_runtime.configure_escalation
_connected_llm = cli_runtime.connected_llm
_llm_for = cli_runtime.llm_for
_run_agent = cli_runtime.run_agent
_run_session_agent = cli_runtime.run_session_agent
backend_option = cli_runtime.backend_option
escalation_options = cli_runtime.escalation_options
model_option = cli_runtime.model_option
paddleocr_url_option = cli_runtime.paddleocr_url_option
pdf_engine_option = cli_runtime.pdf_engine_option
program_option = cli_runtime.program_option

AUTOLEAN_BANNER = r"""
 ______     __  __     ______   ______     __         ______     ______     __   __
/\  __ \   /\ \/\ \   /\__  _\ /\  __ \   /\ \       /\  ___\   /\  __ \   /\ "-.\ \
\ \  __ \  \ \ \_\ \  \/_/\ \/ \ \ \/\ \  \ \ \____  \ \  __\   \ \  __ \  \ \ \-.  \
 \ \_\ \_\  \ \_____\    \ \_\  \ \_____\  \ \_____\  \ \_____\  \ \_\ \_\  \ \_\\"\_\
  \/_/\/_/   \/_____/     \/_/   \/_____/   \/_____/   \/_____/   \/_/\/_/   \/_/ \/_/
""".strip("\n")

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
    (
        "Proof workflows",
        (
            "plan",
            "prove",
            "solve",
            "resume",
            "verify",
            "problems",
        ),
    ),
    ("Understand", ("sessions", "targets", "inspect")),
    ("Project", ("doctor", "models", "init", "export")),
)


class AutoLeanGroup(click.Group):
    """Click group with stable aliases and task-oriented help sections."""

    def format_help(self, ctx: click.Context, formatter: click.HelpFormatter) -> None:
        if ctx.parent is None:
            banner = click.style(AUTOLEAN_BANNER, fg="cyan", bold=True)
            formatter.write(f"{banner}\n\n")
        super().format_help(ctx, formatter)

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
      autolean problems work collatz    # continue an open-problem workspace
      autolean verify <arxiv-url>       # verify a paper
      autolean solve                    # prove all sorry targets

    \b
    Open problems:
      autolean problems                  # list the curated catalog
      autolean problems suggest          # choose bounded, formalized work
      autolean problems work goldbach    # create or continue a workspace
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
@escalation_options
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
    escalation: str | None,
    escalate_to: str | None,
    escalate_after: int | None,
    max_cycles: int | None,
    resume: bool,
    overnight: bool,
    target: str | None,
) -> None:
    """Start the autonomous proof agent loop.

    \b
    Runs proof experiments until all targets are proved or you press
    Ctrl+C. Use --max-cycles to set a limit. The experiment cycle is
    described in docs/explanation/research-loop.md.

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
    _configure_escalation(
        agent,
        escalation=escalation,
        escalate_to=escalate_to,
        escalate_after=escalate_after,
    )

    if max_cycles is not None:
        agent.config.max_cycles = max_cycles
    if overnight:
        agent.config.max_cycles = 0  # unlimited; the loop resets epochs itself
        agent.config.max_retries_per_sorry = 100
        agent.resume = True

    if dry_run:
        _run_agent(agent)
        return

    from autolean.session import SessionKind, SessionStore

    store = SessionStore(agent.project.root)
    # `--overnight` resumes, so the session it continues is the one the
    # earlier night left behind.
    session = (
        store.find_workflow(
            SessionKind.PROJECT,
            target_filter=target or "",
        )
        if agent.resume
        else None
    )
    if session is None:
        title = f"Project target {target}" if target else f"Project {agent.project.root.name}"
        session = store.create(
            kind=SessionKind.PROJECT,
            title=title,
            model=agent.llm.config.model,
            backend=agent.llm.config.backend,
            max_cycles=agent.config.max_cycles,
            escalation_policy=agent.config.escalation_policy,
            escalation_model=agent.config.escalation_model or "",
            escalation_after_failures=agent.config.escalation_after_failures,
            target_filter=target or "",
            guidance=tuple(agent.config.strategy_hints),
        )
    else:
        session = store.save(
            session.update(
                model=agent.llm.config.model,
                backend=agent.llm.config.backend,
                max_cycles=agent.config.max_cycles,
                escalation_policy=agent.config.escalation_policy,
                escalation_model=agent.config.escalation_model or "",
                escalation_after_failures=agent.config.escalation_after_failures,
            )
        )
    _run_session_agent(agent, store, session)


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
        ui.fail(f"Preflight: {error}")
        return f"backend preflight: {error}"
    if ready:
        ui.ok("Preflight passed")
        return None
    ui.fail("Backend preflight did not pass — see `autolean models`.")
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
    from rich.markup import escape

    ui.ok(f"Response: {escape(response.text[:60])}")
    proof = validate_generated_proof(clean_llm_proof(response.text, tactic_mode=True))
    ui.ok(f"Proof SHA-256: {sha256_text(proof)}")
    if llm.capabilities.token_counts and response.output_tokens:
        ui.ok(
            f"{response.output_tokens} tokens "
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
    try:
        llm = _llm_for(model, backend, config)
    except (LLMError, ValueError) as error:
        ui.fail(f"Configuration: {error}")
        return "", [f"model configuration: {error}"]
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
            ui.fail(f"Generation: {error}")
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
        ui.ok(f"Model proof passed sandboxed Lean ({smoke.duration_seconds:.1f}s)")
        return None
    from rich.markup import escape

    detail = smoke.stderr or (smoke.errors[0].message if smoke.errors else "Lean rejected the model proof")
    ui.fail(f"Model proof: {escape(detail[:300])}")
    return f"model proof validation: {detail[:300]}"


def _doctor_lean(program: Path, config: ProgramConfig, model_proof: str) -> list[str]:
    """Check the pinned proof environment, candidate, and trusted project."""
    from autolean.lean_interface import LeanProject

    failures: list[str] = []
    console.print("\n[bold]Checking Lean 4...[/]")
    lean_root = program.parent / config.lean_project_path
    try:
        project = LeanProject(lean_root)
        ui.ok(f"Project: {project.root}")
        ui.ok(f"Lean files: {len(project.lean_files())}")
        environment = project.proof_environment()
        ui.ok(f"Environment: sha256:{environment.sha256}")
        if model_proof:
            proof_failure = _doctor_validate_proof(project, environment, model_proof)
            if proof_failure:
                failures.append(proof_failure)
        with ui.status("Building Lean project..."):
            build = project.build(timeout=120)
        if build.success:
            ui.ok(f"Build succeeded ({build.duration_seconds:.1f}s)")
        else:
            failures.append("Lean project build failed")
            ui.fail(f"Build errors: {len(build.errors)}")
    except (FileNotFoundError, OSError, ProofEnvironmentError) as error:
        failures.append(f"Lean toolchain: {error}")
        ui.fail(str(error))
    return failures


def _doctor_research_tools() -> list[str]:
    """Report the paper and indexed-context tools used by research workflows."""
    from autolean.research_tools import research_tools

    failures: list[str] = []
    console.print("\n[bold]Checking research tools...[/]")
    for tool in research_tools():
        if tool.available:
            ui.ok(tool.identity)
        elif tool.required:
            failures.append(tool.identity)
            ui.fail(tool.identity)
        else:
            ui.warn(tool.identity)
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
    failures.extend(_doctor_research_tools())
    failures.extend(_doctor_lean(program, config, model_proof))

    if failures:
        raise click.ClickException("Checks failed: " + "; ".join(failures))


@main.command("environment", hidden=True)
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


@main.command("export")
@click.argument(
    "output",
    type=click.Path(path_type=Path, file_okay=False, resolve_path=True),
)
@click.option("--title", default="AutoLean proof artifact", show_default=True)
@click.option("--session", "session_id", default=None, help="Include one proof session record.")
@program_option
def export_command(output: Path, title: str, session_id: str | None, program: Path) -> None:
    """Export a standalone Lean project and companion LaTeX paper."""
    from autolean.export import ExportError, export_project, paper_bundle_from_artifacts
    from autolean.lean_interface import LeanProject
    from autolean.program import parse_program
    from autolean.session import SessionError, SessionKind, SessionStore

    config = parse_program(program)
    project = LeanProject(program.parent / config.lean_project_path)
    try:
        environment = project.proof_environment()
        store = SessionStore(project.root)
        proof_session = store.load(session_id) if session_id else None
        session = proof_session.as_dict() if proof_session is not None else None
        paper_bundle = None
        if proof_session is not None and proof_session.kind is SessionKind.PAPER:
            paper_bundle = paper_bundle_from_artifacts(store.artifact_paths(proof_session))
        result = export_project(
            project.root,
            output,
            title=title,
            environment_sha256=environment.sha256,
            session=session,
            paper_bundle=paper_bundle,
        )
    except (ExportError, OSError, ProofEnvironmentError, SessionError) as error:
        raise click.ClickException(str(error)) from error
    console.print(
        Panel(
            f"[bold green]Portable artifact created[/]\n"
            f"Path:       {result.path}\n"
            f"Lean files: {result.source_count}\n"
            f"Manifest:   sha256:{result.manifest_sha256}\n\n"
            "Build Lean:  cd project && lake build\n"
            "Build paper: cd paper && latexmk -xelatex main.tex\n"
            "Paper data:  source/ (paper sessions)",
            title="Export",
            border_style="green",
        )
    )


# ---------------------------------------------------------------------------
# results — show experiment log
# ---------------------------------------------------------------------------


@main.command(hidden=True)
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


def _show_proof_plan(plan: ProofPlan) -> None:
    """Display one reviewable proof plan and its content identity."""
    console.print(
        Panel(
            Text(plan.render()),
            title="Mathematical research plan",
            subtitle=f"sha256:{plan.sha256[:16]}",
            border_style="cyan",
        )
    )


def _proof_plan(
    statement: str,
    llm: LLMBackend,
    guidance: tuple[str, ...],
    *,
    context: str = "",
    on_response: Callable[[PlanAttempt], None] | None = None,
) -> ProofPlan:
    """Generate a plan and translate strategy errors for Click."""
    from autolean.strategy import ProofStrategyError, generate_proof_plan

    try:
        return generate_proof_plan(
            statement,
            llm.generate,
            guidance=guidance,
            context=context,
            on_response=on_response,
            on_repair=lambda attempt, error: console.print(
                f"[yellow]Strategy response rejected:[/] {error}\n"
                f"  Requesting bounded repair {attempt}/1 from {llm.config.model}."
            ),
        )
    except ProofStrategyError as error:
        raise click.ClickException(f"Could not form a proof strategy: {error}") from error


@main.command()
@click.argument("statement")
@model_option
@backend_option
@click.option(
    "--guide",
    multiple=True,
    help="Add a mathematical constraint or preferred method.",
)
@click.option("--json", "as_json", is_flag=True, help="Output canonical plan JSON.")
@program_option
def plan(
    statement: str,
    model: str | None,
    backend: str | None,
    guide: tuple[str, ...],
    as_json: bool,
    program: Path,
) -> None:
    """Develop a reviewable strategy for a mathematical statement."""
    from autolean.program import parse_program

    config = parse_program(program)
    with _connected_llm(model, backend, config) as llm:
        proof_plan = _proof_plan(statement, llm, guide)
    if as_json:
        click.echo(proof_plan.to_json())
    else:
        _show_proof_plan(proof_plan)


# ---------------------------------------------------------------------------
# prove — natural language theorem → formalize → prove
# ---------------------------------------------------------------------------


@main.command()
@click.argument("statement")
@model_option
@backend_option
@escalation_options
@click.option(
    "--max-attempts",
    type=click.IntRange(min=0),
    default=5,
    show_default=True,
    help="Max proof attempts (0 = unlimited).",
)
@click.option(
    "--formalization-repairs",
    type=click.IntRange(min=0, max=5),
    default=2,
    show_default=True,
    help="Compiler-guided repairs before proof search.",
)
@click.option(
    "--guide",
    multiple=True,
    help="Add a mathematical constraint or preferred method.",
)
@click.option(
    "--review-plan",
    is_flag=True,
    help="Review and optionally revise the strategy before formalization.",
)
@program_option
def prove(
    statement: str,
    model: str | None,
    backend: str | None,
    escalation: str | None,
    escalate_to: str | None,
    escalate_after: int | None,
    max_attempts: int,
    formalization_repairs: int,
    guide: tuple[str, ...],
    review_plan: bool,
    program: Path,
) -> None:
    """Prove a theorem from natural language.

    \b
    Takes a math statement in plain English, formalizes it as Lean 4,
    and attempts to prove it.

    \b
    Examples:
      autolean prove "1 + 1 = 2"
      autolean prove "for all n, n + 0 = n"
      autolean prove "if P implies Q and Q implies R then P implies R"
      autolean prove "the square root of 2 is irrational"
    """
    from autolean.challenges import match_open_problem

    if problem := match_open_problem(statement):
        from autolean.cli_workflows import challenge

        console.print(
            f"[yellow]Recognized curated open problem:[/] {problem.name}\n"
            "Opening its source-aware research workspace."
        )
        click.get_current_context().invoke(
            challenge,
            problem_id=problem.id,
            field=None,
            difficulty=None,
            max_cycles=max_attempts,
            model=model,
            backend=backend,
            escalation=escalation,
            escalate_to=escalate_to,
            escalate_after=escalate_after,
            guide=guide,
            program=program,
        )
        return

    from autolean.lean_interface import LeanProject
    from autolean.program import parse_program
    from autolean.theorem import FormalizationError, formalize_theorem, generated_theorem_path

    cfg = parse_program(program)
    lean_root = program.parent / cfg.lean_project_path
    project = LeanProject(lean_root)

    ui.phase("Plan")
    console.print(f"{statement}\n")
    with _connected_llm(model, backend, cfg) as llm:
        with ui.status(f"Consulting {llm.config.model}..."):
            proof_plan = _proof_plan(statement, llm, guide)
        _show_proof_plan(proof_plan)
        while review_plan and not click.confirm("Use this plan?", default=True):
            revision = click.prompt("Additional guidance", type=str).strip()
            with ui.status(f"Consulting {llm.config.model}..."):
                proof_plan = _proof_plan(statement, llm, (*guide, revision))
            _show_proof_plan(proof_plan)

        ui.phase("Formalize")
        try:
            with ui.status("Formalizing with the pinned Lean kernel..."):
                theorem = formalize_theorem(
                    statement,
                    proof_plan,
                    llm.generate,
                    project,
                    max_repairs=formalization_repairs,
                )
        except FormalizationError as error:
            raise click.ClickException(str(error)) from error

    console.print(
        f"[green]Formalization compiled[/] after {theorem.attempts} "
        f"attempt{'s' if theorem.attempts != 1 else ''}:"
    )
    console.print(Panel(Text(theorem.code), title="Lean 4 theorem", border_style="cyan"))

    target_file = generated_theorem_path(lean_root, theorem.declaration_name)
    target_file, _ = _accept_generated_source(
        lean_root,
        target_file,
        theorem.source,
    )
    console.print(f"[green]Accepted {target_file}[/]\n")

    attempts_label = "unlimited" if max_attempts == 0 else str(max_attempts)
    ui.phase(f"Prove ({attempts_label} attempts)")
    agent = _agent_for(
        program,
        model=model,
        backend=backend,
        verbose=True,
        target_filter=theorem.declaration_name,
        target_file=target_file,
    )
    _configure_escalation(
        agent,
        escalation=escalation,
        escalate_to=escalate_to,
        escalate_after=escalate_after,
    )
    agent.config.max_cycles = max_attempts
    if max_attempts > 0:
        agent.config.max_retries_per_sorry = max_attempts

    from autolean.session import SessionKind, SessionStore

    store = SessionStore(agent.project.root)
    session = store.create(
        kind=SessionKind.THEOREM,
        title=statement,
        model=agent.llm.config.model,
        backend=agent.llm.config.backend,
        max_cycles=max_attempts,
        escalation_policy=agent.config.escalation_policy,
        escalation_model=agent.config.escalation_model or "",
        escalation_after_failures=agent.config.escalation_after_failures,
        target_file=target_file,
        target_filter=theorem.declaration_name,
        guidance=guide,
    )
    _run_session_agent(agent, store, session)


# ---------------------------------------------------------------------------
# Paper extraction and formalization
# ---------------------------------------------------------------------------


def _prepare_paper(
    source: str,
    *,
    pages: str | None,
    pdf_engine: str,
    paddleocr_url: str | None,
    extract_only: bool,
    output: Path | None,
    model: str | None,
    backend: str | None,
    guide: tuple[str, ...],
    review_plan: bool,
    program: Path,
) -> tuple[PreparedPaper | None, ProgramConfig]:
    """Extract, review, and accept one paper through the shared workflow."""
    from autolean.paper_workflow import PaperServices, prepare_paper

    return prepare_paper(
        source,
        pages=pages,
        pdf_engine=pdf_engine,
        paddleocr_url=paddleocr_url,
        extract_only=extract_only,
        output=output,
        model=model,
        backend=backend,
        guide=guide,
        review_plan=review_plan,
        program=program,
        services=PaperServices(
            console=console,
            connect_llm=_connected_llm,
            plan_proof=_proof_plan,
            show_plan=_show_proof_plan,
            accept_source=_accept_generated_source,
        ),
    )


@main.command()
@click.argument("source")
@click.option("--pages", type=str, default=None, help="Page range (e.g., '1-5').")
@pdf_engine_option
@paddleocr_url_option
@click.option("--extract-only", is_flag=True, help="Extract claims without formalizing them.")
@click.option(
    "--formalize-only",
    is_flag=True,
    help="Write the Lean project without running proof search.",
)
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Output Lean file inside the configured project.",
)
@click.option(
    "--max-cycles",
    type=click.IntRange(min=0),
    default=5,
    show_default=True,
    help="Cycle budget after formalization (0 = unlimited).",
)
@click.option(
    "--guide",
    multiple=True,
    help="Add a mathematical constraint or preferred method.",
)
@click.option(
    "--review-plan",
    is_flag=True,
    help="Review and optionally revise the paper strategy before formalization.",
)
@model_option
@backend_option
@program_option
def verify(
    source: str,
    pages: str | None,
    pdf_engine: str,
    paddleocr_url: str | None,
    extract_only: bool,
    formalize_only: bool,
    output: Path | None,
    max_cycles: int,
    guide: tuple[str, ...],
    review_plan: bool,
    model: str | None,
    backend: str | None,
    program: Path,
) -> None:
    """Extract, formalize, and attempt the claims in a paper."""
    if extract_only and formalize_only:
        raise click.ClickException("Choose exactly one of --extract-only and --formalize-only.")
    prepared, config = _prepare_paper(
        source,
        pages=pages,
        pdf_engine=pdf_engine,
        paddleocr_url=paddleocr_url,
        extract_only=extract_only,
        output=output,
        model=model,
        backend=backend,
        guide=guide,
        review_plan=review_plan,
        program=program,
    )
    if extract_only:
        return
    if prepared is None:  # pragma: no cover - fixed by extract_only=False
        raise click.ClickException("Paper formalization produced no output file.")
    if formalize_only:
        console.print(f"\n[dim]Continue:[/] autolean solve --program {program}")
        return

    from autolean.scanner import count_sorries
    from autolean.session import SessionKind, SessionStatus, SessionStore

    artifact = prepared.lean_path
    paper_artifacts = (
        prepared.source.markdown_path,
        prepared.coverage_path,
        prepared.plan_path,
        *((prepared.source.pdf_path,) if prepared.source.pdf_path is not None else ()),
    )
    if count_sorries(artifact.read_text(encoding="utf-8")) == 0:
        lean_root = (program.parent / config.lean_project_path).resolve()
        store = SessionStore(lean_root)
        session = store.create(
            kind=SessionKind.PAPER,
            title=source,
            model=prepared.model,
            backend=prepared.backend,
            max_cycles=max_cycles,
            target_file=artifact,
            artifacts=paper_artifacts,
            guidance=guide,
        )
        finished = store.save(
            session.update(
                status=SessionStatus.COMPLETED,
                remaining_targets=0,
                message="Every reviewed paper item passed Lean elaboration.",
            )
        )
        console.print(
            f"\n[bold green]Paper session complete:[/] {finished.id}\n"
            f"  autolean export paper-artifact --session {finished.id}"
        )
        return

    cycles_label = "unlimited" if max_cycles == 0 else str(max_cycles)
    console.print(f"\n[bold]Attempting proofs ({cycles_label} cycles)...[/]\n")
    agent = _agent_for(
        program,
        model=model,
        backend=backend,
        verbose=True,
        target_file=artifact,
    )
    agent.config.max_cycles = max_cycles

    store = SessionStore(agent.project.root)
    session = store.create(
        kind=SessionKind.PAPER,
        title=source,
        model=agent.llm.config.model,
        backend=agent.llm.config.backend,
        max_cycles=max_cycles,
        escalation_policy=agent.config.escalation_policy,
        escalation_model=agent.config.escalation_model or "",
        escalation_after_failures=agent.config.escalation_after_failures,
        target_file=artifact,
        artifacts=paper_artifacts,
        guidance=guide,
    )
    _run_session_agent(agent, store, session)


@main.command("verify-paper", hidden=True)
@click.argument("source")
@click.option("--pages", type=str, default=None, help="Page range (e.g., '1-5').")
@pdf_engine_option
@paddleocr_url_option
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
    pdf_engine: str,
    paddleocr_url: str | None,
    extract_only: bool,
    output: Path | None,
    model: str | None,
    backend: str | None,
    program: Path,
) -> None:
    """Extract or formalize the claims in a paper."""
    prepared, _ = _prepare_paper(
        source,
        pages=pages,
        pdf_engine=pdf_engine,
        paddleocr_url=paddleocr_url,
        extract_only=extract_only,
        output=output,
        model=model,
        backend=backend,
        guide=(),
        review_plan=False,
        program=program,
    )
    if prepared is not None:
        console.print(f"\n  Next: [cyan]{ui.command()} solve[/] to attempt proofs")


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
        "model: auto\n"
        "max_output_tokens: 32768\n"
        "max_retries_per_sorry: 5\n"
        "escalation_policy: ask\n"
        "escalation_after_failures: 2\n"
        "cycle_timeout_seconds: 120\n"
        "max_cycles: 5\n"
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
    """Create a pinned Lean project and `program.md`."""
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
    console.print(f"    {ui.command()} targets -d {path}")
    console.print(f"    {ui.command()} solve")


# ---------------------------------------------------------------------------
_register_workflow_commands(main)
_register_session_commands(main)

# ---------------------------------------------------------------------------
# Entry
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    main()
