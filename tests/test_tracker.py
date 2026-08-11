"""Tests for autolean.tracker — TSV logging, summary, ExperimentRecord."""

from __future__ import annotations

import csv
import subprocess
from datetime import UTC, datetime
from pathlib import Path

import pytest

from autolean.tracker import (
    TSV_FIELDS,
    ExperimentRecord,
    ExperimentTracker,
    GitError,
    Outcome,
)

# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_record(
    cycle: int = 1,
    outcome: Outcome = Outcome.SUCCESS,
    target_id: str = "Foo.lean:5:bar",
    error_summary: str = "",
    error_category: str = "",
    build_duration: float = 1.5,
) -> ExperimentRecord:
    """Create a minimal ExperimentRecord for testing."""
    return ExperimentRecord(
        cycle=cycle,
        timestamp=datetime.now(UTC).isoformat(),
        target_id=target_id,
        decl_name="bar",
        file="Foo.lean",
        line=5,
        outcome=outcome,
        attempt=1,
        duration_seconds=2.3,
        llm_tokens=100,
        llm_tok_per_sec=50.0,
        error_summary=error_summary,
        error_category=error_category,
        build_duration_seconds=build_duration,
    )


# ---------------------------------------------------------------------------
# ExperimentRecord.as_dict
# ---------------------------------------------------------------------------


class TestExperimentRecordAsDict:
    """Tests for the as_dict serialization."""

    def test_includes_error_category(self) -> None:
        rec = _make_record(error_category="type_mismatch")
        d = rec.as_dict()
        assert "error_category" in d
        assert d["error_category"] == "type_mismatch"

    def test_includes_build_s(self) -> None:
        rec = _make_record(build_duration=3.14)
        d = rec.as_dict()
        assert "build_s" in d
        assert d["build_s"] == 3.1  # rounded to 1 decimal

    def test_error_truncated_at_200_chars(self) -> None:
        long_error = "x" * 500
        rec = _make_record(error_summary=long_error)
        d = rec.as_dict()
        assert len(d["error"]) == 200

    def test_short_error_not_truncated(self) -> None:
        short = "type mismatch"
        rec = _make_record(error_summary=short)
        d = rec.as_dict()
        assert d["error"] == short

    def test_all_tsv_fields_present(self) -> None:
        rec = _make_record()
        d = rec.as_dict()
        for field in TSV_FIELDS:
            assert field in d, f"Missing field: {field}"

    def test_outcome_serialized_as_value(self) -> None:
        rec = _make_record(outcome=Outcome.FAIL_BUILD)
        d = rec.as_dict()
        assert d["outcome"] == "fail_build"

    def test_serializes_proof_provenance_and_axioms(self) -> None:
        rec = _make_record()
        rec.environment_sha256 = "a" * 64
        rec.proof_sha256 = "b" * 64
        rec.axioms = "none"
        rec.model = "gpt-5.6-luna"
        rec.backend = "codex_cli"

        serialized = rec.as_dict()

        assert serialized["environment_sha256"] == "a" * 64
        assert serialized["proof_sha256"] == "b" * 64
        assert serialized["axioms"] == "none"
        assert serialized["model"] == "gpt-5.6-luna"
        assert serialized["backend"] == "codex_cli"


# ---------------------------------------------------------------------------
# ExperimentTracker — TSV logging
# ---------------------------------------------------------------------------


class TestTrackerTsvLogging:
    """Tests for TSV file creation and appending."""

    def test_first_log_creates_file_with_header(self, tmp_path: Path) -> None:
        tracker = ExperimentTracker(project_root=tmp_path)
        tracker.log(_make_record())

        tsv = tmp_path / "results.tsv"
        assert tsv.exists()

        with open(tsv, newline="") as f:
            reader = csv.reader(f, delimiter="\t")
            header = next(reader)
            assert header == TSV_FIELDS

    def test_subsequent_log_appends(self, tmp_path: Path) -> None:
        tracker = ExperimentTracker(project_root=tmp_path)
        tracker.log(_make_record(cycle=1))
        tracker.log(_make_record(cycle=2))

        tsv = tmp_path / "results.tsv"
        with open(tsv, newline="") as f:
            reader = csv.reader(f, delimiter="\t")
            rows = list(reader)

        # 1 header + 2 data rows
        assert len(rows) == 3

    def test_header_not_duplicated(self, tmp_path: Path) -> None:
        tracker = ExperimentTracker(project_root=tmp_path)
        tracker.log(_make_record(cycle=1))
        tracker.log(_make_record(cycle=2))
        tracker.log(_make_record(cycle=3))

        tsv = tmp_path / "results.tsv"
        content = tsv.read_text()
        # The header line should appear exactly once
        assert content.count("cycle\t") == 1

    def test_older_schema_is_migrated_without_losing_rows(self, tmp_path: Path) -> None:
        new_fields = {
            "environment_sha256",
            "proof_sha256",
            "axioms",
            "model",
            "backend",
        }
        old_fields = [field for field in TSV_FIELDS if field not in new_fields]
        old_row = {field: "" for field in old_fields}
        old_row.update({"cycle": "1", "outcome": "success", "decl_name": "old"})
        with open(tmp_path / "results.tsv", "w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=old_fields, delimiter="\t")
            writer.writeheader()
            writer.writerow(old_row)

        tracker = ExperimentTracker(project_root=tmp_path)
        tracker.log(_make_record(cycle=2))

        with open(tmp_path / "results.tsv", newline="") as handle:
            rows = list(csv.DictReader(handle, delimiter="\t"))
        assert len(rows) == 2
        assert rows[0]["decl_name"] == "old"
        assert rows[0]["environment_sha256"] == ""
        assert rows[0]["model"] == ""
        assert rows[1]["proof_sha256"] == ""


# ---------------------------------------------------------------------------
# ExperimentTracker — summary
# ---------------------------------------------------------------------------


class TestTrackerSummary:
    """Tests for summary aggregation."""

    def test_summary_counts_by_outcome(self, tmp_path: Path) -> None:
        tracker = ExperimentTracker(project_root=tmp_path)
        tracker.log(_make_record(cycle=1, outcome=Outcome.SUCCESS))
        tracker.log(_make_record(cycle=2, outcome=Outcome.SUCCESS))
        tracker.log(_make_record(cycle=3, outcome=Outcome.FAIL_BUILD))

        s = tracker.summary()
        assert s["success"] == 2
        assert s["fail_build"] == 1

    def test_summary_empty_tracker(self, tmp_path: Path) -> None:
        tracker = ExperimentTracker(project_root=tmp_path)
        assert tracker.summary() == {}

    def test_all_outcome_types(self, tmp_path: Path) -> None:
        tracker = ExperimentTracker(project_root=tmp_path)
        for outcome in Outcome:
            tracker.log(_make_record(outcome=outcome))
        s = tracker.summary()
        for outcome in Outcome:
            assert s[outcome.value] == 1


# ---------------------------------------------------------------------------
# ExperimentTracker — cycle counter
# ---------------------------------------------------------------------------


class TestTrackerCycleCounter:
    """Tests for next_cycle."""

    def test_next_cycle_increments(self, tmp_path: Path) -> None:
        tracker = ExperimentTracker(project_root=tmp_path)
        assert tracker.next_cycle() == 1
        assert tracker.next_cycle() == 2
        assert tracker.next_cycle() == 3

    def test_cycle_starts_at_zero(self, tmp_path: Path) -> None:
        tracker = ExperimentTracker(project_root=tmp_path)
        assert tracker.cycle == 0


def _git(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-c", "core.fsmonitor=false", *args],
        cwd=cwd,
        capture_output=True,
        text=True,
        check=True,
    )


def _repository(tmp_path: Path) -> tuple[Path, Path]:
    workspace = tmp_path / "workspace"
    source = workspace / "AutoLean" / "Target.lean"
    source.parent.mkdir(parents=True)
    source.write_text("theorem target : True := by\n  sorry\n")
    (tmp_path / "unrelated.txt").write_text("accepted\n")
    _git(tmp_path, "init")
    _git(tmp_path, "config", "user.name", "AutoLean Test")
    _git(tmp_path, "config", "user.email", "autolean@example.invalid")
    _git(tmp_path, "config", "commit.gpgsign", "false")
    _git(tmp_path, "add", "--", ".")
    _git(tmp_path, "commit", "-m", "test: Initial state")
    return workspace, source


def test_proof_commit_contains_only_the_proven_file(tmp_path: Path) -> None:
    workspace, source = _repository(tmp_path)
    tracker = ExperimentTracker(workspace)
    tracker.setup_branch("autolean/test")

    source.write_text("theorem target : True := by\n  trivial\n")
    unrelated = tmp_path / "unrelated.txt"
    unrelated.write_text("user change\n")
    _git(tmp_path, "add", "--", "unrelated.txt")
    (tmp_path / "untracked.txt").write_text("user artifact\n")

    record = _make_record(target_id="AutoLean/Target.lean:2:target")
    record.file = "AutoLean/Target.lean"
    tracker.commit_success(record)

    committed = _git(
        tmp_path,
        "show",
        "--pretty=format:",
        "--name-only",
        "HEAD",
    ).stdout.split()
    assert committed == ["workspace/AutoLean/Target.lean"]
    assert _git(tmp_path, "diff", "--cached", "--name-only").stdout.strip() == ("unrelated.txt")
    assert (tmp_path / "untracked.txt").read_text() == "user artifact\n"


def test_existing_branch_does_not_fall_through_to_current_branch(
    tmp_path: Path,
) -> None:
    workspace, _ = _repository(tmp_path)
    original = _git(tmp_path, "branch", "--show-current").stdout.strip()
    _git(tmp_path, "branch", "autolean/existing")

    tracker = ExperimentTracker(workspace)
    with pytest.raises(GitError, match="already exists"):
        tracker.setup_branch("autolean/existing")
    assert _git(tmp_path, "branch", "--show-current").stdout.strip() == original


def test_git_process_start_failure_uses_the_typed_boundary(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    def fail(*args: object, **kwargs: object) -> subprocess.CompletedProcess[str]:
        del args, kwargs
        raise FileNotFoundError("git unavailable")

    monkeypatch.setattr("autolean.tracker.subprocess.run", fail)

    with pytest.raises(GitError, match="could not run git"):
        ExperimentTracker(tmp_path).setup_branch("autolean/test")
