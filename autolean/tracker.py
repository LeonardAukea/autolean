"""Experiment tracking — git commits and append-only TSV rows."""

from __future__ import annotations

import csv
import logging
import subprocess
from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum
from pathlib import Path

from rich.table import Table

from autolean.ui import console

log = logging.getLogger("autolean")


class GitError(RuntimeError):
    """A required repository transition failed."""


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------


class Outcome(StrEnum):
    SUCCESS = "success"
    VALIDATED = "validated"
    GAP_FILLED = "gap_filled"
    FAIL_BUILD = "fail_build"
    FAIL_PROVIDER = "fail_provider"
    FAIL_TIMEOUT = "fail_timeout"
    FAIL_SORRY_REMAINS = "fail_sorry_remains"
    SKIPPED = "skipped"


FAILURE_OUTCOMES = frozenset(
    {
        Outcome.FAIL_BUILD,
        Outcome.FAIL_PROVIDER,
        Outcome.FAIL_TIMEOUT,
        Outcome.FAIL_SORRY_REMAINS,
    }
)


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
    environment_sha256: str = ""
    proof_sha256: str = ""
    axioms: str = ""
    model: str = ""
    backend: str = ""
    llm_input_tokens: int = 0
    prompt_sha256: str = ""
    structural_context_sha256: str = ""
    indexed_context_sha256: str = ""
    strategy_sha256: str = ""
    strategy_response_sha256: str = ""
    model_revision: str = ""
    sampling_seed: int | None = None
    model_artifact_sha256: str = ""

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
            "llm_input_tokens": self.llm_input_tokens,
            "llm_tok_s": round(self.llm_tok_per_sec, 1),
            "proof_lines": self.proof_length,
            "error_category": self.error_category,
            "build_s": round(self.build_duration_seconds, 1),
            "environment_sha256": self.environment_sha256,
            "proof_sha256": self.proof_sha256,
            "axioms": self.axioms,
            "model": self.model,
            "backend": self.backend,
            "model_revision": self.model_revision,
            "sampling_seed": "" if self.sampling_seed is None else self.sampling_seed,
            "model_artifact_sha256": self.model_artifact_sha256,
            "prompt_sha256": self.prompt_sha256,
            "structural_context_sha256": self.structural_context_sha256,
            "indexed_context_sha256": self.indexed_context_sha256,
            "strategy_sha256": self.strategy_sha256,
            "strategy_response_sha256": self.strategy_response_sha256,
            "error": self.error_summary[:200],  # truncate long errors
        }


TSV_FIELDS = [
    "cycle",
    "timestamp",
    "target_id",
    "decl_name",
    "file",
    "line",
    "outcome",
    "attempt",
    "duration_s",
    "llm_tokens",
    "llm_input_tokens",
    "llm_tok_s",
    "proof_lines",
    "error_category",
    "build_s",
    "environment_sha256",
    "proof_sha256",
    "axioms",
    "model",
    "backend",
    "model_revision",
    "sampling_seed",
    "model_artifact_sha256",
    "prompt_sha256",
    "structural_context_sha256",
    "indexed_context_sha256",
    "strategy_sha256",
    "strategy_response_sha256",
    "error",
]


# ---------------------------------------------------------------------------
# Git operations
# ---------------------------------------------------------------------------


def _git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    """Run a Git command or raise the repository-boundary error."""
    try:
        return subprocess.run(
            ["git", "-c", "core.fsmonitor=false", *args],
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise GitError(f"could not run git {' '.join(args)}: {e}") from e


def _checked_git(args: list[str], cwd: Path) -> subprocess.CompletedProcess[str]:
    result = _git(args, cwd)
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise GitError(f"git {' '.join(args)} failed: {detail or 'no detail'}")
    return result


def git_is_repo(cwd: Path) -> bool:
    r = _git(["rev-parse", "--is-inside-work-tree"], cwd)
    return r.returncode == 0


def git_init(cwd: Path) -> None:
    if not git_is_repo(cwd):
        _checked_git(["init"], cwd)
        _checked_git(["add", "--", "."], cwd)
        _checked_git(["commit", "-m", "autolean: Record initial state"], cwd)


def git_create_branch(cwd: Path, branch_name: str) -> None:
    current = _checked_git(["branch", "--show-current"], cwd).stdout.strip()
    exists = _git(["show-ref", "--verify", "--quiet", f"refs/heads/{branch_name}"], cwd)
    if exists.returncode == 0:
        if current == branch_name:
            return
        raise GitError(f"branch '{branch_name}' already exists; current branch is '{current}'")
    _checked_git(["checkout", "-b", branch_name], cwd)
    active = _checked_git(["branch", "--show-current"], cwd).stdout.strip()
    if active != branch_name:
        raise GitError(f"expected branch '{branch_name}', found '{active}'")


def git_commit(cwd: Path, message: str, files: list[str]) -> None:
    """Commit exactly `files`, preserving every unrelated index entry."""
    repository = Path(_checked_git(["rev-parse", "--show-toplevel"], cwd).stdout.strip()).resolve()
    pathspecs: list[str] = []
    for filename in files:
        candidate = (cwd / filename).resolve()
        try:
            candidate.relative_to(cwd.resolve())
            pathspec = str(candidate.relative_to(repository))
        except ValueError as e:
            raise GitError(f"commit path escapes the Lean project: {filename}") from e
        tracked = _git(["ls-files", "--error-unmatch", "--", pathspec], repository)
        if tracked.returncode != 0:
            _checked_git(["add", "--intent-to-add", "--", pathspec], repository)
        pathspecs.append(pathspec)
    _checked_git(["commit", "--only", "-m", message, "--", *pathspecs], repository)


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def setup_logging(log_dir: Path, verbose: bool = False) -> Path:
    """Configure structured logging to file + console; return the log path."""
    log_dir.mkdir(parents=True, exist_ok=True)
    log_file = log_dir / f"autolean_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log"

    file_handler = logging.FileHandler(log_file, encoding="utf-8")
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(
        logging.Formatter(
            "%(asctime)s | %(levelname)-7s | %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S",
        )
    )

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
    persist: bool = True
    results_file: Path = field(init=False)
    records: list[ExperimentRecord] = field(default_factory=list)
    _cycle: int = 0
    _branch_name: str | None = field(default=None, init=False, repr=False)

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
            self._write_tsv([])
            return
        with open(self.results_file, newline="", encoding="utf-8") as handle:
            reader = csv.DictReader(handle, delimiter="\t")
            header = reader.fieldnames or []
            if header == TSV_FIELDS:
                return
            unknown = set(header) - set(TSV_FIELDS)
            if unknown:
                fields = ", ".join(sorted(unknown))
                raise ValueError(f"results.tsv has unsupported fields: {fields}")
            rows = list(reader)
        self._write_tsv(rows)

    def _write_tsv(self, rows: list[dict[str, str]]) -> None:
        """Write a complete TSV schema through an atomic replacement."""
        temporary = self.results_file.with_name(f".{self.results_file.name}.tmp")
        with open(temporary, "w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(
                handle,
                fieldnames=TSV_FIELDS,
                delimiter="\t",
                extrasaction="ignore",
            )
            writer.writeheader()
            writer.writerows(rows)
        temporary.replace(self.results_file)

    def log(self, record: ExperimentRecord) -> None:
        """Append a record to the TSV log and structured log."""
        self.records.append(record)
        if self.persist:
            self._ensure_tsv_header()
            with open(self.results_file, "a", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=TSV_FIELDS, delimiter="\t")
                writer.writerow(record.as_dict())

        # Structured log entry
        log.info(
            "cycle=%d target=%s outcome=%s attempt=%d duration=%.1fs "
            "input_tokens=%d output_tokens=%d model=%s backend=%s environment=%s%s",
            record.cycle,
            record.decl_name,
            record.outcome.value,
            record.attempt,
            record.duration_seconds,
            record.llm_input_tokens,
            record.llm_tokens,
            record.model or "unknown",
            record.backend or "unknown",
            record.environment_sha256[:12] or "unknown",
            f" error_cat={record.error_category}" if record.error_category else "",
        )

    # -- Git integration ----------------------------------------------------

    def setup_branch(self, branch_name: str | None = None) -> str:
        if branch_name is None:
            date_str = datetime.now().strftime("%b%d").lower()
            branch_name = f"autolean/{date_str}"
        git_init(self.project_root)
        git_create_branch(self.project_root, branch_name)
        self._branch_name = branch_name
        console.print(f"[green]Branch:[/] {branch_name}")
        return branch_name

    def commit_success(self, record: ExperimentRecord) -> None:
        if self._branch_name is None:
            raise GitError("proof commits require a successfully prepared branch")
        current = _checked_git(["branch", "--show-current"], self.project_root).stdout.strip()
        if current != self._branch_name:
            raise GitError(f"proof branch changed: expected '{self._branch_name}', found '{current}'")
        if record.outcome == Outcome.GAP_FILLED:
            msg = f"library: Add {record.decl_name}\n\nCycle {record.cycle}, attempt {record.attempt}."
        else:
            msg = f"proof: Prove {record.decl_name}\n\nCycle {record.cycle}, attempt {record.attempt}."
        if record.environment_sha256:
            msg += f"\nEnvironment SHA-256:\n  {record.environment_sha256}"
        if record.proof_sha256:
            msg += f"\nProof SHA-256:\n  {record.proof_sha256}"
        if record.axioms:
            msg += f"\nAxioms: {record.axioms}"
        git_commit(self.project_root, msg, [record.file])

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
        validated = sum(1 for r in self.records if r.outcome == Outcome.VALIDATED)
        failures = sum(1 for r in self.records if r.outcome in FAILURE_OUTCOMES)
        neutral = total - successes - validated - failures

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
        metrics.add_row("Proofs accepted", f"[bold green]{successes}[/bold green]")
        if validated:
            metrics.add_row("Dry-run proofs validated", f"[bold green]{validated}[/bold green]")
        metrics.add_row("Failed attempts", f"[red]{failures}[/red]")
        if neutral:
            metrics.add_row("Skipped attempts", f"[yellow]{neutral}[/yellow]")

        if total > 0:
            metrics.add_row(
                "Candidate pass rate",
                f"{(successes + validated) / total * 100:.1f}%",
            )

            first_attempts = [r for r in self.records if r.attempt == 1]
            first_successes = sum(
                1 for r in first_attempts if r.outcome in (Outcome.SUCCESS, Outcome.VALIDATED)
            )
            if first_attempts:
                rate = first_successes / len(first_attempts) * 100
                metrics.add_row(
                    "First-attempt pass",
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

            input_tokens = sum(r.llm_input_tokens for r in self.records)
            output_tokens = sum(r.llm_tokens for r in self.records)
            if input_tokens:
                metrics.add_row("Input tokens", f"{input_tokens:,}")
            if output_tokens:
                metrics.add_row("Output tokens", f"{output_tokens:,}")
            if input_tokens + output_tokens > 0 and successes > 0:
                metrics.add_row(
                    "Tokens per proof",
                    f"{(input_tokens + output_tokens) / successes:,.0f}",
                )
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
            if outcome_val in {Outcome.SUCCESS.value, Outcome.VALIDATED.value}:
                style = "green"
            elif outcome_val in {Outcome.SKIPPED.value, Outcome.GAP_FILLED.value}:
                style = "yellow"
            else:
                style = "red"
            pct = count / total * 100 if total > 0 else 0
            outcome_table.add_row(
                f"[{style}]{outcome_val}[/{style}]",
                f"[{style}]{count}[/{style}]",
                f"{pct:.0f}%",
            )

        console.print(outcome_table)
