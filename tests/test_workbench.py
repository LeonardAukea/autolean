"""Headless interaction and command-planning tests for the workbench."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import pytest
from textual.widgets import Button, Input, Label, Log, OptionList, Select, Static, TabbedContent

from autolean.program import parse_program
from autolean.progress import PROGRESS_PREFIX, ProgressEvent, ProgressKind
from autolean.routing import EscalationPolicy
from autolean.workbench import (
    CUSTOM_MODEL,
    AutoLeanWorkbench,
    CommandPlan,
    ConfirmSolve,
    WorkbenchInputError,
    WorkbenchSession,
    WorkbenchSettings,
    _interrupt_child,
    _profile_options,
)


def _program(tmp_path: Path) -> Path:
    project = tmp_path / "lean"
    project.mkdir()
    (project / "lakefile.lean").write_text("package «test» where\n", encoding="utf-8")
    (project / "Main.lean").write_text(
        "namespace Algebra\n\n"
        "theorem identity : True := by\n"
        "  sorry\n\n"
        "theorem second : True := by\n"
        "  sorry\n\n"
        "end Algebra\n",
        encoding="utf-8",
    )
    program = tmp_path / "program.md"
    program.write_text(
        "# Test Program\n\n"
        "## Mode\n\n"
        "sorry-elimination\n\n"
        "## Lean Project Path\n\n"
        "lean\n\n"
        "## Goals\n\n"
        "- Prove the selected theorem.\n\n"
        "## Constraints\n\n"
        "- Preserve the statement.\n\n"
        "## Strategy Hints\n\n"
        "- Try trivial.\n\n"
        "## LLM Configuration\n\n"
        "model: opus\n"
        "temperature: 0.0\n\n"
        "## Experiment Budget\n\n"
        "max_cycles: 0\n",
        encoding="utf-8",
    )
    return program


def test_automatic_profile_is_the_first_workbench_choice() -> None:
    assert _profile_options()[0] == ("auto · Strongest authenticated provider", "auto")


def test_workbench_preserves_automatic_machine_selection(tmp_path: Path) -> None:
    async def exercise() -> None:
        program = _program(tmp_path)
        source = program.read_text(encoding="utf-8").replace("model: opus", "model: auto")
        program.write_text(source, encoding="utf-8")
        app = AutoLeanWorkbench(WorkbenchSession.load(program))

        async with app.run_test() as pilot:
            await pilot.pause()
            assert app.query_one("#model-profile", Select).value == "auto"
            assert app.query_one("#custom-model", Input).disabled

    asyncio.run(exercise())


def test_session_program_and_commands_share_the_cli_contract(tmp_path: Path) -> None:
    session = WorkbenchSession.load(_program(tmp_path))
    settings = WorkbenchSettings(
        model="muse-glimmer",
        backend="muse_glimmer",
        endpoint="http://127.0.0.1:8080",
        effort="low",
        max_output_tokens=4096,
        max_cycles=2,
        escalation_policy=EscalationPolicy.AUTO,
        escalation_model="opus",
        escalation_after_failures=3,
        guidance="Use the local identity theorem.",
    )
    session_program = tmp_path / "session.md"
    session.write_program(settings, session_program)

    parsed = parse_program(session_program)
    rendered = session_program.read_text(encoding="utf-8")
    assert "provider: muse" in rendered
    assert "\nbackend:" not in rendered
    assert parsed.model == "muse-glimmer"
    assert parsed.backend == "muse_glimmer"
    assert parsed.endpoint == "http://127.0.0.1:8080"
    assert parsed.effort == "low"
    assert parsed.max_output_tokens == 4096
    assert parsed.max_cycles == 2
    assert parsed.escalation_policy is EscalationPolicy.AUTO
    assert parsed.escalation_model == "opus"
    assert parsed.escalation_after_failures == 3
    assert parsed.strategy_hints[-1] == "Use the local identity theorem."
    assert Path(parsed.lean_project_path) == session.lean_root

    target = session.targets[0]
    validation = session.command("validate", session_program, target)
    acceptance = session.command("solve", session_program, target)
    assert validation.argv[-1] == "--dry-run"
    assert not validation.mutates_project
    assert "--dry-run" not in acceptance.argv
    assert "--resume" in acceptance.argv
    assert acceptance.mutates_project
    assert target.id in validation.argv


@pytest.mark.parametrize(
    "settings_args, message",
    [
        (
            ("two words", None, None, None, None, 1),
            "one non-empty token",
        ),
        (
            ("opus", None, "file:///tmp/model", None, None, 1),
            "absolute HTTP or HTTPS",
        ),
        (
            ("opus", None, None, None, None, 0),
            "cycles must be positive",
        ),
    ],
)
def test_workbench_settings_fail_closed(
    tmp_path: Path,
    settings_args: tuple[object, ...],
    message: str,
) -> None:
    session = WorkbenchSession.load(_program(tmp_path))
    with pytest.raises(WorkbenchInputError, match=message):
        settings = WorkbenchSettings(*settings_args)  # type: ignore[arg-type]
        settings.program_config(session.config)


@pytest.mark.parametrize("size", [(80, 24), (140, 45)])
def test_workbench_runs_headlessly_at_common_terminal_sizes(
    tmp_path: Path,
    size: tuple[int, int],
) -> None:
    async def exercise() -> None:
        app = AutoLeanWorkbench(WorkbenchSession.load(_program(tmp_path)))
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            targets = app.query_one("#target-list", OptionList)
            assert targets.option_count == 2
            assert app.screen.has_class("narrow") is (size[0] < 100)

            app.query_one("#target-filter", Input).value = "identity"
            await pilot.pause()
            assert targets.option_count == 1

            app.query_one("#model-profile", Select).value = CUSTOM_MODEL
            await pilot.pause()
            assert not app.query_one("#custom-model", Input).disabled
            assert app.query_one("#max-cycles", Input).value == "1"
            assert app.query_one("#escalation-policy", Select).value == "ask"
            assert app.query_one("#escalation-after", Input).value == "2"
            assert app.query_one("#stop", Button).disabled

            await pilot.click("#solve")
            await pilot.pause()
            assert isinstance(app.screen, ConfirmSolve)
            await pilot.click("#cancel-solve")
            await pilot.pause()
            assert not isinstance(app.screen, ConfirmSolve)

    asyncio.run(exercise())


@pytest.mark.parametrize("size", [(80, 24), (140, 45)])
def test_research_views_show_real_child_events_without_claiming_a_proof(
    tmp_path: Path, size: tuple[int, int]
) -> None:
    async def exercise() -> None:
        app = AutoLeanWorkbench(WorkbenchSession.load(_program(tmp_path)))
        events = [
            ProgressEvent(ProgressKind.TARGET, "Attempt 1", "Gromov hypothesis", "Gromov.question", 1, 1),
            ProgressEvent(
                ProgressKind.GOAL,
                "Lean goal",
                "unsolved goals\nG : Type u\ninst : Group G\nh : Gromov.IsWordHyperbolic G\n"
                "⊢ Group.ResiduallyFinite G",
            ),
            ProgressEvent(ProgressKind.CANDIDATE, "Awaiting Lean", "exact missing_lemma"),
            ProgressEvent(ProgressKind.FEEDBACK, "FAIL_BUILD", "Unknown identifier missing_lemma"),
            ProgressEvent(
                ProgressKind.LEARNING, "Self-correction", "The rejected candidate informs the next attempt."
            ),
            ProgressEvent(ProgressKind.SUMMARY, "0 accepted · 1 open · 0 eligible"),
            ProgressEvent(ProgressKind.FINISHED, "Run finished · 1 proof target remains open"),
        ]
        payload = "\n".join(PROGRESS_PREFIX + event.to_json() for event in events)
        command = CommandPlan("solve", (sys.executable, "-c", f"print({payload!r})"), tmp_path, False)
        async with app.run_test(size=size) as pilot:
            await pilot.pause()
            worker = app.run_plan(command)
            await worker.wait()
            await pilot.pause()
            assert app.query_one("#workspace", TabbedContent).active == "research"
            goal = app.query_one("#research-goal", Static)
            assert str(goal.render()).startswith("⊢ Group.ResiduallyFinite G")
            assert app.query_one("#learning-pane").region.bottom <= app.query_one("#actions").region.y
            assert "missing_lemma" in str(app.query_one("#candidate", Static).render())
            assert "FAIL_BUILD" in str(app.query_one("#feedback", Static).render())
            assert "Self-correction" in str(app.query_one("#learning", Static).render())
            assert "1 open" in str(app.query_one("#run-progress", Static).render())
            status = str(app.query_one("#status", Label).render())
            assert "finished" in status
            assert "accepted successfully" not in status
            assert app.query_one("#stop", Button).disabled

    asyncio.run(exercise())


def test_new_attempt_clears_previous_candidate_and_verdict(tmp_path: Path) -> None:
    async def exercise() -> None:
        app = AutoLeanWorkbench(WorkbenchSession.load(_program(tmp_path)))
        async with app.run_test() as pilot:
            await pilot.pause()
            app._receive_progress(ProgressEvent(ProgressKind.CANDIDATE, "Candidate", "exact old"))
            app._receive_progress(ProgressEvent(ProgressKind.FEEDBACK, "SUCCESS", "Accepted"))
            app._receive_progress(
                ProgressEvent(ProgressKind.TARGET, "Attempt 2", "new goal", "new_target", 2, 2)
            )
            assert "exact old" not in str(app.query_one("#candidate", Static).render())
            assert "no Lean verdict" in str(app.query_one("#feedback", Static).render())

    asyncio.run(exercise())


@pytest.mark.parametrize("keyboard", [False, True])
def test_confirmed_run_keeps_research_visible_and_respects_the_target_filter(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, keyboard: bool
) -> None:
    async def exercise() -> None:
        program = _program(tmp_path)
        program.write_text(program.read_text().replace("temperature: 0.0\n", ""))
        session = WorkbenchSession.load(program)
        app = AutoLeanWorkbench(session)
        event = ProgressEvent(ProgressKind.FINISHED, "Run finished · 1 proof target remains open")
        payload = PROGRESS_PREFIX + event.to_json()
        plan = CommandPlan("solve", (sys.executable, "-c", f"print({payload!r})"), tmp_path, True)
        monkeypatch.setattr(WorkbenchSession, "command", lambda *args: plan)
        async with app.run_test() as pilot:
            app.query_one("#target-filter", Input).value = "identity"
            await pilot.pause()
            if keyboard:
                app.query_one("#target-filter", Input).focus()
                await pilot.press("ctrl+s")
                await pilot.pause()
                await pilot.press("tab", "enter")
            else:
                await pilot.click("#solve")
                await pilot.pause()
                await pilot.click("#accept-solve")
            await pilot.pause()
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert app.query_one("#workspace", TabbedContent).active == "research", str(
                app.query_one("#status", Label).render()
            )
            assert app.query_one("#target-list", OptionList).option_count == 1
            assert app._selected_target().decl_name == "identity"

    asyncio.run(exercise())


def test_workbench_cancellation_drains_output_and_reaps_owned_processes(tmp_path: Path) -> None:
    async def exercise() -> None:
        child_pid = tmp_path / "child.pid"
        receipt = tmp_path / "cleanup.txt"
        script = """
import os
import signal
import sys
from pathlib import Path
from autolean.process import run_process

interrupts = 0
def stop(signum, frame):
    global interrupts
    interrupts += 1
    if interrupts == 1:
        print("x" * 262144, flush=True)
        return
    raise KeyboardInterrupt

signal.signal(signal.SIGINT, stop)
child = "import os, sys, time; from pathlib import Path; " \
        "Path(sys.argv[1]).write_text(str(os.getpid())); time.sleep(60)"
try:
    run_process([sys.executable, "-c", child, sys.argv[1]], timeout=60)
except KeyboardInterrupt:
    Path(sys.argv[2]).write_text("owned process reaped")
"""
        process = await asyncio.create_subprocess_exec(
            sys.executable,
            "-c",
            script,
            str(child_pid),
            str(receipt),
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.STDOUT,
            start_new_session=True,
        )
        try:
            async with asyncio.timeout(10):
                while not child_pid.exists():
                    await asyncio.sleep(0.02)
                await _interrupt_child(process)
            assert process.returncode == 0
            assert receipt.read_text() == "owned process reaped"
            with pytest.raises(ProcessLookupError):
                os.kill(int(child_pid.read_text()), 0)
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()

    asyncio.run(exercise())


def test_hidden_transcript_enforces_its_bound_before_first_display(tmp_path: Path) -> None:
    async def exercise() -> None:
        app = AutoLeanWorkbench(WorkbenchSession.load(_program(tmp_path)))
        async with app.run_test() as pilot:
            await pilot.pause()
            transcript = app.query_one("#activity", Log)
            assert app.query_one("#workspace", TabbedContent).active == "setup"
            for index in range(2000):
                app._receive_output(f"diagnostic {index}")
            app._receive_output("x" * 65536)
            assert len(app._transcript_pending) == 1000
            await pilot.pause()
            assert not app._transcript_pending
            assert len(transcript.lines) <= 1000
            assert all(len(line) <= 8192 for line in transcript.lines)
            assert "diagnostic 1999" in transcript.lines
            assert "diagnostic 0" not in transcript.lines
            app.query_one("#workspace", TabbedContent).active = "transcript"
            await pilot.pause()
            assert len(transcript.lines) <= 1000

    asyncio.run(exercise())


def test_shortcuts_preserve_the_active_command_and_stop_control(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def exercise() -> None:
        program = _program(tmp_path)
        program.write_text(program.read_text().replace("temperature: 0.0\n", ""))
        app = AutoLeanWorkbench(WorkbenchSession.load(program))
        calls = []
        script = "import time; print('ready', flush=True); time.sleep(60)"
        plan = CommandPlan("validate", (sys.executable, "-c", script), tmp_path, False)

        def command(*args: object) -> CommandPlan:
            calls.append(args)
            return plan

        monkeypatch.setattr(WorkbenchSession, "command", command)
        async with app.run_test() as pilot:
            app.action_validate()
            app.action_doctor()
            async with asyncio.timeout(10):
                while "ready" not in app.query_one("#activity", Log).lines:
                    await pilot.pause()
            worker, process = app._worker, app._process
            assert worker is not None and process is not None
            for shortcut in ("ctrl+d", "ctrl+i", "ctrl+v", "ctrl+s"):
                await pilot.press(shortcut)
            await pilot.pause()
            assert len(calls) == 1
            assert app._worker is worker and app._process is process
            assert process.returncode is None
            assert not app.query_one("#stop", Button).disabled
            assert not isinstance(app.screen, ConfirmSolve)
            app.action_stop()
            await worker.wait()
            assert process.returncode is not None
            assert app._process is None
            assert app.query_one("#stop", Button).disabled

    asyncio.run(exercise())


def test_new_run_scopes_all_research_evidence_even_on_preflight_failure(tmp_path: Path) -> None:
    async def exercise() -> None:
        app = AutoLeanWorkbench(WorkbenchSession.load(_program(tmp_path)))
        plan = CommandPlan("validate", (sys.executable, "-c", "raise SystemExit(1)"), tmp_path, False)
        async with app.run_test() as pilot:
            app._receive_progress(ProgressEvent(ProgressKind.TARGET, "Old target", "Old goal", "old_lemma"))
            app._receive_progress(ProgressEvent(ProgressKind.CANDIDATE, "Candidate", "exact old_proof"))
            app._receive_progress(ProgressEvent(ProgressKind.FEEDBACK, "SUCCESS", "Accepted"))
            app._receive_progress(ProgressEvent(ProgressKind.LEARNING, "Old lesson"))
            await app.run_plan(plan).wait()
            await pilot.pause()
            displayed = "\n".join(
                str(app.query_one(selector, Static).render())
                for selector in ("#run-progress", "#research-goal", "#candidate", "#feedback", "#learning")
            )
            for previous in ("old_lemma", "Old goal", "old_proof", "SUCCESS", "Old lesson"):
                assert previous not in displayed
            assert "failed with exit code 1" in str(app.query_one("#status", Label).render())

    asyncio.run(exercise())


@pytest.mark.parametrize("tab", ["research", "transcript"])
def test_find_target_activates_setup_from_every_tab(tmp_path: Path, tab: str) -> None:
    async def exercise() -> None:
        app = AutoLeanWorkbench(WorkbenchSession.load(_program(tmp_path)))
        async with app.run_test() as pilot:
            app.query_one("#workspace", TabbedContent).active = tab
            await pilot.pause()
            await pilot.press("ctrl+f")
            await pilot.pause()
            assert app.query_one("#workspace", TabbedContent).active == "setup"
            assert app.focused is app.query_one("#target-filter", Input)

    asyncio.run(exercise())


@pytest.mark.parametrize("quit_app", [False, True])
def test_stop_and_quit_preserve_intent_while_the_child_starts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, quit_app: bool
) -> None:
    async def exercise() -> None:
        app = AutoLeanWorkbench(WorkbenchSession.load(_program(tmp_path)))
        spawn_started, allow_spawn = asyncio.Event(), asyncio.Event()
        children = []
        create = asyncio.create_subprocess_exec

        async def delayed(*args: object, **kwargs: object) -> asyncio.subprocess.Process:
            spawn_started.set()
            await allow_spawn.wait()
            child = await create(*args, **kwargs)
            children.append(child)
            return child

        monkeypatch.setattr(asyncio, "create_subprocess_exec", delayed)
        script = "import time; time.sleep(60)"
        plan = CommandPlan("validate", (sys.executable, "-c", script), tmp_path, False)
        async with app.run_test() as pilot:
            worker = app.run_plan(plan)
            await asyncio.wait_for(spawn_started.wait(), 10)
            assert not app.query_one("#stop", Button).disabled
            if quit_app:
                await app.action_quit()
            else:
                app.action_stop()
            assert app._stop_requests == 1
            allow_spawn.set()
            await asyncio.wait_for(worker.wait(), 10)
            await pilot.pause()
            assert len(children) == 1 and children[0].returncode is not None
            assert app._process is None

    asyncio.run(exercise())


def test_output_failure_still_reaps_the_owned_child(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    async def exercise() -> None:
        app = AutoLeanWorkbench(WorkbenchSession.load(_program(tmp_path)))
        children = []
        create = asyncio.create_subprocess_exec

        async def capture(*args: object, **kwargs: object) -> asyncio.subprocess.Process:
            child = await create(*args, **kwargs)
            children.append(child)
            return child

        def fail_output(line: str) -> None:
            raise OSError("display output failed")

        monkeypatch.setattr(asyncio, "create_subprocess_exec", capture)
        monkeypatch.setattr(app, "_receive_output", fail_output)
        script = "import time; print('ready', flush=True); time.sleep(60)"
        plan = CommandPlan("validate", (sys.executable, "-c", script), tmp_path, False)
        async with app.run_test() as pilot:
            await app.run_plan(plan).wait()
            await pilot.pause()
            assert len(children) == 1 and children[0].returncode is not None
            assert app._process is None and app._worker is None
            assert app.query_one("#stop", Button).disabled

    asyncio.run(exercise())
