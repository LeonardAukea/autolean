"""Tests for training data collector."""

from __future__ import annotations

import json
from pathlib import Path

from autolean.collector import TrainingDataCollector
from autolean.tracker import ExperimentRecord, Outcome


def _make_record(
    decl: str = "foo",
    outcome: Outcome = Outcome.SUCCESS,
    attempt: int = 1,
    tokens: int = 100,
    error_cat: str = "",
    error_msg: str = "",
) -> ExperimentRecord:
    return ExperimentRecord(
        cycle=1,
        timestamp="2026-01-01T00:00:00Z",
        target_id=f"File.lean:10:{decl}",
        decl_name=decl,
        file="File.lean",
        line=10,
        outcome=outcome,
        attempt=attempt,
        duration_seconds=5.0,
        llm_tokens=tokens,
        llm_tok_per_sec=50.0,
        error_summary=error_msg,
        error_category=error_cat,
        model="gpt-5.6-luna",
        backend="codex_cli",
    )


class TestTrainingDataCollector:
    def test_record_attempt_stores_example(self, tmp_path: Path) -> None:
        c = TrainingDataCollector(output_dir=tmp_path)
        c.set_context("File.lean:10:foo", "goal: True", "context code")
        r = _make_record()
        c.record_attempt(r, "rfl")
        assert len(c.examples) == 1
        assert c.examples[0].success is True
        assert c.examples[0].proof == "rfl"

    def test_stats_counts_correctly(self, tmp_path: Path) -> None:
        c = TrainingDataCollector(output_dir=tmp_path)
        c.record_attempt(_make_record(outcome=Outcome.SUCCESS), "rfl")
        c.record_attempt(_make_record(outcome=Outcome.FAIL_BUILD), "bad")
        c.record_attempt(_make_record(decl="bar", outcome=Outcome.SUCCESS), "simp")
        s = c.stats()
        assert s["total_examples"] == 3
        assert s["positive"] == 2
        assert s["negative"] == 1
        assert s["unique_theorems"] == 2

    def test_export_sft_only_positives(self, tmp_path: Path) -> None:
        c = TrainingDataCollector(output_dir=tmp_path)
        c.set_context("t1", "goal1", "ctx1")
        c.record_attempt(_make_record(outcome=Outcome.SUCCESS), "rfl")
        c.record_attempt(_make_record(outcome=Outcome.FAIL_BUILD), "bad")
        path = c.export_instruction_jsonl(tmp_path / "sft.jsonl")
        assert path is not None
        lines = path.read_text().strip().split("\n")
        assert len(lines) == 1  # only the positive
        data = json.loads(lines[0])
        assert data["messages"][2]["content"] == "rfl"

    def test_export_sft_empty_when_no_positives(self, tmp_path: Path) -> None:
        c = TrainingDataCollector(output_dir=tmp_path)
        c.record_attempt(_make_record(outcome=Outcome.FAIL_BUILD), "bad")
        path = c.export_instruction_jsonl(tmp_path / "sft.jsonl")
        assert path is None

    def test_export_sharegpt_format(self, tmp_path: Path) -> None:
        c = TrainingDataCollector(output_dir=tmp_path)
        c.set_context("t1", "goal", "ctx")
        c.record_attempt(_make_record(outcome=Outcome.SUCCESS), "simp")
        path = c.export_sharegpt_jsonl(tmp_path / "sg.jsonl")
        assert path is not None
        data = json.loads(path.read_text().strip())
        assert data["conversations"][0]["from"] == "system"
        assert data["conversations"][1]["from"] == "human"
        assert data["conversations"][2]["from"] == "gpt"
        assert data["conversations"][2]["value"] == "simp"
        assert data["metadata"]["model"] == "gpt-5.6-luna"
        assert data["metadata"]["backend"] == "codex_cli"

    def test_export_dpo_pairs(self, tmp_path: Path) -> None:
        c = TrainingDataCollector(output_dir=tmp_path)
        c.set_context("t1", "goal", "ctx")
        # First attempt fails
        c.record_attempt(
            _make_record(decl="foo", outcome=Outcome.FAIL_BUILD, attempt=1),
            "bad_proof",
        )
        # Second attempt succeeds
        c.record_attempt(
            _make_record(decl="foo", outcome=Outcome.SUCCESS, attempt=2),
            "good_proof",
        )
        path = c.export_dpo_jsonl(tmp_path / "dpo.jsonl")
        assert path is not None
        data = json.loads(path.read_text().strip())
        assert data["chosen"] == "good_proof"
        assert data["rejected"] == "bad_proof"

    def test_export_dpo_needs_both_pos_and_neg(self, tmp_path: Path) -> None:
        c = TrainingDataCollector(output_dir=tmp_path)
        # Only successes -> no DPO pairs
        c.record_attempt(_make_record(outcome=Outcome.SUCCESS), "rfl")
        path = c.export_dpo_jsonl(tmp_path / "dpo.jsonl")
        assert path is None

    def test_export_all_creates_files(self, tmp_path: Path) -> None:
        c = TrainingDataCollector(output_dir=tmp_path)
        c.set_context("t1", "g", "c")
        c.record_attempt(_make_record(outcome=Outcome.FAIL_BUILD, attempt=1), "bad")
        c.record_attempt(_make_record(outcome=Outcome.SUCCESS, attempt=2), "good")
        paths = c.export_all()
        assert "sft" in paths
        assert "dpo" in paths
