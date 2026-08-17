"""Tests for local fine-tuning pipeline."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from autolean.finetune import (
    FINETUNE_THRESHOLD,
    check_finetune_readiness,
    convert_to_gemma_tuner_format,
    trigger_local_finetune,
)


class TestConvertToGemmaTuner:
    def test_converts_sft_to_csv(self, tmp_path: Path) -> None:
        sft = tmp_path / "sft.jsonl"
        sft.write_text(
            json.dumps(
                {
                    "messages": [
                        {"role": "system", "content": "sys"},
                        {"role": "user", "content": "prove this"},
                        {"role": "assistant", "content": "rfl"},
                    ]
                }
            )
            + "\n"
        )

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
        sft.write_text(
            json.dumps(
                {
                    "messages": [
                        {"role": "user", "content": "hi"},
                        {"role": "assistant", "content": "hello"},
                    ]
                }
            )
            + "\n"
        )

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
                f.write(
                    json.dumps(
                        {
                            "messages": [
                                {"role": "system", "content": "s"},
                                {"role": "user", "content": f"q{i}"},
                                {"role": "assistant", "content": f"a{i}"},
                            ]
                        }
                    )
                    + "\n"
                )

        status = check_finetune_readiness(td)
        assert status.ready
        assert status.positive_examples == FINETUNE_THRESHOLD
        assert status.training_file == sft


class TestReportedTruthfully:
    """The report claims what happened, not what a reader might hope."""

    def _ready_dir(self, tmp_path: Path) -> Path:
        directory = tmp_path / "training_data"
        directory.mkdir()
        example = {
            "messages": [
                {"role": "system", "content": "s"},
                {"role": "user", "content": "## Goal State\n```\n⊢ True\n```"},
                {"role": "assistant", "content": "trivial"},
            ]
        }
        (directory / "sft_20260817.jsonl").write_text(
            "\n".join(json.dumps(example) for _ in range(FINETUNE_THRESHOLD)) + "\n",
            encoding="utf-8",
        )
        return directory

    def test_no_training_is_announced_when_none_is_started(
        self, tmp_path: Path, capsys: pytest.CaptureFixture[str]
    ) -> None:
        directory = self._ready_dir(tmp_path)

        assert trigger_local_finetune(directory) is True

        printed = capsys.readouterr().out.lower()
        assert "starting local fine-tuning" not in printed
        assert "auto-triggered" not in printed
        assert "separate step" in printed

    def test_the_config_does_not_depend_on_the_working_directory(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """The tuner checkout may sit anywhere; the artifacts must not move."""
        directory = self._ready_dir(tmp_path)
        elsewhere = tmp_path / "elsewhere"
        elsewhere.mkdir()
        monkeypatch.chdir(elsewhere)

        trigger_local_finetune(directory)

        assert (directory / "gemma_tuner_config.json").is_file()
        assert (directory / "gemma_tuner_data.csv").is_file()
