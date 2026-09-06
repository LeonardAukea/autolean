"""Interactive workbench for choosing and validating one Lean proof target."""

from __future__ import annotations

import asyncio
import os
import shlex
import signal
import sys
import tempfile
import time
from collections import deque
from collections.abc import AsyncIterator
from contextlib import suppress
from dataclasses import dataclass, replace
from pathlib import Path
from typing import ClassVar, Literal

from rich.text import Text
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Container, Horizontal, Vertical, VerticalScroll
from textual.events import Resize
from textual.screen import ModalScreen
from textual.widgets import (
    Button,
    Footer,
    Header,
    Input,
    Label,
    Log,
    OptionList,
    Select,
    Static,
    TabbedContent,
    TabPane,
)
from textual.widgets.option_list import Option
from textual.worker import Worker, get_current_worker

from autolean.llm import BACKEND_NAMES, BACKENDS, provider_name, resolve_backend_name
from autolean.models import AUTO_PROFILE, ModelProfile, profile_groups, resolve_profile
from autolean.program import ProgramConfig, parse_program
from autolean.progress import MAX_EVENT_BYTES, ProgressEvent, ProgressKind, excerpt
from autolean.routing import DEFAULT_ESCALATION_AFTER, EscalationPolicy
from autolean.scanner import SorryTarget, difficulty_score, prioritize_targets, scan_project

WorkbenchAction = Literal["doctor", "inspect", "validate", "solve"]

PROFILE_DEFAULT = "__profile_default__"
CUSTOM_MODEL = "__custom_model__"


class WorkbenchInputError(ValueError):
    """A workbench field cannot form a valid proof run."""


@dataclass(frozen=True)
class WorkbenchSettings:
    """Validated model and experiment choices from one workbench session."""

    model: str
    backend: str | None
    endpoint: str | None
    effort: str | None
    max_output_tokens: int | None
    max_cycles: int
    escalation_policy: EscalationPolicy = EscalationPolicy.ASK
    escalation_model: str | None = None
    escalation_after_failures: int = DEFAULT_ESCALATION_AFTER
    guidance: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.model, str):
            raise WorkbenchInputError("Model must be text.")
        optional_text = (
            self.backend,
            self.endpoint,
            self.effort,
            self.escalation_model,
        )
        if any(value is not None and not isinstance(value, str) for value in optional_text) or not isinstance(
            self.guidance, str
        ):
            raise WorkbenchInputError("Optional model settings must be text.")
        if isinstance(self.max_cycles, bool) or not isinstance(self.max_cycles, int) or self.max_cycles <= 0:
            raise WorkbenchInputError("Experiment cycles must be positive.")
        if self.max_output_tokens is not None and (
            isinstance(self.max_output_tokens, bool)
            or not isinstance(self.max_output_tokens, int)
            or self.max_output_tokens <= 0
        ):
            raise WorkbenchInputError("Output token limit must be positive.")
        if not isinstance(self.escalation_policy, EscalationPolicy):
            raise WorkbenchInputError("Escalation policy is invalid.")
        if (
            isinstance(self.escalation_after_failures, bool)
            or not isinstance(self.escalation_after_failures, int)
            or self.escalation_after_failures <= 0
        ):
            raise WorkbenchInputError("Escalation threshold must be positive.")

    def program_config(self, base: ProgramConfig) -> ProgramConfig:
        """Apply session choices to a copy of the parsed program."""
        model = self.model.strip()
        if not model or any(character.isspace() for character in model):
            raise WorkbenchInputError("Model IDs must be one non-empty token.")
        if self.max_cycles <= 0:
            raise WorkbenchInputError("Experiment cycles must be positive.")

        strategy_hints = list(base.strategy_hints)
        if guidance := " ".join(self.guidance.split()):
            strategy_hints.append(guidance)
        config = replace(
            base,
            model=model,
            backend=self.backend,
            endpoint=self.endpoint,
            effort=self.effort,
            max_output_tokens=self.max_output_tokens,
            max_cycles=self.max_cycles,
            escalation_policy=self.escalation_policy,
            escalation_model=self.escalation_model,
            escalation_after_failures=self.escalation_after_failures,
            strategy_hints=strategy_hints,
        )
        try:
            config.validate()
            config.llm_config()
        except ValueError as error:
            raise WorkbenchInputError(str(error)) from error
        return config


@dataclass(frozen=True)
class CommandPlan:
    """One exact child-process invocation launched by the workbench."""

    action: WorkbenchAction
    argv: tuple[str, ...]
    cwd: Path
    mutates_project: bool

    def __post_init__(self) -> None:
        if self.action not in {"doctor", "inspect", "validate", "solve"}:
            raise WorkbenchInputError("Workbench action is invalid.")
        if (
            not isinstance(self.argv, tuple)
            or not self.argv
            or any(not isinstance(value, str) or not value for value in self.argv)
        ):
            raise WorkbenchInputError("Workbench command must be a text tuple.")
        if not isinstance(self.cwd, Path):
            raise WorkbenchInputError("Workbench command directory must be a path.")
        if not isinstance(self.mutates_project, bool):
            raise WorkbenchInputError("Workbench mutation flag must be a boolean.")

    @property
    def display(self) -> str:
        """Return a shell-readable command for the activity log."""
        return shlex.join(self.argv)


@dataclass(frozen=True)
class WorkbenchSession:
    """Project facts shared by the widgets and command planner."""

    program_path: Path
    config: ProgramConfig
    lean_root: Path
    targets: tuple[SorryTarget, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.program_path, Path) or not isinstance(self.lean_root, Path):
            raise WorkbenchInputError("Workbench project locations must be paths.")
        if not isinstance(self.config, ProgramConfig):
            raise WorkbenchInputError("Workbench config must use ProgramConfig.")
        if not isinstance(self.targets, tuple) or any(
            not isinstance(target, SorryTarget) for target in self.targets
        ):
            raise WorkbenchInputError("Workbench targets must use SorryTarget.")

    @classmethod
    def load(cls, program_path: Path) -> WorkbenchSession:
        """Parse the program and scan its Lean project once."""
        resolved_program = program_path.resolve()
        config = parse_program(resolved_program)
        lean_root = (resolved_program.parent / config.lean_project_path).resolve()
        if not (lean_root / "lakefile.lean").is_file():
            raise WorkbenchInputError(f"Lean project has no lakefile.lean: {lean_root}")
        targets = tuple(prioritize_targets(scan_project(lean_root)))
        return cls(resolved_program, config, lean_root, targets)

    def write_program(self, settings: WorkbenchSettings, path: Path) -> None:
        """Write the minimal, complete program consumed by child commands."""
        config = settings.program_config(self.config)
        path.write_text(render_program(config, self.lean_root), encoding="utf-8")

    def command(
        self,
        action: WorkbenchAction,
        program_path: Path,
        target: SorryTarget | None,
    ) -> CommandPlan:
        """Build the exact CLI command for an interactive action."""
        prefix: tuple[str, ...] = (sys.executable, "-m", "autolean")
        argv: tuple[str, ...]
        if action == "doctor":
            argv = (*prefix, "doctor", "--program", str(program_path))
        elif action == "inspect":
            if target is None:
                raise WorkbenchInputError("Select a proof target to inspect.")
            argv = (
                *prefix,
                "inspect",
                target.id,
                "--project",
                str(self.lean_root),
                "--goal-state",
            )
        else:
            if target is None:
                raise WorkbenchInputError("Select a proof target to run.")
            argv = (
                *prefix,
                "solve",
                "--program",
                str(program_path),
                "--target",
                target.id,
            )
            argv = (*argv, "--dry-run") if action == "validate" else (*argv, "--resume")
        return CommandPlan(
            action=action,
            argv=argv,
            cwd=self.program_path.parent,
            mutates_project=action == "solve",
        )


def _list_section(items: list[str]) -> str:
    """Render program list items without introducing new settings lines."""
    return "\n".join(f"- {' '.join(item.split())}" for item in items)


def render_program(config: ProgramConfig, lean_root: Path) -> str:
    """Serialize a validated program for one ephemeral workbench command."""
    config.validate()
    llm_lines = [f"model: {config.model}"]
    if config.backend is not None:
        backend = resolve_backend_name(config.backend)
        if backend is None:
            raise WorkbenchInputError(f"Unknown provider: {config.backend}")
        llm_lines.append(f"provider: {provider_name(backend)}")
    if config.endpoint is not None:
        llm_lines.append(f"endpoint: {config.endpoint}")
    if config.effort is not None:
        llm_lines.append(f"effort: {config.effort}")
    llm_lines.append(f"search_scope: {config.search_scope.value}")
    if config.temperature is not None:
        llm_lines.append(f"temperature: {config.temperature}")
    if config.max_output_tokens is not None:
        llm_lines.append(f"max_output_tokens: {config.max_output_tokens}")
    llm_lines.extend(
        (
            f"max_retries_per_sorry: {config.max_retries_per_sorry}",
            f"escalation_policy: {config.escalation_policy.value}",
            f"escalation_after_failures: {config.escalation_after_failures}",
            f"cycle_timeout_seconds: {config.cycle_timeout_seconds}",
            f"llm_timeout_seconds: {config.llm_timeout_seconds or 600}",
            f"max_proof_lines: {config.max_proof_lines}",
        )
    )
    if config.escalation_model is not None:
        llm_lines.append(f"escalation_model: {config.escalation_model}")
    llm_config = "\n".join(llm_lines)

    return (
        "# AutoLean Workbench Session\n\n"
        "## Mode\n\n"
        f"{config.mode}\n\n"
        "## Lean Project Path\n\n"
        f"{lean_root}\n\n"
        "## Goals\n\n"
        f"{_list_section(config.goals)}\n\n"
        "## Constraints\n\n"
        f"{_list_section(config.constraints)}\n\n"
        "## Strategy Hints\n\n"
        f"{_list_section(config.strategy_hints)}\n\n"
        "## LLM Configuration\n\n"
        f"{llm_config}\n\n"
        "## Experiment Budget\n\n"
        f"max_cycles: {config.max_cycles}\n"
    )


def _profile_options() -> list[tuple[str, str]]:
    """Return discoverable profile labels in registry display order."""
    options = [("auto · Strongest authenticated provider", AUTO_PROFILE)]
    for group, profiles in profile_groups():
        options.extend((f"{profile.name} · {group}", profile.name) for profile in profiles)
    options.append(("Custom model ID…", CUSTOM_MODEL))
    return options


class ConfirmSolve(ModalScreen[bool]):
    """Require an explicit choice before a project-changing proof run."""

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("escape", "cancel", "Cancel"),
        Binding("ctrl+enter", "accept", "Accept"),
    ]

    DEFAULT_CSS = """
    ConfirmSolve {
        align: center middle;
        background: $background 70%;
    }

    ConfirmSolve > Container {
        width: 66;
        height: auto;
        padding: 1 2;
        border: round $warning;
        background: $panel;
    }

    ConfirmSolve .modal-title {
        text-style: bold;
        color: $warning;
        margin-bottom: 1;
    }

    ConfirmSolve Horizontal {
        height: 3;
        align: right middle;
        margin-top: 1;
    }

    ConfirmSolve Button {
        margin-left: 1;
    }
    """

    def __init__(self, target_name: str) -> None:
        super().__init__()
        self.target_name = target_name

    def compose(self) -> ComposeResult:
        with Container():
            yield Static("Accept a proof into the Lean project?", classes="modal-title")
            yield Static(
                f"Target: {self.target_name}\n\n"
                "AutoLean writes only the exact candidate accepted by the "
                "sandboxed pinned Lean kernel.",
                markup=False,
            )
            with Horizontal():
                yield Button("Cancel", id="cancel-solve")
                yield Button("Accept proof", id="accept-solve", variant="warning")

    @on(Button.Pressed)
    def handle_button(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "accept-solve")

    def action_cancel(self) -> None:
        self.dismiss(False)

    def action_accept(self) -> None:
        self.dismiss(True)


class AutoLeanWorkbench(App[None]):
    """A keyboard-friendly proof workbench for mathematicians."""

    TITLE = "AutoLean Workbench"
    SUB_TITLE = "attempts → Lean feedback → reusable proof patterns"

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("/", "focus_filter", "Find target"),
        Binding("ctrl+f", "focus_filter", "Find target", show=False, priority=True),
        Binding("ctrl+d", "doctor", "Check system"),
        Binding("ctrl+i", "inspect", "Inspect goal"),
        Binding("ctrl+v", "validate", "Validate"),
        Binding("ctrl+s", "solve", "Run agent"),
        Binding("escape", "stop", "Stop run"),
    ]

    CSS = """
    Screen {
        background: #0c1117;
        color: #d9e2ec;
    }

    Header {
        background: #132238;
        color: #f5f7fa;
    }

    #main {
        height: 1fr;
        padding: 1;
    }

    #workspace, TabPane {
        height: 1fr;
    }

    #research-view {
        padding: 0 1;
        height: 1fr;
    }

    #run-progress {
        height: 3;
        color: #7dd3fc;
    }

    #research-goal {
        height: 3;
        overflow-y: auto;
        color: #9fb3c8;
    }

    #evidence {
        height: 1fr;
        min-height: 5;
    }

    .research-card {
        border: round #34506f;
        padding: 0 1;
    }

    .research-card .pane-title {
        height: 1;
        padding-top: 0;
    }

    #candidate-pane, #feedback-pane {
        width: 1fr;
    }

    #candidate-pane {
        margin-right: 1;
    }

    #learning-pane {
        height: 6;
        border: round #73538f;
    }

    .pane {
        border: round #34506f;
        background: #111a24;
        padding: 0 1;
    }

    #targets-pane {
        width: 3fr;
        margin-right: 1;
    }

    #model-pane {
        width: 2fr;
    }

    .pane-title {
        color: #7dd3fc;
        text-style: bold;
        height: 2;
        padding-top: 1;
    }

    .field-label {
        color: #9fb3c8;
        height: 1;
        margin-top: 1;
    }

    Input, Select {
        height: 3;
    }

    #target-list {
        height: 1fr;
        margin-top: 1;
        border: tall #263b52;
    }

    #target-details, #model-details {
        min-height: 3;
        color: #9fb3c8;
        padding: 0 1;
    }

    #actions {
        height: 3;
        padding: 0 1;
        align: left middle;
    }

    #actions Button {
        margin-right: 1;
        min-width: 15;
    }

    #status {
        height: 2;
        padding: 0 2;
        color: #7dd3fc;
    }

    #activity {
        height: 1fr;
        margin: 0 1;
        border: round #263b52;
        background: #080c11;
        padding: 0 1;
    }

    Screen.narrow #target-details,
    Screen.narrow #model-details {
        display: none;
    }

    Screen.narrow #actions Button {
        min-width: 11;
    }

    Screen.compact #research-goal {
        height: 2;
    }

    Screen.compact #learning-pane {
        height: 4;
    }

    Screen.compact #status {
        height: 1;
    }

    Footer {
        background: #132238;
    }
    """

    def __init__(self, session: WorkbenchSession) -> None:
        super().__init__()
        self.session = session
        self._visible_targets = list(session.targets)
        self._target_by_option: dict[str, SorryTarget] = {}
        self._temporary_directory = tempfile.TemporaryDirectory(prefix="autolean-workbench-")
        self._session_program = Path(self._temporary_directory.name) / "program.md"
        self._process: asyncio.subprocess.Process | None = None
        self._worker: Worker[None] | None = None
        self._run_started = 0.0
        self._run_phase = "Ready"
        self._run_summary = ""
        self._run_target = ""
        self._stop_requests = 0
        self._quit_after_run = False
        self._lessons: deque[str] = deque(maxlen=8)
        self._transcript_pending: deque[str] = deque(maxlen=1000)
        self._transcript_scheduled = False

    def _setup_widgets(self) -> ComposeResult:
        initial_profile = resolve_profile(self.session.config.model)
        is_automatic = self.session.config.model == AUTO_PROFILE
        profile_value = (
            AUTO_PROFILE
            if is_automatic
            else initial_profile.name
            if initial_profile is not None
            else CUSTOM_MODEL
        )
        custom_value = "" if initial_profile is not None or is_automatic else self.session.config.model
        backend_value = self.session.config.backend or PROFILE_DEFAULT
        effort_value = self.session.config.effort or PROFILE_DEFAULT

        with Horizontal(id="main"):
            with Vertical(id="targets-pane", classes="pane"):
                yield Static("Proof targets", classes="pane-title")
                yield Input(placeholder="Filter by theorem or file…", id="target-filter")
                yield OptionList(id="target-list")
                yield Static("", id="target-details", markup=False)
            with VerticalScroll(id="model-pane", classes="pane"):
                yield Static("Model", classes="pane-title")
                yield Label("Profile", classes="field-label")
                yield Select(
                    _profile_options(),
                    value=profile_value,
                    allow_blank=False,
                    id="model-profile",
                )
                yield Label("Custom model ID", classes="field-label")
                yield Input(
                    value=custom_value,
                    placeholder="provider/model-or-local-tag",
                    id="custom-model",
                    disabled=initial_profile is not None or is_automatic,
                )
                yield Label("Provider", classes="field-label")
                yield Select(
                    [
                        ("Use profile default", PROFILE_DEFAULT),
                        *(
                            (
                                f"{BACKENDS[name].provider} · {BACKENDS[name].summary}",
                                name,
                            )
                            for name in BACKEND_NAMES
                        ),
                    ],
                    value=backend_value,
                    allow_blank=False,
                    id="backend",
                )
                yield Label("Endpoint", classes="field-label")
                yield Input(
                    value=self.session.config.endpoint or "",
                    placeholder="Profile default or http://127.0.0.1:8080",
                    id="endpoint",
                )
                yield Label("Reasoning", classes="field-label")
                yield Select(
                    [
                        ("Use profile default", PROFILE_DEFAULT),
                        *(
                            (effort.title(), effort)
                            for effort in ("none", "low", "medium", "high", "xhigh", "max")
                        ),
                    ],
                    value=effort_value,
                    allow_blank=False,
                    id="effort",
                )
                yield Label("Maximum output tokens", classes="field-label")
                yield Input(
                    value=(
                        str(self.session.config.max_output_tokens)
                        if self.session.config.max_output_tokens is not None
                        else ""
                    ),
                    placeholder="Use profile default",
                    type="integer",
                    id="max-output-tokens",
                )
                yield Label("Experiment cycles", classes="field-label")
                yield Input(
                    value=str(self.session.config.max_cycles or 1),
                    type="integer",
                    id="max-cycles",
                )
                yield Label("When the model stalls", classes="field-label")
                yield Select(
                    [
                        ("Suggest a stronger model", EscalationPolicy.ASK.value),
                        ("Keep the selected model", EscalationPolicy.NEVER.value),
                        ("Switch automatically", EscalationPolicy.AUTO.value),
                    ],
                    value=self.session.config.escalation_policy.value,
                    allow_blank=False,
                    id="escalation-policy",
                )
                yield Input(
                    value=self.session.config.escalation_model or "",
                    placeholder="Use the profile's stronger sibling",
                    id="escalation-model",
                )
                yield Input(
                    value=str(self.session.config.escalation_after_failures),
                    type="integer",
                    id="escalation-after",
                )
                yield Label("Guidance for the next run", classes="field-label")
                yield Input(
                    placeholder="A constraint, lemma, or method to try",
                    id="guidance",
                )
                yield Static("", id="model-details", markup=False)

    def compose(self) -> ComposeResult:
        yield Header(show_clock=False)
        with TabbedContent(initial="setup", id="workspace"):
            with TabPane("Setup", id="setup"):
                yield from self._setup_widgets()
            with TabPane("Research", id="research"), Vertical(id="research-view"):
                yield Static("Ready", id="run-progress", markup=False)
                yield Static("Select a target and run the agent.", id="research-goal", markup=False)
                with Horizontal(id="evidence"):
                    with VerticalScroll(id="candidate-pane", classes="research-card"):
                        yield Static("Candidate", classes="pane-title")
                        yield Static("Awaiting a model proposal.", id="candidate", markup=False)
                    with VerticalScroll(id="feedback-pane", classes="research-card"):
                        yield Static("Lean feedback", classes="pane-title")
                        yield Static("Each candidate requires a Lean verdict.", id="feedback", markup=False)
                with VerticalScroll(id="learning-pane", classes="research-card"):
                    yield Static("Learning", classes="pane-title")
                    yield Static(
                        "Accepted proofs supply reusable patterns. Lean errors guide retries.",
                        id="learning",
                        markup=False,
                    )
            with TabPane("Transcript", id="transcript"):
                yield Log(id="activity", max_lines=1000, highlight=False)
        with Horizontal(id="actions"):
            yield Button("Check system", id="doctor", classes="command")
            yield Button("Inspect goal", id="inspect", classes="command")
            yield Button("Validate", id="validate", variant="primary", classes="command")
            yield Button("Run agent", id="solve", variant="warning", classes="command")
            yield Button("Stop", id="stop", variant="error", disabled=True)
        yield Label(
            "Ready · Validate runs the complete model-to-kernel loop without project writes.",
            id="status",
            markup=False,
        )
        yield Footer()

    def on_mount(self) -> None:
        self._show_targets(self.session.targets)
        self._update_model_details()
        self.set_interval(1, self._update_run_progress)

    def on_unmount(self) -> None:
        self._temporary_directory.cleanup()

    def on_resize(self, event: Resize) -> None:
        self.screen.set_class(event.size.width < 100, "narrow")
        self.screen.set_class(event.size.height < 34, "compact")

    @on(Input.Changed, "#target-filter")
    def filter_targets(self, event: Input.Changed) -> None:
        del event
        self._show_targets(self.session.targets)

    @on(OptionList.OptionHighlighted, "#target-list")
    def show_target_details(self, event: OptionList.OptionHighlighted) -> None:
        self._update_target_details(self._target_by_option.get(event.option_id or ""))

    @on(OptionList.OptionSelected, "#target-list")
    def inspect_selected_target(self, _: OptionList.OptionSelected) -> None:
        self.action_inspect()

    @on(Select.Changed, "#model-profile")
    def select_model_profile(self, event: Select.Changed) -> None:
        custom = self.query_one("#custom-model", Input)
        custom.disabled = event.value != CUSTOM_MODEL
        if event.value == CUSTOM_MODEL:
            custom.focus()
        self._update_model_details()

    @on(Button.Pressed, "#doctor")
    def press_doctor(self) -> None:
        self.action_doctor()

    @on(Button.Pressed, "#inspect")
    def press_inspect(self) -> None:
        self.action_inspect()

    @on(Button.Pressed, "#validate")
    def press_validate(self) -> None:
        self.action_validate()

    @on(Button.Pressed, "#solve")
    def press_solve(self) -> None:
        self.action_solve()

    @on(Button.Pressed, "#stop")
    def press_stop(self) -> None:
        self.action_stop()

    def action_focus_filter(self) -> None:
        self.query_one("#workspace", TabbedContent).active = "setup"
        self.call_after_refresh(self.query_one("#target-filter", Input).focus)

    def action_doctor(self) -> None:
        self._launch("doctor")

    def action_inspect(self) -> None:
        self._launch("inspect")

    def action_validate(self) -> None:
        self._launch("validate")

    def action_solve(self) -> None:
        if self._command_active():
            return
        target = self._selected_target()
        if target is None:
            self._show_error("Select a proof target to accept.")
            return
        self.push_screen(
            ConfirmSolve(target.qualified_decl_name or target.decl_name),
            self._confirmed_solve,
        )

    def action_stop(self) -> None:
        """Request a stop while the child owns proof installation cleanup."""
        process = self._process
        if process is None:
            if self._worker is not None and not self._worker.is_finished:
                self._stop_requests += 1
                self.query_one("#status", Label).update("Stopping command startup…")
            return
        if process.returncode is not None:
            return
        self._stop_requests += 1
        try:
            process.send_signal(signal.SIGINT)
        except ProcessLookupError:
            return
        message = (
            "Stopping after this attempt · press Stop again to interrupt the candidate."
            if self._stop_requests == 1
            else "Interrupting the candidate · preserving accepted proof records."
        )
        self.query_one("#status", Label).update(message)

    async def action_quit(self) -> None:
        """Close after the active child has recorded its final state."""
        if self._worker is not None and not self._worker.is_finished:
            self._quit_after_run = True
            self.action_stop()
        else:
            self.exit()

    def _confirmed_solve(self, confirmed: bool | None) -> None:
        if confirmed:
            self.call_after_refresh(self._launch, "solve")

    def _show_targets(self, targets: list[SorryTarget] | tuple[SorryTarget, ...]) -> None:
        option_list = self.query_one("#target-list", OptionList)
        query = self.query_one("#target-filter", Input).value.casefold().strip()
        targets = [
            target
            for target in targets
            if query
            in " ".join((target.decl_name, target.qualified_decl_name, target.rel_path, target.id)).casefold()
        ]
        self._visible_targets = list(targets)
        self._target_by_option.clear()
        options: list[Option] = []
        for index, target in enumerate(self._visible_targets):
            option_id = f"target-{index}"
            self._target_by_option[option_id] = target
            difficulty = difficulty_score(target)
            declaration = target.qualified_decl_name or target.decl_name
            options.append(
                Option(
                    f"{declaration}  ·  {target.rel_path}:{target.line}  ·  level {difficulty}",
                    id=option_id,
                )
            )
        if not options:
            options.append(Option("No matching proof targets", id="empty", disabled=True))
        option_list.clear_options().add_options(options)
        option_list.highlighted = 0
        self._update_target_details(self._visible_targets[0] if self._visible_targets else None)

    def _selected_target(self) -> SorryTarget | None:
        option_list = self.query_one("#target-list", OptionList)
        index = option_list.highlighted
        if index is None:
            return None
        option = option_list.get_option_at_index(index)
        return self._target_by_option.get(option.id or "")

    def _update_target_details(self, target: SorryTarget | None) -> None:
        details = self.query_one("#target-details", Static)
        if target is None:
            details.update("No target selected.")
            return
        mode = "tactic proof" if target.tactic_mode else "proof term"
        details.update(
            f"{target.qualified_decl_name or target.decl_name}\n"
            f"{target.rel_path}:{target.line}:{target.col} · {mode}"
        )

    def _profile_value(self) -> str:
        value = self.query_one("#model-profile", Select).value
        if not isinstance(value, str):
            raise WorkbenchInputError("Select a model profile.")
        return value

    def _settings(self) -> WorkbenchSettings:
        profile_value = self._profile_value()
        model = (
            self.query_one("#custom-model", Input).value.strip()
            if profile_value == CUSTOM_MODEL
            else profile_value
        )
        backend_value = self.query_one("#backend", Select).value
        effort_value = self.query_one("#effort", Select).value
        backend = (
            backend_value if isinstance(backend_value, str) and backend_value != PROFILE_DEFAULT else None
        )
        effort = effort_value if isinstance(effort_value, str) and effort_value != PROFILE_DEFAULT else None
        endpoint = self.query_one("#endpoint", Input).value.strip() or None
        max_output_tokens = self._optional_positive_integer("#max-output-tokens", "Maximum output tokens")
        max_cycles = self._positive_integer("#max-cycles", "Experiment cycles")
        escalation_value = self.query_one("#escalation-policy", Select).value
        if not isinstance(escalation_value, str):
            raise WorkbenchInputError("Select a model escalation policy.")
        settings = WorkbenchSettings(
            model=model,
            backend=backend,
            endpoint=endpoint,
            effort=effort,
            max_output_tokens=max_output_tokens,
            max_cycles=max_cycles,
            escalation_policy=EscalationPolicy(escalation_value),
            escalation_model=self.query_one("#escalation-model", Input).value.strip() or None,
            escalation_after_failures=self._positive_integer(
                "#escalation-after",
                "Escalation failures",
            ),
            guidance=self.query_one("#guidance", Input).value,
        )
        settings.program_config(self.session.config)
        return settings

    def _positive_integer(self, selector: str, label: str) -> int:
        raw = self.query_one(selector, Input).value.strip()
        try:
            value = int(raw)
        except ValueError as error:
            raise WorkbenchInputError(f"{label} must be a positive integer.") from error
        if value <= 0:
            raise WorkbenchInputError(f"{label} must be a positive integer.")
        return value

    def _optional_positive_integer(self, selector: str, label: str) -> int | None:
        raw = self.query_one(selector, Input).value.strip()
        return self._positive_integer(selector, label) if raw else None

    def _update_model_details(self) -> None:
        details = self.query_one("#model-details", Static)
        try:
            value = self._profile_value()
        except WorkbenchInputError:
            details.update("")
            return
        if value == AUTO_PROFILE:
            details.update(
                "Strongest profile for an authenticated provider.\n"
                "Select a hosted provider to choose its maximum explicitly."
            )
            return
        profile: ModelProfile | None = resolve_profile(value) if value != CUSTOM_MODEL else None
        if profile is None:
            details.update("Custom model · choose its provider and optional endpoint.")
            return
        route = f"\nStronger sibling: {profile.escalates_to}" if profile.escalates_to else ""
        setup = f"\nSetup: {profile.setup_command}" if profile.setup_command else ""
        details.update(f"{profile.description}\nProvider: {provider_name(profile.backend)}{route}{setup}")

    def _launch(self, action: WorkbenchAction) -> None:
        if self._command_active():
            return
        try:
            target = None if action == "doctor" else self._selected_target()
            if action != "inspect":
                settings = self._settings()
                self.session.write_program(settings, self._session_program)
            plan = self.session.command(action, self._session_program, target)
        except (OSError, WorkbenchInputError, ValueError) as error:
            self._show_error(str(error))
            return
        self._stop_requests = 0
        self._worker = self.run_plan(plan)

    def _command_active(self) -> bool:
        if self._worker is not None and not self._worker.is_finished:
            self.notify("A command is running. Stop it before starting another.")
            return True
        return False

    def _show_error(self, message: str) -> None:
        self.query_one("#status", Label).update(f"Input error · {message}")
        self.notify(message, severity="error")

    def _set_commands_disabled(self, disabled: bool) -> None:
        for button in self.query("#actions Button").results(Button):
            if button.id != "stop":
                button.disabled = disabled
        self.query_one("#stop", Button).disabled = not disabled

    def _update_run_progress(self) -> None:
        """Keep elapsed time visible while a model or Lean call is pending."""
        if self._process is not None and self._process.returncode is None:
            elapsed = int(time.monotonic() - self._run_started)
            duration = f"{elapsed // 60}m {elapsed % 60:02d}s"
        else:
            duration = ""
        self.query_one("#run-progress", Static).update(
            Text(
                "\n".join(
                    filter(None, (f"{self._run_phase}  {duration}", self._run_target, self._run_summary))
                )
            )
        )

    def _receive_progress(self, event: ProgressEvent) -> None:
        """Update research views from explicit observations of the child."""
        if event.target:
            self._run_target = event.target
        if event.kind is ProgressKind.SUMMARY:
            self._run_summary = event.message
        else:
            self._run_phase = event.message
        if event.kind in {ProgressKind.TARGET, ProgressKind.GOAL}:
            context = event.detail or event.message
            if event.kind is ProgressKind.GOAL:
                lines = context.splitlines()
                goals = [line for line in lines if line.lstrip().startswith("⊢")]
                hypotheses = [
                    line
                    for line in lines
                    if not line.lstrip().startswith("⊢") and line.strip() != "unsolved goals"
                ]
                context = "\n".join([*goals, *hypotheses])
            self.query_one("#research-goal", Static).update(Text(context))
        if event.kind is ProgressKind.TARGET:
            self.query_one("#candidate", Static).update("Awaiting a model proposal.")
            self.query_one("#feedback", Static).update("This attempt has no Lean verdict yet.")
        if event.kind is ProgressKind.CANDIDATE:
            self.query_one("#candidate", Static).update(Text(event.detail))
        if event.kind is ProgressKind.FEEDBACK:
            self.query_one("#feedback", Static).update(Text(f"{event.message}\n{event.detail}"))
        if event.kind is ProgressKind.LEARNING:
            self._lessons.append(f"{event.message}\n{event.detail}")
            self.query_one("#learning", Static).update(Text("\n\n".join(reversed(self._lessons))))
        self._update_run_progress()

    def _receive_output(self, line: str) -> None:
        """Keep human output and validated progress observations readable."""
        try:
            event = ProgressEvent.from_line(line)
        except ValueError as error:
            self._write_transcript(f"Activity record rejected: {error}")
            return
        if event is None:
            self._write_transcript(Text.from_ansi(line).plain)
            return
        self._receive_progress(event)
        self._write_transcript(f"{event.time_label}  {event.message}\n{event.detail}".rstrip())

    def _write_transcript(self, text: str) -> None:
        """Bound pending lines and coalesce a burst into one UI write."""
        self._transcript_pending.extend(excerpt(line) for line in text.splitlines() or [""])
        if not self._transcript_scheduled:
            self._transcript_scheduled = True
            self.call_later(self._flush_transcript)

    def _flush_transcript(self) -> None:
        self._transcript_scheduled = False
        if self._transcript_pending:
            self.query_one("#activity", Log).write("\n".join(self._transcript_pending) + "\n")
            self._transcript_pending.clear()

    @work(exclusive=True, group="command")
    async def run_plan(self, plan: CommandPlan) -> None:
        """Stream one child CLI workflow into the activity log."""
        worker = get_current_worker()
        if self._worker is not worker:
            self._stop_requests = 0
        self._worker = worker
        activity = self.query_one("#activity", Log)
        status = self.query_one("#status", Label)
        activity.clear()
        self._transcript_pending.clear()
        activity.write(f"$ {plan.display}\n")
        status.update(f"Running {plan.action}…")
        self._run_started = time.monotonic()
        self._run_phase = f"Starting {plan.action}"
        self._run_summary = ""
        self._run_target = ""
        self._lessons.clear()
        self.query_one("#research-goal", Static).update("Waiting for a proof target.")
        self.query_one("#candidate", Static).update("Awaiting a model proposal.")
        self.query_one("#feedback", Static).update("This run has no Lean verdict yet.")
        self.query_one("#learning", Static).update("Waiting for learning observations from this run.")
        self.query_one("#workspace", TabbedContent).active = (
            "research" if plan.action in {"solve", "validate"} else "transcript"
        )
        if plan.action in {"solve", "validate"}:
            self.query_one("#candidate-pane", VerticalScroll).focus()
        self._set_commands_disabled(True)
        environment = os.environ.copy()
        environment["PYTHONUNBUFFERED"] = "1"
        environment["AUTOLEAN_PROGRESS"] = "json"
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *plan.argv,
                cwd=plan.cwd,
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
                start_new_session=True,
            )
            self._process = process
            if self._stop_requests:
                with suppress(ProcessLookupError):
                    process.send_signal(signal.SIGINT)
            assert process.stdout is not None
            async for line in _terminal_lines(process.stdout):
                self._receive_output(line)
            return_code = await process.wait()
            if return_code == 0:
                status.update(f"{plan.action.title()} finished · review the Lean verdict and open targets.")
                self.notify(f"{plan.action.title()} finished.")
                if plan.mutates_project:
                    # The rescan reads every project source; keep the UI live.
                    self.session = await asyncio.to_thread(
                        WorkbenchSession.load,
                        self.session.program_path,
                    )
                    self._show_targets(self.session.targets)
            else:
                status.update(f"{plan.action.title()} failed with exit code {return_code}.")
                self.notify(f"{plan.action.title()} failed.", severity="error")
        except asyncio.CancelledError:
            if self._worker is worker:
                status.update(f"Cancelled {plan.action}.")
            raise
        except OSError as error:
            status.update(f"Could not complete {plan.action}: {error}")
            self.notify(str(error), severity="error")
        finally:
            if process is not None and process.returncode is None:
                await _interrupt_child(process)
            if self._worker is worker:
                self._process = None
                self._worker = None
                self._update_run_progress()
                self._set_commands_disabled(False)
                if self._quit_after_run:
                    self.exit()


async def _terminal_lines(reader: asyncio.StreamReader) -> AsyncIterator[str]:
    """Drain complete lines and bounded fragments of long diagnostics."""
    pending = bytearray()
    while chunk := await reader.read(8192):
        pending.extend(chunk)
        while pending:
            newline = pending.find(b"\n")
            if 0 <= newline < MAX_EVENT_BYTES:
                yield bytes(pending[:newline]).decode("utf-8", errors="replace").rstrip("\r")
                del pending[: newline + 1]
            elif len(pending) >= MAX_EVENT_BYTES:
                yield bytes(pending[:MAX_EVENT_BYTES]).decode("utf-8", errors="replace")
                del pending[:MAX_EVENT_BYTES]
            else:
                break
    if pending:
        yield pending.decode("utf-8", errors="replace")


async def _interrupt_child(process: asyncio.subprocess.Process) -> None:
    """Let the agent unwind owned processes and finish accepted records."""

    async def drain() -> None:
        if process.stdout is not None:
            while await process.stdout.read(8192):
                pass
        await process.wait()

    cleanup = asyncio.create_task(drain())
    try:
        process.send_signal(signal.SIGINT)
        try:
            await asyncio.wait_for(asyncio.shield(cleanup), timeout=2)
        except TimeoutError:
            process.send_signal(signal.SIGINT)
            await cleanup
    except ProcessLookupError:
        await cleanup


def run_workbench(program_path: Path) -> None:
    """Load and run the interactive workbench."""
    AutoLeanWorkbench(WorkbenchSession.load(program_path)).run()
