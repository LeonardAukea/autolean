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
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path

from autolean.tracker import ExperimentRecord, Outcome

log = logging.getLogger("autolean")


# ---------------------------------------------------------------------------
# Data point types
# ---------------------------------------------------------------------------


@dataclass
class ProofExample:
    """A single (goal_state, proof) training example."""

    theorem_name: str
    file: str
    goal_state: str  # Lean goal state (from hole-punch)
    context: str  # surrounding code context
    proof: str  # the tactic proof
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


@dataclass
class DPOPair:
    """A preference pair: positive proof (compiled) vs negative (failed)."""

    theorem_name: str
    goal_state: str
    context: str
    chosen: str  # proof that worked
    rejected: str  # proof that failed
    rejected_error: str  # why it failed


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

    def set_context(self, target_id: str, goal_state: str, context: str) -> None:
        """Store goal state and context for a target (before attempts)."""
        self._goal_states[target_id] = goal_state or ""
        self._contexts[target_id] = context or ""

    def record_attempt(
        self,
        record: ExperimentRecord,
        proof: str,
    ) -> None:
        """Record a proof attempt (successful or not)."""
        example = ProofExample(
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
        positives = [e for e in self.examples if e.success]
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
        positives = [e for e in self.examples if e.success]
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
        # Group by theorem
        by_theorem: dict[str, list[ProofExample]] = {}
        for ex in self.examples:
            by_theorem.setdefault(ex.theorem_name, []).append(ex)

        pairs: list[DPOPair] = []
        for name, attempts in by_theorem.items():
            positives = [a for a in attempts if a.success]
            negatives = [a for a in attempts if not a.success and a.proof]
            if not positives or not negatives:
                continue
            # Use the first success and each distinct failure
            chosen = positives[0]
            seen_proofs: set[str] = set()
            for neg in negatives:
                if neg.proof in seen_proofs:
                    continue
                seen_proofs.add(neg.proof)
                pairs.append(
                    DPOPair(
                        theorem_name=name,
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
                    "prompt": self._format_user_prompt_from_pair(pair),
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

    def _format_user_prompt(self, ex: ProofExample) -> str:
        parts = []
        if ex.context:
            parts.append(f"## Context\n```lean\n{ex.context[:2000]}\n```")
        if ex.goal_state:
            parts.append(f"## Goal State\n```\n{ex.goal_state}\n```")
        parts.append(f"## Task\nProvide the tactic proof for `{ex.theorem_name}`.")
        return "\n\n".join(parts)

    def _format_user_prompt_from_pair(self, pair: DPOPair) -> str:
        parts = []
        if pair.context:
            parts.append(f"## Context\n```lean\n{pair.context[:2000]}\n```")
        if pair.goal_state:
            parts.append(f"## Goal State\n```\n{pair.goal_state}\n```")
        parts.append(f"## Task\nProvide the tactic proof for `{pair.theorem_name}`.")
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

    def should_finetune(self, threshold: int = 50) -> bool:
        """Report whether enough positive examples enable fine-tuning."""
        return sum(1 for e in self.examples if e.success) >= threshold
