"""Persistent proof sessions preserve exact workflow state."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autolean.routing import EscalationPolicy, ModelTransition
from autolean.session import (
    ProofSession,
    SessionError,
    SessionKind,
    SessionStatus,
    SessionStore,
)


def test_session_round_trip_and_latest_target_lookup(tmp_path: Path) -> None:
    target = tmp_path / "AutoLean" / "Challenge_Collatz.lean"
    target.parent.mkdir()
    target.write_text("theorem target : True := by\n  sorry\n", encoding="utf-8")
    store = SessionStore(tmp_path)

    session = store.create(
        kind=SessionKind.PROBLEM,
        title="Collatz Conjecture",
        model="opus",
        backend="claude_cli",
        max_cycles=8,
        target_file=target,
        target_filter="collatz",
        guidance=("Prefer finite reductions.",),
        escalation_policy=EscalationPolicy.AUTO,
        escalation_model="fable",
        escalation_after_failures=3,
        session_id="20260811-collatz-00000001",
    )
    transition = ModelTransition(
        timestamp="2026-08-11T00:30:00Z",
        from_model="sonnet",
        from_backend="claude_cli",
        to_model="opus",
        to_backend="claude_cli",
        reason="three kernel-rejected attempts",
        failure_count=3,
    )
    paused = store.save(
        session.update(
            status=SessionStatus.PAUSED,
            model="opus",
            model_transitions=(transition,),
            remaining_targets=1,
            message="cycle budget reached",
        )
    )

    assert store.load(session.id) == paused
    assert store.latest() == paused
    assert store.find_target(target) == paused
    assert store.target_path(paused) == target
    assert paused.escalation_policy is EscalationPolicy.AUTO
    assert paused.escalation_model == "fable"
    assert paused.model_transitions == (transition,)
    assert json.loads((store.directory / f"{session.id}.json").read_text())["schema"] == (
        "autolean.proof-session.v1"
    )


def test_completed_session_is_not_the_latest_resumable_session(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    session = store.create(
        kind=SessionKind.PROJECT,
        title="Workspace",
        model="opus",
        backend="claude_cli",
        max_cycles=5,
        session_id="20260811-workspace-00000001",
    )
    completed = store.save(session.update(status=SessionStatus.COMPLETED, remaining_targets=0))

    with pytest.raises(SessionError, match="no resumable"):
        store.latest()
    assert store.latest(include_completed=True) == completed


def test_session_target_must_remain_inside_project(tmp_path: Path) -> None:
    store = SessionStore(tmp_path / "workspace")

    with pytest.raises(SessionError, match="inside the Lean project"):
        store.create(
            kind=SessionKind.THEOREM,
            title="Outside",
            model="opus",
            backend="claude_cli",
            max_cycles=5,
            target_file=tmp_path / "outside.lean",
            session_id="20260811-outside-00000001",
        )


def test_session_loader_rejects_filename_identity_mismatch(tmp_path: Path) -> None:
    store = SessionStore(tmp_path)
    store.directory.mkdir(parents=True)
    record = ProofSession(
        id="20260811-record-00000001",
        kind=SessionKind.PROJECT,
        title="Workspace",
        status=SessionStatus.READY,
        created_at="2026-08-11T00:00:00Z",
        updated_at="2026-08-11T00:00:00Z",
        model="opus",
        backend="claude_cli",
        max_cycles=5,
    )
    (store.directory / "20260811-other-00000001.json").write_text(
        json.dumps(record.as_dict()),
        encoding="utf-8",
    )

    with pytest.raises(SessionError, match="does not match"):
        store.load("20260811-other-00000001")
