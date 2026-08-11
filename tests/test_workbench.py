"""Headless interaction and command-planning tests for the workbench."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest
from textual.widgets import Button, Input, OptionList, Select

from autolean.program import parse_program
from autolean.routing import EscalationPolicy
from autolean.workbench import (
    CUSTOM_MODEL,
    AutoLeanWorkbench,
    ConfirmSolve,
    WorkbenchInputError,
    WorkbenchSession,
    WorkbenchSettings,
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
    "settings, message",
    [
        (
            WorkbenchSettings("two words", None, None, None, None, 1),
            "one non-empty token",
        ),
        (
            WorkbenchSettings("opus", None, "file:///tmp/model", None, None, 1),
            "absolute HTTP or HTTPS",
        ),
        (
            WorkbenchSettings("opus", None, None, None, None, 0),
            "cycles must be positive",
        ),
    ],
)
def test_workbench_settings_fail_closed(
    tmp_path: Path,
    settings: WorkbenchSettings,
    message: str,
) -> None:
    session = WorkbenchSession.load(_program(tmp_path))
    with pytest.raises(WorkbenchInputError, match=message):
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
