"""Shared command options and proof-workflow execution."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, TypeVar

import click
from rich.console import Console

from autolean.llm import BACKEND_NAMES, LLMBackend, LLMError, create_llm_client

if TYPE_CHECKING:
    from autolean.agent import AgentRunResult, AutoLeanAgent
    from autolean.lean_interface import BuildResult
    from autolean.program import ProgramConfig
    from autolean.routing import EscalationDecision
    from autolean.session import ProofSession, SessionStore

console = Console()

model_option = click.option(
    "--model",
    "-m",
    type=str,
    default=None,
    help="Model profile or ID; auto selects the strongest authenticated provider.",
)
backend_option = click.option(
    "--backend",
    "-b",
    type=click.Choice(BACKEND_NAMES),
    default=None,
    help="Select a provider; auto maps hosted providers to their strongest profile.",
)
program_option = click.option(
    "--program",
    "-p",
    type=click.Path(exists=True, path_type=Path),
    default="program.md",
    help="Path to program.md.",
)
pdf_engine_option = click.option(
    "--pdf-engine",
    type=click.Choice(["hybrid", "paddleocr-vl"]),
    default="hybrid",
    show_default=True,
    help="PDF-to-Markdown engine. PaddleOCR-VL requires --paddleocr-url.",
)
paddleocr_url_option = click.option(
    "--paddleocr-url",
    type=str,
    default=None,
    help="Explicit PaddleOCR-VL 1.6 service endpoint.",
)

CommandFunction = TypeVar("CommandFunction", bound=Callable[..., object])


def escalation_options(function: CommandFunction) -> CommandFunction:
    """Add the shared, cost-aware model-routing controls."""
    decorated = click.option(
        "--escalation",
        type=click.Choice(["never", "ask", "auto"]),
        default=None,
        help="Model switching policy after evidence-backed proof failures.",
    )(function)
    decorated = click.option(
        "--escalate-to",
        type=str,
        default=None,
        help="Explicit stronger model profile or model ID.",
    )(decorated)
    decorated = click.option(
        "--escalate-after",
        type=click.IntRange(min=1),
        default=None,
        help="Eligible failures before suggesting a stronger model.",
    )(decorated)
    return decorated


def _confirm_model_escalation(decision: EscalationDecision) -> bool:
    """Ask for a model switch when the command owns an interactive terminal."""
    if not click.get_text_stream("stdin").isatty():
        return False
    return click.confirm(
        f"Switch {decision.from_model} to {decision.to_model}?",
        default=False,
    )


def configure_escalation(
    agent: AutoLeanAgent,
    *,
    escalation: str | None,
    escalate_to: str | None,
    escalate_after: int | None,
) -> None:
    """Apply explicit routing controls to one configured agent."""
    from autolean.routing import EscalationPolicy

    if escalation is not None:
        agent.config.escalation_policy = EscalationPolicy(escalation)
    elif escalate_to is not None and agent.config.escalation_policy is EscalationPolicy.NEVER:
        agent.config.escalation_policy = EscalationPolicy.ASK
    if escalate_to is not None:
        agent.config.escalation_model = escalate_to
    if escalate_after is not None:
        agent.config.escalation_after_failures = escalate_after


def llm_for(
    model: str | None,
    backend: str | None,
    program_config: ProgramConfig,
    *,
    timeout: float | None = None,
) -> LLMBackend:
    """Build a backend from the command, program, and profile precedence."""
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


def connected_llm(
    model: str | None,
    backend: str | None,
    program_config: ProgramConfig,
    *,
    timeout: float | None = None,
) -> LLMBackend:
    """Build a backend and require its local preflight."""
    try:
        llm = llm_for(model, backend, program_config, timeout=timeout)
    except (LLMError, ValueError) as error:
        raise click.ClickException(f"Model selection failed: {error}") from error
    console.print(f"[dim]Model:[/] {llm.config.model} [dim]via[/] {llm.config.backend}")
    if llm.config.model_revision:
        console.print(f"[dim]Revision:[/] {llm.config.model_revision}")
    if llm.config.model_artifact_sha256:
        console.print(f"[dim]Weight SHA-256:[/] {llm.config.model_artifact_sha256}")
    if llm.config.seed is not None:
        console.print(f"[dim]Sampling seed:[/] {llm.config.seed}")
    try:
        reachable = llm.ping()
    except LLMError as error:
        llm.close()
        raise click.ClickException(str(error)) from error
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


def agent_for(
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
            confirm_escalation=_confirm_model_escalation,
        )
        if model is not None or backend is not None:
            agent.llm.close()
            agent.llm = llm_for(model, backend, agent.config)
        return agent
    except (LLMError, OSError, ValueError) as error:
        if agent is not None:
            agent.close()
        raise click.ClickException(f"Agent configuration failed: {error}") from error


def _execute_agent(agent: AutoLeanAgent) -> AgentRunResult:
    """Run and close an owned agent."""
    try:
        return agent.run()
    finally:
        agent.close()


def run_agent(agent: AutoLeanAgent) -> None:
    """Run an owned agent and translate its terminal status for Click."""
    result = _execute_agent(agent)
    if not result.successful:
        raise click.ClickException(result.message or "Agent run failed.")


def _remaining_session_targets(store: SessionStore, session: ProofSession) -> int:
    """Count unresolved targets inside one session's execution scope."""
    from autolean.scanner import count_sorries, scan_project

    target_file = store.target_path(session)
    if target_file is not None:
        if not target_file.is_file():
            raise click.ClickException(f"Session target does not exist: {target_file}")
        return count_sorries(target_file.read_text(encoding="utf-8"))

    targets = scan_project(store.project_root)
    if session.target_filter:
        targets = [target for target in targets if session.target_filter in (target.id, target.decl_name)]
    return len(targets)


def run_session_agent(
    agent: AutoLeanAgent,
    store: SessionStore,
    session: ProofSession,
) -> ProofSession:
    """Run an agent while preserving resumable workflow state."""
    from autolean.session import SessionStatus

    hints = dict.fromkeys([*agent.config.strategy_hints, *session.guidance])
    agent.config.strategy_hints = list(hints)
    agent.config.max_cycles = session.max_cycles
    agent.config.escalation_policy = session.escalation_policy
    agent.config.escalation_model = session.escalation_model or None
    agent.config.escalation_after_failures = session.escalation_after_failures
    running = store.save(
        session.update(
            status=SessionStatus.RUNNING,
            model=agent.llm.config.model,
            backend=agent.llm.config.backend,
            message="",
        )
    )
    console.print(f"[dim]Session:[/] {running.id}  [dim]resume with[/] autolean resume {running.id}")

    try:
        result = _execute_agent(agent)
        remaining = _remaining_session_targets(store, running)
    except Exception as error:
        store.save(
            running.update(
                status=SessionStatus.FAILED,
                model=agent.llm.config.model,
                backend=agent.llm.config.backend,
                model_transitions=(*running.model_transitions, *agent.model_transitions),
                message=str(error),
            )
        )
        raise

    status = SessionStatus.COMPLETED if remaining == 0 else SessionStatus.PAUSED
    if not result.successful:
        status = SessionStatus.FAILED
    finished = store.save(
        running.update(
            status=status,
            model=agent.llm.config.model,
            backend=agent.llm.config.backend,
            model_transitions=(*running.model_transitions, *agent.model_transitions),
            remaining_targets=remaining,
            message=result.message,
        )
    )
    if finished.status is SessionStatus.COMPLETED:
        console.print(f"[bold green]Session complete:[/] {finished.id}")
    else:
        console.print(
            f"[yellow]Session {finished.status.value}:[/] {remaining} target(s) remain\n"
            f"  autolean resume {finished.id} --model {finished.model}"
        )
    if not result.successful:
        raise click.ClickException(result.message or "Agent run failed.")
    return finished


def accept_generated_source(
    lean_root: Path,
    output: Path,
    content: str,
    *,
    timeout: int = 120,
    expected_content: str | None = None,
) -> tuple[Path, BuildResult]:
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
    except (OSError, ValueError) as error:
        raise click.ClickException(f"Generated Lean output could not be accepted: {error}") from error
    if result.success:
        return output, result

    detail = result.errors[0].message if result.errors else result.stderr.strip() or result.stdout.strip()
    detail = " ".join(detail.split())[:500] if detail else "Lean rejected the source"
    raise click.ClickException(f"Generated Lean failed sandboxed compilation: {detail}")
