"""Persistent proof-session commands."""

from __future__ import annotations

from pathlib import Path

import click

from autolean import cli_runtime
from autolean.ui import console

_agent_for = cli_runtime.agent_for
_configure_escalation = cli_runtime.configure_escalation
_run_session_agent = cli_runtime.run_session_agent
backend_option = cli_runtime.backend_option
escalation_options = cli_runtime.escalation_options
model_option = cli_runtime.model_option
program_option = cli_runtime.program_option


@click.group()
def session_commands() -> None:
    """Own persistent proof-session commands."""


@session_commands.command("sessions")
@click.option("--json", "as_json", is_flag=True, help="Output canonical session JSON.")
@click.option("--active", is_flag=True, help="Show only resumable sessions.")
@program_option
def sessions(program: Path, as_json: bool, active: bool) -> None:
    """List persistent proof sessions."""
    import json

    from rich.table import Table

    from autolean.program import parse_program
    from autolean.session import SessionStatus, SessionStore

    config = parse_program(program)
    store = SessionStore(program.parent / config.lean_project_path)
    records = store.list()
    if active:
        records = [record for record in records if record.status is not SessionStatus.COMPLETED]
    if as_json:
        click.echo(json.dumps([record.as_dict() for record in records], indent=2, sort_keys=True))
        return
    if not records:
        console.print("[dim]No proof sessions found.[/]")
        return

    table = Table(title="Proof sessions")
    table.add_column("Session", style="cyan", no_wrap=True)
    table.add_column("Kind")
    table.add_column("Status")
    table.add_column("Model")
    table.add_column("Remaining", justify="right")
    table.add_column("Title")
    for record in records:
        style = {
            SessionStatus.COMPLETED: "green",
            SessionStatus.RUNNING: "cyan",
            SessionStatus.FAILED: "red",
        }.get(record.status, "yellow")
        model_path = record.model
        if record.model_transitions:
            model_path = f"{record.model_transitions[0].from_model} → {record.model}"
        table.add_row(
            record.id,
            record.kind.value,
            f"[{style}]{record.status.value}[/{style}]",
            model_path,
            "?" if record.remaining_targets is None else str(record.remaining_targets),
            record.title,
        )
    console.print(table)
    console.print(f"\n[dim]Continue the latest:[/] autolean resume {records[0].id}")


@session_commands.command("resume")
@click.argument("session_id", required=False)
@model_option
@backend_option
@escalation_options
@click.option(
    "--max-cycles",
    type=click.IntRange(min=0),
    default=None,
    help="Cycle budget for this continuation (0 = unlimited).",
)
@click.option(
    "--guide",
    multiple=True,
    help="Add a mathematical constraint or preferred method.",
)
@program_option
def resume_session(
    session_id: str | None,
    model: str | None,
    backend: str | None,
    escalation: str | None,
    escalate_to: str | None,
    escalate_after: int | None,
    max_cycles: int | None,
    guide: tuple[str, ...],
    program: Path,
) -> None:
    """Continue a proof session, optionally with a different model."""
    from autolean.program import parse_program
    from autolean.session import SessionError, SessionStatus, SessionStore

    config = parse_program(program)
    store = SessionStore(program.parent / config.lean_project_path)
    try:
        session = store.load(session_id) if session_id else store.latest()
    except SessionError as error:
        raise click.ClickException(str(error)) from error
    if session.status is SessionStatus.COMPLETED:
        raise click.ClickException(f"Proof session is already complete: {session.id}")

    target_file = store.target_path(session)
    if target_file is not None and not target_file.is_file():
        raise click.ClickException(f"Session target does not exist: {target_file}")
    cycle_budget = session.max_cycles if max_cycles is None else max_cycles
    guidance = tuple(dict.fromkeys([*session.guidance, *guide]))
    agent = _agent_for(
        program,
        model=model or session.model,
        backend=backend or session.backend,
        verbose=True,
        resume=True,
        target_filter=session.target_filter or None,
        target_file=target_file,
    )
    agent.config.escalation_policy = session.escalation_policy
    agent.config.escalation_model = session.escalation_model or None
    agent.config.escalation_after_failures = session.escalation_after_failures
    _configure_escalation(
        agent,
        escalation=escalation,
        escalate_to=escalate_to,
        escalate_after=escalate_after,
    )
    updated = store.save(
        session.update(
            model=agent.llm.config.model,
            backend=agent.llm.config.backend,
            max_cycles=cycle_budget,
            guidance=guidance,
            escalation_policy=agent.config.escalation_policy,
            escalation_model=agent.config.escalation_model or "",
            escalation_after_failures=agent.config.escalation_after_failures,
        )
    )
    _run_session_agent(agent, store, updated)


# ---------------------------------------------------------------------------
# targets — find sorry targets
# ---------------------------------------------------------------------------


def register_commands(root: click.Group) -> None:
    """Register persistent session commands on the root group."""
    for command in session_commands.commands.values():
        root.add_command(command)
