"""Interactive workbench for choosing and validating one Lean proof target."""

from __future__ import annotations

import asyncio
import os
import shlex
import sys
import tempfile
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
from textual.widgets import Button, Footer, Header, Input, Label, OptionList, RichLog, Select, Static
from textual.widgets.option_list import Option

from autolean.llm import BACKEND_NAMES, BACKENDS
from autolean.models import ModelProfile, profile_groups, resolve_profile
from autolean.program import ProgramConfig, parse_program
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

    def program_config(self, base: ProgramConfig) -> ProgramConfig:
        """Apply session choices to a copy of the parsed program."""
        model = self.model.strip()
        if not model or any(character.isspace() for character in model):
            raise WorkbenchInputError("Model IDs must be one non-empty token.")
        if self.max_cycles <= 0:
            raise WorkbenchInputError("Experiment cycles must be positive.")

        config = replace(
            base,
            model=model,
            backend=self.backend,
            endpoint=self.endpoint,
            effort=self.effort,
            max_output_tokens=self.max_output_tokens,
            max_cycles=self.max_cycles,
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
            if action == "validate":
                argv = (*argv, "--dry-run")
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
        llm_lines.append(f"backend: {config.backend}")
    if config.endpoint is not None:
        llm_lines.append(f"endpoint: {config.endpoint}")
    if config.effort is not None:
        llm_lines.append(f"effort: {config.effort}")
    llm_lines.append(f"temperature: {config.temperature}")
    if config.max_output_tokens is not None:
        llm_lines.append(f"max_output_tokens: {config.max_output_tokens}")
    llm_lines.extend(
        (
            f"max_retries_per_sorry: {config.max_retries_per_sorry}",
            f"cycle_timeout_seconds: {config.cycle_timeout_seconds}",
            f"llm_timeout_seconds: {config.llm_timeout_seconds or 600}",
            f"max_proof_lines: {config.max_proof_lines}",
        )
    )
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
    options: list[tuple[str, str]] = []
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
    SUB_TITLE = "model candidates are accepted only by the pinned Lean kernel"

    BINDINGS: ClassVar[list[Binding | tuple[str, str] | tuple[str, str, str]]] = [
        Binding("ctrl+q", "quit", "Quit"),
        Binding("/", "focus_filter", "Find target"),
        Binding("ctrl+d", "doctor", "Check system"),
        Binding("ctrl+i", "inspect", "Inspect goal"),
        Binding("ctrl+v", "validate", "Validate"),
        Binding("ctrl+s", "solve", "Accept proof"),
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
        height: 9;
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

    def compose(self) -> ComposeResult:
        initial_profile = resolve_profile(self.session.config.model)
        profile_value = initial_profile.name if initial_profile is not None else CUSTOM_MODEL
        custom_value = "" if initial_profile is not None else self.session.config.model
        backend_value = self.session.config.backend or PROFILE_DEFAULT
        effort_value = self.session.config.effort or PROFILE_DEFAULT

        yield Header(show_clock=False)
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
                    disabled=initial_profile is not None,
                )
                yield Label("Backend", classes="field-label")
                yield Select(
                    [
                        ("Use profile default", PROFILE_DEFAULT),
                        *((f"{name} · {BACKENDS[name].summary}", name) for name in BACKEND_NAMES),
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
                yield Static("", id="model-details", markup=False)
        with Horizontal(id="actions"):
            yield Button("Check system", id="doctor", classes="command")
            yield Button("Inspect goal", id="inspect", classes="command")
            yield Button("Validate", id="validate", variant="primary", classes="command")
            yield Button("Accept proof", id="solve", variant="warning", classes="command")
        yield Label(
            "Ready · Validate runs the complete model-to-kernel loop without project writes.",
            id="status",
            markup=False,
        )
        yield RichLog(id="activity", wrap=True, markup=False, highlight=False)
        yield Footer()

    def on_mount(self) -> None:
        self._show_targets(self.session.targets)
        self._update_model_details()

    def on_unmount(self) -> None:
        self._temporary_directory.cleanup()

    def on_resize(self, event: Resize) -> None:
        self.screen.set_class(event.size.width < 100, "narrow")

    @on(Input.Changed, "#target-filter")
    def filter_targets(self, event: Input.Changed) -> None:
        query = event.value.casefold().strip()
        targets = [
            target
            for target in self.session.targets
            if query
            in " ".join((target.decl_name, target.qualified_decl_name, target.rel_path, target.id)).casefold()
        ]
        self._show_targets(targets if query else self.session.targets)

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

    def action_focus_filter(self) -> None:
        self.query_one("#target-filter", Input).focus()

    def action_doctor(self) -> None:
        self._launch("doctor")

    def action_inspect(self) -> None:
        self._launch("inspect")

    def action_validate(self) -> None:
        self._launch("validate")

    def action_solve(self) -> None:
        target = self._selected_target()
        if target is None:
            self._show_error("Select a proof target to accept.")
            return
        self.push_screen(
            ConfirmSolve(target.qualified_decl_name or target.decl_name),
            self._confirmed_solve,
        )

    def _confirmed_solve(self, confirmed: bool | None) -> None:
        if confirmed:
            self._launch("solve")

    def _show_targets(self, targets: list[SorryTarget] | tuple[SorryTarget, ...]) -> None:
        option_list = self.query_one("#target-list", OptionList)
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
        settings = WorkbenchSettings(
            model=model,
            backend=backend,
            endpoint=endpoint,
            effort=effort,
            max_output_tokens=max_output_tokens,
            max_cycles=max_cycles,
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
        profile: ModelProfile | None = resolve_profile(value) if value != CUSTOM_MODEL else None
        if profile is None:
            details.update("Custom model · choose its backend and optional endpoint.")
            return
        setup = f"\nSetup: {profile.setup_command}" if profile.setup_command else ""
        details.update(f"{profile.description}\nBackend: {profile.backend}{setup}")

    def _launch(self, action: WorkbenchAction) -> None:
        try:
            target = None if action == "doctor" else self._selected_target()
            if action != "inspect":
                settings = self._settings()
                self.session.write_program(settings, self._session_program)
            plan = self.session.command(action, self._session_program, target)
        except (OSError, WorkbenchInputError, ValueError) as error:
            self._show_error(str(error))
            return
        self.run_plan(plan)

    def _show_error(self, message: str) -> None:
        self.query_one("#status", Label).update(f"Input error · {message}")
        self.notify(message, severity="error")

    def _set_commands_disabled(self, disabled: bool) -> None:
        for button in self.query("#actions Button").results(Button):
            button.disabled = disabled

    @work(exclusive=True, group="command")
    async def run_plan(self, plan: CommandPlan) -> None:
        """Stream one child CLI workflow into the activity log."""
        activity = self.query_one("#activity", RichLog)
        status = self.query_one("#status", Label)
        activity.clear()
        activity.write(Text(f"$ {plan.display}", style="bold cyan"))
        status.update(f"Running {plan.action}…")
        self._set_commands_disabled(True)
        environment = os.environ.copy()
        environment["PYTHONUNBUFFERED"] = "1"
        process: asyncio.subprocess.Process | None = None
        try:
            process = await asyncio.create_subprocess_exec(
                *plan.argv,
                cwd=plan.cwd,
                env=environment,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.STDOUT,
            )
            assert process.stdout is not None
            while line := await process.stdout.readline():
                activity.write(Text.from_ansi(line.decode(errors="replace").rstrip()))
            return_code = await process.wait()
            if return_code == 0:
                verb = "accepted" if plan.mutates_project else "completed"
                status.update(f"{plan.action.title()} {verb} successfully.")
                self.notify(f"{plan.action.title()} {verb} successfully.")
            else:
                status.update(f"{plan.action.title()} failed with exit code {return_code}.")
                self.notify(f"{plan.action.title()} failed.", severity="error")
        except asyncio.CancelledError:
            if process is not None and process.returncode is None:
                process.terminate()
                try:
                    await asyncio.wait_for(process.wait(), timeout=2)
                except TimeoutError:
                    process.kill()
                    await process.wait()
            status.update(f"Cancelled {plan.action}.")
            raise
        except OSError as error:
            status.update(f"Could not start {plan.action}: {error}")
            self.notify(str(error), severity="error")
        finally:
            self._set_commands_disabled(False)


def run_workbench(program_path: Path) -> None:
    """Load and run the interactive workbench."""
    AutoLeanWorkbench(WorkbenchSession.load(program_path)).run()
