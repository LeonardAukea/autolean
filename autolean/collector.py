"""Training data collector — converts proof attempts into fine-tuning datasets.

Collects data in three formats:
  1. Instruction JSONL (messages format for SFT)
  2. ShareGPT JSONL (Hermes/Axolotl compatible)
  3. DPO pairs (positive proof + negative proof for preference learning)

Successful proofs become positive examples; failed attempts become DPO
negatives.
"""

from __future__ import annotations

import json
import logging
import math
import re
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from autolean.tracker import ExperimentRecord, Outcome

log = logging.getLogger("autolean")


# ---------------------------------------------------------------------------
# Data point types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ProofExample:
    """A single (goal_state, proof) training example."""

    #: The source position these attempts address. A preference pair also
    #: requires the same goal, context, and proof environment.
    target_id: str
    theorem_name: str
    file: str
    goal_state: str
    context: str
    proof: str
    success: bool  # whether Lean accepted it
    attempt: int
    tokens: int
    duration: float
    error_category: str = ""
    error_message: str = ""
    environment_sha256: str = ""
    proof_sha256: str = ""
    axioms: str = ""
    model: str = ""
    backend: str = ""
    inference_location: str = ""
    search_context_sha256: str = ""
    remote_search: bool | None = None

    def __post_init__(self) -> None:
        text_values = (
            self.target_id,
            self.theorem_name,
            self.file,
            self.goal_state,
            self.context,
            self.proof,
            self.error_category,
            self.error_message,
            self.environment_sha256,
            self.proof_sha256,
            self.axioms,
            self.model,
            self.backend,
            self.inference_location,
            self.search_context_sha256,
        )
        if any(not isinstance(value, str) for value in text_values):
            raise ValueError("proof example text fields must be strings")
        if not self.target_id or not self.theorem_name or not self.file:
            raise ValueError("proof example target identity must be complete")
        if not isinstance(self.success, bool):
            raise ValueError("proof example verdict must be a boolean")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (self.attempt, self.tokens)
        ):
            raise ValueError("proof example counts must be non-negative integers")
        if (
            isinstance(self.duration, bool)
            or not isinstance(self.duration, (int, float))
            or not math.isfinite(self.duration)
            or self.duration < 0
        ):
            raise ValueError("proof example duration must be finite and non-negative")
        digests = (
            self.environment_sha256,
            self.proof_sha256,
            self.search_context_sha256,
        )
        if any(digest and re.fullmatch(r"[0-9a-f]{64}", digest) is None for digest in digests):
            raise ValueError("proof example digests must be lowercase SHA-256 values")
        if self.inference_location not in {"", "local", "remote"}:
            raise ValueError("proof example inference location is invalid")
        if self.remote_search is not None and not isinstance(self.remote_search, bool):
            raise ValueError("proof example search placement must be a boolean")


@dataclass(frozen=True)
class DPOPair:
    """A preference pair: positive proof (compiled) vs negative (failed)."""

    theorem_name: str
    goal_state: str
    context: str
    chosen: str
    rejected: str
    rejected_error: str

    def __post_init__(self) -> None:
        values = (
            self.theorem_name,
            self.goal_state,
            self.context,
            self.chosen,
            self.rejected,
            self.rejected_error,
        )
        if any(not isinstance(value, str) for value in values):
            raise ValueError("preference pair fields must be text")
        if any(
            not value.strip()
            for value in (
                self.theorem_name,
                self.goal_state,
                self.chosen,
                self.rejected,
                self.rejected_error,
            )
        ):
            raise ValueError("preference pair evidence must be complete")
        if self.chosen == self.rejected:
            raise ValueError("preference pair proofs must differ")


# ---------------------------------------------------------------------------
# Collector
# ---------------------------------------------------------------------------


@dataclass
class TrainingDataCollector:
    """Collects proof attempts for fine-tuning.

    Integrates into the agent loop. After each attempt, call
    `record_attempt()`. At session end, call `export()`.
    """

    output_dir: Path
    examples: list[ProofExample] = field(default_factory=list)
    _goal_states: dict[str, str] = field(default_factory=dict)  # target_id -> goal
    _contexts: dict[str, str] = field(default_factory=dict)  # target_id -> context

    def __post_init__(self) -> None:
        if not isinstance(self.output_dir, Path):
            raise ValueError("collector output directory must be a path")
        if not isinstance(self.examples, list) or any(
            not isinstance(example, ProofExample) for example in self.examples
        ):
            raise ValueError("collector examples must contain ProofExample values")

    def set_context(self, target_id: str, goal_state: str, context: str) -> None:
        """Store goal state and context for a target (before attempts)."""
        if not isinstance(target_id, str) or not target_id:
            raise ValueError("collector target ID must not be empty")
        if not isinstance(goal_state, str) or not isinstance(context, str):
            raise ValueError("collector goal and context must be text")
        self._goal_states[target_id] = goal_state or ""
        self._contexts[target_id] = context or ""

    def record_attempt(
        self,
        record: ExperimentRecord,
        proof: str,
    ) -> None:
        """Record a proof attempt (successful or not)."""
        if not isinstance(record, ExperimentRecord) or not isinstance(proof, str):
            raise ValueError("collector attempts require a record and proof text")
        example = ProofExample(
            target_id=record.target_id,
            theorem_name=record.decl_name,
            file=record.file,
            goal_state=self._goal_states.get(record.target_id, ""),
            context=self._contexts.get(record.target_id, ""),
            proof=proof,
            success=record.outcome == Outcome.SUCCESS,
            attempt=record.attempt,
            tokens=record.llm_tokens,
            duration=record.duration_seconds,
            error_category=record.error_category,
            error_message=record.error_summary[:500],
            environment_sha256=record.environment_sha256,
            proof_sha256=record.proof_sha256,
            axioms=record.axioms,
            model=record.model,
            backend=record.backend,
            inference_location=(
                record.inference_location.value if record.inference_location is not None else ""
            ),
            search_context_sha256=record.search_context_sha256,
            remote_search=record.remote_search,
        )
        self.examples.append(example)
        log.debug(
            "Collected %s example: %s (attempt %d)",
            "positive" if example.success else "negative",
            example.theorem_name,
            example.attempt,
        )

    # -- Export formats -------------------------------------------------------

    def export_all(self) -> dict[str, Path]:
        """Export every training format and return the generated paths."""
        self.output_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        paths = {}

        sft = self.export_instruction_jsonl(self.output_dir / f"sft_{timestamp}.jsonl")
        if sft:
            paths["sft"] = sft

        sharegpt = self.export_sharegpt_jsonl(self.output_dir / f"sharegpt_{timestamp}.jsonl")
        if sharegpt:
            paths["sharegpt"] = sharegpt

        dpo = self.export_dpo_jsonl(self.output_dir / f"dpo_{timestamp}.jsonl")
        if dpo:
            paths["dpo"] = dpo

        return paths

    def _trainable(self) -> list[ProofExample]:
        """Accepted proofs that carry the goal they closed.

        Without a goal the exported turn is a proof of an unstated question,
        which teaches an answer with no problem attached.
        """
        return [example for example in self.examples if example.success and example.goal_state]

    def export_instruction_jsonl(self, path: Path) -> Path | None:
        """Export as instruction-tuning JSONL (OpenAI messages format).

        Only includes SUCCESSFUL proofs (positive examples for SFT).

        Format:
        {"messages": [
            {"role": "system", "content": "..."},
            {"role": "user", "content": "<goal_state + context>"},
            {"role": "assistant", "content": "<proof>"}
        ]}
        """
        positives = self._trainable()
        if not positives:
            return None

        with open(path, "w", encoding="utf-8") as f:
            for ex in positives:
                record = {
                    "messages": [
                        {
                            "role": "system",
                            "content": (
                                "You are a Lean 4 theorem prover. Given a proof goal state "
                                "and surrounding code context, output ONLY the tactic proof "
                                "body that closes all goals. No explanation."
                            ),
                        },
                        {
                            "role": "user",
                            "content": self._format_user_prompt(ex),
                        },
                        {
                            "role": "assistant",
                            "content": ex.proof,
                        },
                    ],
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        log.info("Exported %d SFT examples to %s", len(positives), path)
        return path

    def export_sharegpt_jsonl(self, path: Path) -> Path | None:
        """Export as ShareGPT JSONL (Hermes/Axolotl compatible).

        Format:
        {"conversations": [
            {"from": "system", "value": "..."},
            {"from": "human", "value": "..."},
            {"from": "gpt", "value": "..."}
        ]}
        """
        positives = self._trainable()
        if not positives:
            return None

        with open(path, "w", encoding="utf-8") as f:
            for ex in positives:
                record = {
                    "conversations": [
                        {
                            "from": "system",
                            "value": ("You are a Lean 4 theorem prover. Output ONLY tactic code."),
                        },
                        {
                            "from": "human",
                            "value": self._format_user_prompt(ex),
                        },
                        {
                            "from": "gpt",
                            "value": ex.proof,
                        },
                    ],
                    "metadata": {
                        "theorem": ex.theorem_name,
                        "file": ex.file,
                        "attempt": ex.attempt,
                        "tokens": ex.tokens,
                        "environment_sha256": ex.environment_sha256,
                        "proof_sha256": ex.proof_sha256,
                        "axioms": ex.axioms,
                        "model": ex.model,
                        "backend": ex.backend,
                        "inference_location": ex.inference_location,
                        "search_context_sha256": ex.search_context_sha256,
                        "remote_search": ex.remote_search,
                    },
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        log.info("Exported %d ShareGPT examples to %s", len(positives), path)
        return path

    def export_dpo_jsonl(self, path: Path) -> Path | None:
        """Export as DPO pairs (positive + negative for preference learning).

        For each theorem that was eventually proved, pairs the successful
        proof (chosen) with earlier failed attempts (rejected).

        Format:
        {"prompt": "...", "chosen": "...", "rejected": "..."}
        """
        by_target: dict[tuple[str, str, str, str], list[ProofExample]] = {}
        for ex in self.examples:
            identity = (ex.target_id, ex.goal_state, ex.context, ex.environment_sha256)
            by_target.setdefault(identity, []).append(ex)

        pairs: list[DPOPair] = []
        for attempts in by_target.values():
            positives = [a for a in attempts if a.success and a.goal_state]
            negatives = [a for a in attempts if not a.success and a.proof]
            if not positives or not negatives:
                continue
            chosen = positives[0]
            seen_proofs: set[str] = set()
            for neg in negatives:
                if neg.proof == chosen.proof or neg.proof in seen_proofs:
                    continue
                seen_proofs.add(neg.proof)
                pairs.append(
                    DPOPair(
                        theorem_name=chosen.theorem_name,
                        goal_state=chosen.goal_state,
                        context=chosen.context,
                        chosen=chosen.proof,
                        rejected=neg.proof,
                        rejected_error=neg.error_category or "build_failed",
                    )
                )

        if not pairs:
            return None

        with open(path, "w", encoding="utf-8") as f:
            for pair in pairs:
                record = {
                    "prompt": self._format_user_prompt(pair),
                    "chosen": pair.chosen,
                    "rejected": pair.rejected,
                    "metadata": {
                        "theorem": pair.theorem_name,
                        "rejected_error": pair.rejected_error,
                    },
                }
                f.write(json.dumps(record, ensure_ascii=False) + "\n")

        log.info("Exported %d DPO pairs to %s", len(pairs), path)
        return path

    # -- Helpers ---------------------------------------------------------------

    def _format_user_prompt(self, item: ProofExample | DPOPair) -> str:
        parts = []
        if item.context:
            parts.append(f"## Context\n```lean\n{item.context[:2000]}\n```")
        if item.goal_state:
            parts.append(f"## Goal State\n```\n{item.goal_state}\n```")
        parts.append(f"## Task\nProvide the tactic proof for `{item.theorem_name}`.")
        return "\n\n".join(parts)

    # -- Stats ----------------------------------------------------------------

    def stats(self) -> dict[str, int]:
        total = len(self.examples)
        pos = sum(1 for e in self.examples if e.success)
        return {
            "total_examples": total,
            "positive": pos,
            "negative": total - pos,
            "unique_theorems": len(set(e.theorem_name for e in self.examples)),
        }
