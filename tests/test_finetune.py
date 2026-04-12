"""Tests for local fine-tuning pipeline."""

from __future__ import annotations

import json
from pathlib import Path

from autolean.finetune import (
    check_finetune_readiness,
    convert_to_gemma_tuner_format,
    FINETUNE_THRESHOLD,
)


class TestConvertToGemmaTuner:

    def test_converts_sft_to_csv(self, tmp_path: Path) -> None:
        sft = tmp_path / "sft.jsonl"
        sft.write_text(json.dumps({
            "messages": [
                {"role": "system", "content": "sys"},
                {"role": "user", "content": "prove this"},
                {"role": "assistant", "content": "rfl"},
            ]
        }) + "\n")

        csv_path = tmp_path / "out.csv"
        n = convert_to_gemma_tuner_format(sft, csv_path)
        assert n == 1
        assert csv_path.exists()

        lines = csv_path.read_text().strip().split("\n")
        assert lines[0] == "prompt,response"
        assert "prove this" in lines[1]
        assert "rfl" in lines[1]

    def test_skips_incomplete_messages(self, tmp_path: Path) -> None:
        sft = tmp_path / "sft.jsonl"
        # Only 2 messages, not 3
        sft.write_text(json.dumps({
            "messages": [
                {"role": "user", "content": "hi"},
                {"role": "assistant", "content": "hello"},
            ]
        }) + "\n")

        csv_path = tmp_path / "out.csv"
        n = convert_to_gemma_tuner_format(sft, csv_path)
        assert n == 0


class TestCheckFinetuneReadiness:

    def test_not_ready_when_empty(self, tmp_path: Path) -> None:
        td = tmp_path / "training_data"
        td.mkdir()
        status = check_finetune_readiness(td)
        assert not status.ready
        assert status.positive_examples == 0

    def test_ready_when_enough_data(self, tmp_path: Path) -> None:
        td = tmp_path / "training_data"
        td.mkdir()
        sft = td / "sft_test.jsonl"
        # Write FINETUNE_THRESHOLD examples
        with open(sft, "w") as f:
            for i in range(FINETUNE_THRESHOLD):
                f.write(json.dumps({"messages": [
                    {"role": "system", "content": "s"},
                    {"role": "user", "content": f"q{i}"},
                    {"role": "assistant", "content": f"a{i}"},
                ]}) + "\n")

        status = check_finetune_readiness(td)
        assert status.ready
        assert status.positive_examples == FINETUNE_THRESHOLD
        assert status.training_file == sft
