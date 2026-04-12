"""Experiment tracking — git-based with TSV logging (like AutoResearch)."""

from __future__ import annotations

import csv
import logging
import subprocess
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from pathlib import Path

from rich.console import Console
from rich.panel import Panel
from rich.table import Table

console = Console()
log = logging.getLogger("autolean")


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
    r = _git(["rev-parse", "--is-inside-work-tree"], cwd)
    return r.returncode == 0


def git_init(cwd: Path) -> None:
    if not git_is_repo(cwd):
        _git(["init"], cwd)
        _git(["add", "."], cwd)
        _git(["commit", "-m", "autolean: initial state"], cwd)


def git_create_branch(cwd: Path, branch_name: str) -> None:
    _git(["checkout", "-b", branch_name], cwd)


def git_commit(cwd: Path, message: str, files: list[str] | None = None) -> bool:
    if files:
        for f in files:
            _git(["add", f], cwd)
    else:
        _git(["add", "-A"], cwd)
    r = _git(["commit", "-m", message], cwd)
    return r.returncode == 0


def git_revert_file(cwd: Path, filepath: str) -> None:
    _git(["checkout", "--", filepath], cwd)


def git_stash_and_restore(cwd: Path, filepath: str, original_content: str) -> None:
    (cwd / filepath).write_text(original_content, encoding="utf-8")


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def setup_logging(log_dir: Path, verbose: bool = False) -> None:
    """Configure structured logging to file + console."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"autolean_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(logging.Formatter(
        "%(asctime)s | %(levelname)-7s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    ))

    log.addHandler(file_handler)
    log.setLevel(logging.DEBUG)

    if verbose:
        console_handler = logging.StreamHandler()
        console_handler.setLevel(logging.DEBUG)
        console_handler.setFormatter(logging.Formatter("%(levelname)-7s | %(message)s"))
        log.addHandler(console_handler)

    log.info("AutoLean session started — log: %s", log_file)
    return log_file


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
        if not self.results_file.exists():
            with open(self.results_file, "w", newline="") as f:
                writer = csv.DictWriter(f, fieldnames=TSV_FIELDS, delimiter="\t")
                writer.writeheader()

    def log(self, record: ExperimentRecord) -> None:
        """Append a record to the TSV log and structured log."""
        self._ensure_tsv_header()
        self.records.append(record)
        with open(self.results_file, "a", newline="") as f:
            writer = csv.DictWriter(f, fieldnames=TSV_FIELDS, delimiter="\t")
            writer.writerow(record.as_dict())

        # Structured log entry
        log.info(
            "cycle=%d target=%s outcome=%s attempt=%d duration=%.1fs tokens=%d%s",
            record.cycle, record.decl_name, record.outcome.value,
            record.attempt, record.duration_seconds, record.llm_tokens,
            f" error_cat={record.error_category}" if record.error_category else "",
        )

    # -- Git integration ----------------------------------------------------

    def setup_branch(self, branch_name: str | None = None) -> str:
        if branch_name is None:
            date_str = datetime.now().strftime("%b%d").lower()
            branch_name = f"autolean/{date_str}"
        git_init(self.project_root)
        git_create_branch(self.project_root, branch_name)
        console.print(f"[green]Branch:[/] {branch_name}")
        return branch_name

    def commit_success(self, record: ExperimentRecord) -> bool:
        msg = (
            f"autolean: prove {record.decl_name} "
            f"(cycle {record.cycle}, attempt {record.attempt})"
        )
        return git_commit(self.project_root, msg)

    def revert_failure(self, filepath: str, original_content: str) -> None:
        git_stash_and_restore(self.project_root, filepath, original_content)

    # -- Summary ------------------------------------------------------------

    def summary(self) -> dict[str, int]:
        counts: dict[str, int] = {}
        for r in self.records:
            counts[r.outcome.value] = counts.get(r.outcome.value, 0) + 1
        return counts

    def print_summary(self, initial_count: int = 0) -> None:
        """Print a properly formatted session summary using Rich tables."""
        total = len(self.records)
        successes = sum(1 for r in self.records if r.outcome == Outcome.SUCCESS)
        failures = total - successes

        # ── Main metrics table ──
        metrics = Table(
            title="Session Metrics",
            show_header=True,
            header_style="bold",
            min_width=50,
            pad_edge=True,
        )
        metrics.add_column("Metric", style="bold", min_width=25)
        metrics.add_column("Value", justify="right", min_width=20)

        metrics.add_row("Total cycles", str(self._cycle))
        metrics.add_row("Total attempts", str(total))
        metrics.add_row("Proofs found", f"[bold green]{successes}[/bold green]")
        metrics.add_row("Failed attempts", f"[red]{failures}[/red]")

        if total > 0:
            metrics.add_row(
                "Overall success rate",
                f"{successes / total * 100:.1f}%",
            )

            first_attempts = [r for r in self.records if r.attempt == 1]
            first_successes = sum(1 for r in first_attempts if r.outcome == Outcome.SUCCESS)
            if first_attempts:
                rate = first_successes / len(first_attempts) * 100
                metrics.add_row(
                    "First-attempt success",
                    f"{'[green]' if rate > 50 else '[yellow]'}{rate:.1f}%[/]",
                )

            if initial_count > 0:
                coverage = successes / initial_count * 100
                metrics.add_row(
                    "Sorry coverage",
                    f"[bold {'green' if coverage > 50 else 'yellow'}]"
                    f"{successes}/{initial_count} ({coverage:.1f}%)"
                    f"[/bold {'green' if coverage > 50 else 'yellow'}]",
                )

            total_tokens = sum(r.llm_tokens for r in self.records)
            if total_tokens > 0 and successes > 0:
                metrics.add_row("Tokens per proof", f"{total_tokens / successes:,.0f}")
            metrics.add_row("Total tokens used", f"{total_tokens:,}")

            avg_dur = sum(r.duration_seconds for r in self.records) / total
            metrics.add_row("Avg cycle time", f"{avg_dur:.1f}s")

            build_times = [r.build_duration_seconds for r in self.records if r.build_duration_seconds > 0]
            if build_times:
                metrics.add_row("Avg build time", f"{sum(build_times) / len(build_times):.1f}s")

        console.print(metrics)

        # ── Error breakdown table ──
        error_counts: dict[str, int] = {}
        for r in self.records:
            if r.error_category:
                error_counts[r.error_category] = error_counts.get(r.error_category, 0) + 1

        if error_counts:
            err_table = Table(
                title="Error Breakdown",
                show_header=True,
                header_style="bold red",
                min_width=50,
            )
            err_table.add_column("Category", style="bold", min_width=25)
            err_table.add_column("Count", justify="right", min_width=10)
            err_table.add_column("Pct", justify="right", min_width=10)

            for cat, count in sorted(error_counts.items(), key=lambda x: -x[1]):
                pct = count / failures * 100 if failures > 0 else 0
                err_table.add_row(cat, str(count), f"{pct:.0f}%")

            console.print(err_table)

        # ── Outcome breakdown table ──
        outcome_table = Table(
            title="Outcome Breakdown",
            show_header=True,
            header_style="bold",
            min_width=50,
        )
        outcome_table.add_column("Outcome", style="bold", min_width=25)
        outcome_table.add_column("Count", justify="right", min_width=10)
        outcome_table.add_column("Pct", justify="right", min_width=10)

        for outcome_val, count in sorted(self.summary().items(), key=lambda x: -x[1]):
            style = "green" if outcome_val == "success" else "red"
            pct = count / total * 100 if total > 0 else 0
            outcome_table.add_row(
                f"[{style}]{outcome_val}[/{style}]",
                f"[{style}]{count}[/{style}]",
                f"{pct:.0f}%",
            )

        console.print(outcome_table)
