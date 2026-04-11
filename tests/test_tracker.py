"""Tests for autolean.tracker — TSV logging, summary, ExperimentRecord."""

from __future__ import annotations

import csv
from datetime import datetime, timezone
from pathlib import Path

import pytest

from autolean.tracker import (
    ExperimentRecord,
    ExperimentTracker,
    Outcome,
    TSV_FIELDS,
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
        timestamp=datetime.now(timezone.utc).isoformat(),
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
