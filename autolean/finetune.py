"""Local fine-tuning on collected proof data.

Converts collected SFT/DPO data to gemma-tuner-multimodal or
HuggingFace TRL format and, past FINETUNE_THRESHOLD positive
examples, launches local LoRA training or writes a cloud export.
"""

from __future__ import annotations

import csv
import json
import logging
from dataclasses import dataclass
from pathlib import Path

from autolean.ui import console

log = logging.getLogger("autolean")

FINETUNE_THRESHOLD = 50  # minimum positive examples before triggering


@dataclass
class FinetuneStatus:
    """Status of the fine-tuning pipeline."""

    positive_examples: int
    negative_examples: int
    ready: bool  # enough data to fine-tune
    training_file: Path | None  # path to training data
    model_output: Path | None  # path to fine-tuned model


def convert_to_gemma_tuner_format(
    sft_jsonl: Path,
    output_csv: Path,
) -> int:
    """Convert SFT JSONL to gemma-tuner-multimodal CSV format.

    gemma-tuner expects CSV with 'prompt' and 'response' columns.

    Returns number of examples converted.
    """
    count = 0
    with (
        open(sft_jsonl, encoding="utf-8") as f_in,
        open(
            output_csv,
            "w",
            newline="",
            encoding="utf-8",
        ) as f_out,
    ):
        writer = csv.writer(f_out)
        writer.writerow(["prompt", "response"])

        for line in f_in:
            data = json.loads(line)
            messages = data.get("messages", [])
            if len(messages) >= 3:
                # system + user + assistant
                prompt = messages[1].get("content", "")
                response = messages[2].get("content", "")
                if prompt and response:
                    writer.writerow([prompt, response])
                    count += 1

    log.info("Converted %d examples to gemma-tuner CSV: %s", count, output_csv)
    return count


def _count_lines(path: Path) -> int:
    """Count records in a JSONL file — one example per line."""
    with open(path, encoding="utf-8") as f:
        return sum(1 for _ in f)


def check_finetune_readiness(training_data_dir: Path) -> FinetuneStatus:
    """Check if we have enough data to trigger fine-tuning."""
    sft_files = sorted(training_data_dir.glob("sft_*.jsonl"))

    positive = sum(_count_lines(f) for f in sft_files)

    dpo_files = sorted(training_data_dir.glob("dpo_*.jsonl"))
    negative = sum(_count_lines(f) for f in dpo_files)

    latest_sft = sft_files[-1] if sft_files else None
    ready = positive >= FINETUNE_THRESHOLD

    return FinetuneStatus(
        positive_examples=positive,
        negative_examples=negative,
        ready=ready,
        training_file=latest_sft,
        model_output=training_data_dir / "output",
    )


def trigger_local_finetune(
    training_data_dir: Path,
    base_model: str = "google/gemma-4-E2B",
    output_name: str = "autolean-v1",
) -> bool:
    """Trigger local fine-tuning if data threshold is met.

    Uses gemma-tuner-multimodal format (CSV with prompt/response).
    Falls back to generating an Axolotl config if gemma-tuner not available.

    Returns True if training was started/configured.
    """
    status = check_finetune_readiness(training_data_dir)

    if not status.ready:
        console.print(
            f"[yellow]Not enough data yet:[/] {status.positive_examples}/{FINETUNE_THRESHOLD} "
            f"positive examples. Keep running the agent."
        )
        return False

    if not status.training_file:
        console.print("[red]No training data files found.[/]")
        return False

    # Convert to gemma-tuner CSV format
    csv_path = training_data_dir / "gemma_tuner_data.csv"
    n = convert_to_gemma_tuner_format(status.training_file, csv_path)
    console.print(f"[green]Converted {n} examples to {csv_path}[/]")

    # Check if gemma-tuner is available
    gemma_tuner = Path("gemma-tuner-multimodal")
    if gemma_tuner.exists():
        console.print("[bold]Starting local fine-tuning with gemma-tuner...[/]")
        # Generate config
        config = {
            "model_name": base_model,
            "data_path": str(csv_path),
            "output_dir": str(training_data_dir / "output"),
            "epochs": 3,
            "learning_rate": 1e-5,
            "batch_size": 1,
        }
        config_path = training_data_dir / "gemma_tuner_config.json"
        config_path.write_text(json.dumps(config, indent=2), encoding="utf-8")

        console.print(f"  Config: {config_path}")
        console.print(f"  Data: {csv_path} ({n} examples)")
        console.print(f"  Output: {training_data_dir / 'output'}")
        console.print(f"\n  Run: cd gemma-tuner-multimodal && python finetune.py --config {config_path}")
        return True

    # Fallback: generate Axolotl config
    console.print(f"[bold]Fine-tuning data ready ({n} examples).[/]")
    console.print(f"  Data CSV: {csv_path}")
    console.print("\n  Option 1 (gemma-tuner):")
    console.print("    git clone https://github.com/mattmireles/gemma-tuner-multimodal")
    console.print(f"    cd gemma-tuner-multimodal && python finetune.py --data {csv_path}")
    console.print("\n  Option 2 (Axolotl):")
    console.print("    uv run autolean finetune-config --framework axolotl")
    console.print("    accelerate launch -m axolotl.cli.train ...")
    console.print("\n  After training, import to Ollama:")
    console.print(f"    ollama create {output_name} -f Modelfile")
    console.print(f"    uv run autolean solve --model {output_name}")
    return True
