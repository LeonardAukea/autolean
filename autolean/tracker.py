"""Experiment tracking — git-based with TSV logging (like AutoResearch)."""

from __future__ import annotations

import csv
import io
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from rich.console import Console
from rich.table import Table

console = Console()


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class Outcome(str, Enum):
    SUCCESS = "success"
    FAIL_BUILD = "fail_build"
    FAIL_TIMEOUT = "fail_timeout"
    FAIL_SORRY_REMAINS = "fail_sorry_remains"
    SKIPPED = "skipped"


@dataclass
class ExperimentRecord:
    """A single experiment attempt record."""

    cycle: int
    timestamp: str
    target_id: str
    decl_name: str
    file: str
    line: int
    outcome: Outcome
    attempt: int  # which attempt for this target (1-indexed)
    duration_seconds: float
    llm_tokens: int
    llm_tok_per_sec: float
    error_summary: str = ""
    error_category: str = ""  # from ErrorCategory enum
    proof_length: int = 0  # lines in the generated proof
    build_duration_seconds: float = 0.0

    def as_dict(self) -> dict[str, str | int | float]:
        return {
            "cycle": self.cycle,
            "timestamp": self.timestamp,
            "target_id": self.target_id,
            "decl_name": self.decl_name,
            "file": self.file,
            "line": self.line,
            "outcome": self.outcome.value,
            "attempt": self.attempt,
            "duration_s": round(self.duration_seconds, 1),
            "llm_tokens": self.llm_tokens,
            "llm_tok_s": round(self.llm_tok_per_sec, 1),
            "proof_lines": self.proof_length,
            "error_category": self.error_category,
            "build_s": round(self.build_duration_seconds, 1),
            "error": self.error_summary[:200],  # truncate long errors
        }


TSV_FIELDS = [
    "cycle", "timestamp", "target_id", "decl_name", "file", "line",
    "outcome", "attempt", "duration_s", "llm_tokens", "llm_tok_s",
    "proof_lines", "error_category", "build_s", "error",
]


# ---------------------------------------------------------------------------
# Git operations
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a git command."""
    return subprocess.run(
        ["git"] + args,
        cwd=cwd,
        capture_output=True,
        text=True,
        timeout=30,
    )


def git_is_repo(cwd: Path) -> bool:
    """Check if cwd is inside a git repo."""
    r = _git(["rev-parse", "--is-inside-work-tree"], cwd)
    return r.returncode == 0


def git_init(cwd: Path) -> None:
    """Initialize a git repo if not already one."""
    if not git_is_repo(cwd):
        _git(["init"], cwd)
        _git(["add", "."], cwd)
        _git(["commit", "-m", "autolean: initial state"], cwd)


def git_create_branch(cwd: Path, branch_name: str) -> None:
    """Create and checkout a new branch."""
    _git(["checkout", "-b", branch_name], cwd)


def git_commit(cwd: Path, message: str, files: list[str] | None = None) -> bool:
    """Stage and commit. Returns True if commit succeeded."""
    if files:
        for f in files:
            _git(["add", f], cwd)
    else:
        _git(["add", "-A"], cwd)

    r = _git(["commit", "-m", message], cwd)
    return r.returncode == 0


def git_revert_file(cwd: Path, filepath: str) -> None:
    """Revert a single file to the last committed state."""
    _git(["checkout", "--", filepath], cwd)


def git_stash_and_restore(cwd: Path, filepath: str, original_content: str) -> None:
    """Write original content back to a file (manual revert)."""
    (cwd / filepath).write_text(original_content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Experiment Tracker
# ---------------------------------------------------------------------------


@dataclass
class ExperimentTracker:
    """Tracks experiments in a TSV file and manages git commits/reverts."""

    project_root: Path
    results_file: Path = field(init=False)
    records: list[ExperimentRecord] = field(default_factory=list)
    _cycle: int = 0

    def __post_init__(self) -> None:
        self.results_file = self.project_root / "results.tsv"

    @property
    def cycle(self) -> int:
        return self._cycle

    def next_cycle(self) -> int:
        self._cycle += 1
        return self._cycle

    # -- TSV logging --------------------------------------------------------

    def _ensure_tsv_header(self) -> None:
        """Write TSV header if file doesn't exist."""
        if not self.results_file.exists():
            with open(self.results_file, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=TSV_FIELDS, delimiter="\t")
                writer.writeheader()

    def log(self, record: ExperimentRecord) -> None:
        """Append a record to the TSV log."""
        self._ensure_tsv_header()
        self.records.append(record)
        with open(self.results_file, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=TSV_FIELDS, delimiter="\t")
            writer.writerow(record.as_dict())

    # -- Git integration ----------------------------------------------------

    def setup_branch(self, branch_name: str | None = None) -> str:
        """Create an experiment branch."""
        if branch_name is None:
            date_str = datetime.now().strftime("%b%d").lower()
            branch_name = f"autolean/{date_str}"

        git_init(self.project_root)
        git_create_branch(self.project_root, branch_name)
        console.print(f"[green]Branch:[/] {branch_name}")
        return branch_name

    def commit_success(self, record: ExperimentRecord) -> bool:
        """Commit a successful proof fill."""
        msg = (
            f"autolean: prove {record.decl_name} "
            f"(cycle {record.cycle}, attempt {record.attempt})"
        )
        return git_commit(self.project_root, msg)

    def revert_failure(self, filepath: str, original_content: str) -> None:
        """Revert a failed attempt."""
        git_stash_and_restore(self.project_root, filepath, original_content)

    # -- Summary ------------------------------------------------------------

    def summary(self) -> dict[str, int]:
        """Return counts by outcome."""
        counts: dict[str, int] = {}
        for r in self.records:
            key = r.outcome.value
            counts[key] = counts.get(key, 0) + 1
        return counts

    def print_summary(self, initial_count: int = 0) -> None:
        """Print a rich summary table with detailed metrics."""
        table = Table(title="AutoLean Session Summary")
        table.add_column("Metric", style="bold")
        table.add_column("Value", justify="right")

        total = len(self.records)
        successes = sum(1 for r in self.records if r.outcome == Outcome.SUCCESS)
        failures = total - successes

        # Core metrics
        table.add_row("Total cycles", str(self._cycle))
        table.add_row("Total attempts", str(total))
        table.add_row("Proofs found", f"[green]{successes}[/]")
        table.add_row("Failed attempts", f"[red]{failures}[/]")

        if total > 0:
            table.add_row("Overall success rate", f"{successes / total * 100:.1f}%")

            # First-attempt success rate (measures prompt quality)
            first_attempts = [r for r in self.records if r.attempt == 1]
            first_successes = sum(1 for r in first_attempts if r.outcome == Outcome.SUCCESS)
            if first_attempts:
                table.add_row(
                    "First-attempt success",
                    f"{first_successes / len(first_attempts) * 100:.1f}%",
                )

            # Coverage metric (most important!)
            if initial_count > 0:
                coverage = successes / initial_count * 100
                table.add_row(
                    "Sorry coverage",
                    f"[{'green' if coverage > 50 else 'yellow'}]"
                    f"{successes}/{initial_count} ({coverage:.1f}%)[/]",
                )

            # Token efficiency
            total_tokens = sum(r.llm_tokens for r in self.records)
            if total_tokens > 0 and successes > 0:
                tokens_per_proof = total_tokens / successes
                table.add_row("Tokens per proof", f"{tokens_per_proof:.0f}")
            table.add_row("Total tokens", f"{total_tokens:,}")

            # Timing
            avg_dur = sum(r.duration_seconds for r in self.records) / total
            table.add_row("Avg cycle time", f"{avg_dur:.1f}s")
            avg_build = sum(r.build_duration_seconds for r in self.records) / total
            if avg_build > 0:
                table.add_row("Avg build time", f"{avg_build:.1f}s")

        # Error category breakdown
        error_counts: dict[str, int] = {}
        for r in self.records:
            if r.error_category:
                error_counts[r.error_category] = error_counts.get(r.error_category, 0) + 1
        if error_counts:
            table.add_section()
            table.add_row("[bold]Error Categories[/]", "")
            for cat, count in sorted(error_counts.items(), key=lambda x: -x[1]):
                table.add_row(f"  {cat}", str(count))

        # Outcome breakdown
        table.add_section()
        table.add_row("[bold]Outcomes[/]", "")
        for outcome, count in sorted(self.summary().items(), key=lambda x: -x[1]):
            style = "green" if outcome == "success" else "red"
            table.add_row(f"  {outcome}", f"[{style}]{count}[/{style}]")

        console.print(table)
