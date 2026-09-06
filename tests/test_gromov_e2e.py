"""The Gromov example crosses the installed CLI and pinned Lean boundary."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

from autolean.lean_interface import LeanProject
from autolean.progress import PROGRESS_PREFIX, ProgressEvent, ProgressKind
from autolean.scanner import scan_file
from tests import test_tutorial_e2e as tutorial
from tests.test_tutorial_e2e import project_root as project_root
from tests.test_tutorial_e2e import scripted_model as scripted_model

pytestmark = tutorial.pytestmark

_CONVERSE_PROOF = """classical
apply Group.residuallyFinite_iff_exists_finiteIndexNormalSubgroup.mpr
intro g hg
obtain ⟨N, hN⟩ := h {g, 1}
refine ⟨N, ?_⟩
intro hgN
apply hg
apply hN (by simp) (by simp)
rw [map_one]
exact (QuotientGroup.eq_one_iff g).mpr hgN"""


def test_gromov_research_reports_correction_learning_and_an_open_question(
    tmp_path: Path,
    project_root: Path,
    scripted_model: int,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An accepted supporting lemma leaves the open implication unresolved."""
    binary = shutil.which("autolean")
    assert binary is not None
    scaffold = tmp_path / "scaffold"
    scaffold.mkdir()
    initialized = subprocess.run(
        [binary, "init", "research", "--example", "gromov", "--no-cslib"],
        cwd=scaffold,
        check=True,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert "question remains open" in initialized.stdout
    source = project_root / "Gromov.lean"
    source.write_bytes((scaffold / "research/research.lean").read_bytes())
    guards = Path(__file__).parent / "fixtures" / "gromov_guards.lean"
    checked = LeanProject(project_root).validate_candidate(
        source,
        source.read_text() + "\n" + guards.read_text(),
        timeout=240,
    )
    assert checked.success, checked.stderr
    program = tmp_path / "gromov.md"
    program.write_text(
        "## Mode\n\nsorry-elimination\n\n"
        f"## Lean Project Path\n\n{project_root}\n\n"
        "## LLM Configuration\n\n"
        f"model: {tutorial._MODEL}\nprovider: compatible\n"
        f"endpoint: http://127.0.0.1:{scripted_model}\n"
        "search_scope: local\nescalation_policy: never\n\n"
        "## Experiment Budget\n\n"
        "max_cycles: 2\nmax_retries_per_sorry: 2\n"
        "cycle_timeout_seconds: 240\n",
        encoding="utf-8",
    )
    prompts: list[str] = []
    proof_calls = 0
    open_phase = False

    def reply(system: str, user: str) -> str:
        nonlocal proof_calls
        if "mathematical research planner" in system:
            plan = dict(tutorial._PLAN)
            plan["objective"] = "Investigate finite quotients under the stated group hypotheses."
            return json.dumps(plan)
        prompts.append(user)
        proof_calls += 1
        if open_phase:
            return "exact Gromov.finite_group"
        if proof_calls == 1:
            return "exact missing_finite_quotient_lemma"
        return _CONVERSE_PROOF

    monkeypatch.setattr(tutorial, "_scripted_reply", reply)

    def solve(target: str, cycles: int) -> list[ProgressEvent]:
        result = subprocess.run(
            [binary, "solve", "--program", str(program), "--target", target, "--max-cycles", str(cycles)],
            cwd=tmp_path,
            env={**os.environ, "AUTOLEAN_PROGRESS": "json"},
            capture_output=True,
            text=True,
            timeout=900,
        )
        assert result.returncode == 0, result.stdout + result.stderr
        return [
            event
            for line in result.stdout.splitlines()
            if line.startswith(PROGRESS_PREFIX)
            if (event := ProgressEvent.from_line(line)) is not None
        ]

    events = solve("residually_finite_of_finite_quotients", 2)
    feedback = [
        event.message for event in events if event.kind is ProgressKind.FEEDBACK and event.attempt > 0
    ]
    assert feedback == ["fail_build", "success"]
    lessons = [event.message for event in events if event.kind is ProgressKind.LEARNING]
    assert any("Self-correction" in lesson for lesson in lessons)
    assert any("Learned pattern" in lesson for lesson in lessons)
    assert any("missing_finite_quotient_lemma" in prompt for prompt in prompts[1:])
    assert events[-1].kind is ProgressKind.FINISHED
    assert "0 proof target" in events[-1].message
    targets = {target.decl_name: target for target in scan_file(source)}
    assert set(targets) == {"finite_quotient_injective", "residual_finiteness_question"}
    accepted = source.read_bytes()
    assert list((project_root / "skills").glob("*.json"))

    open_phase = True
    events = solve("residual_finiteness_question", 1)
    assert any(
        event.kind is ProgressKind.LEARNING and "from accepted proofs" in event.message for event in events
    )
    assert any(event.kind is ProgressKind.FEEDBACK and event.message == "fail_build" for event in events)
    assert events[-1].kind is ProgressKind.FINISHED
    assert "1 proof target" in events[-1].message
    assert source.read_bytes() == accepted

    target = targets["residual_finiteness_question"]
    audit = LeanProject(project_root).validate_candidate(
        source,
        source.read_text(),
        timeout=240,
        declaration=target.qualified_decl_name,
        declaration_line=target.line,
    )
    assert not audit.success
    assert "sorryAx" in audit.axioms
