"""The autonomous agent loop — the heart of AutoLean.

Inspired by Karpathy's autoresearch and RightNow-AI's autokernel:
  edit → build → evaluate → keep/revert → log → repeat
"""

from __future__ import annotations

import re
import signal
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

from rich.console import Console
from rich.panel import Panel

from autolean.error_classifier import ErrorCategory, classify_error, retry_hint_for
from autolean.lean_interface import LeanProject
from autolean.llm_client import LLMConfig, OllamaClient
from autolean.prompts import SORRY_FILL_USER, SYSTEM_PROMPT
from autolean.scanner import SorryTarget, prioritize_targets, scan_project
from autolean.tracker import ExperimentRecord, ExperimentTracker, Outcome

console = Console()

# Temperature escalation per retry attempt (capped at 1.0)
TEMP_ESCALATION_STEP = 0.1
TEMP_MAX = 1.0

# Maximum proof lines before we reject without building
DEFAULT_MAX_PROOF_LINES = 50


# ---------------------------------------------------------------------------
# Program.md parser
# ---------------------------------------------------------------------------


@dataclass
class ProgramConfig:
    """Parsed configuration from program.md."""

    mode: str = "sorry-elimination"
    lean_project_path: str = "workspace"
    model: str = "gemma4:26b"
    temperature: float = 0.4
    max_retries_per_sorry: int = 5
    cycle_timeout_seconds: int = 120
    max_cycles: int = 0  # 0 = unlimited
    max_proof_lines: int = DEFAULT_MAX_PROOF_LINES
    goals: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    strategy_hints: list[str] = field(default_factory=list)


def parse_program(path: Path) -> ProgramConfig:
    """Parse a program.md file into structured config."""
    content = path.read_text(encoding="utf-8")
    cfg = ProgramConfig()

    # Extract mode
    mode_match = re.search(r"## Mode\s*\n.*?\n(\S+)", content)
    if mode_match:
        cfg.mode = mode_match.group(1).strip()

    # Extract lean project path
    path_match = re.search(r"## Lean Project Path\s*\n.*?\n(\S+)", content)
    if path_match:
        cfg.lean_project_path = path_match.group(1).strip()

    # Extract LLM config key: value pairs
    def _extract_kv(key: str, default: str) -> str:
        m = re.search(rf"{key}:\s*(\S+)", content)
        return m.group(1) if m else default

    cfg.model = _extract_kv("model", cfg.model)

    try:
        cfg.temperature = float(_extract_kv("temperature", str(cfg.temperature)))
    except ValueError:
        pass
    try:
        cfg.max_retries_per_sorry = int(_extract_kv("max_retries_per_sorry", str(cfg.max_retries_per_sorry)))
    except ValueError:
        pass
    try:
        cfg.cycle_timeout_seconds = int(_extract_kv("cycle_timeout_seconds", str(cfg.cycle_timeout_seconds)))
    except ValueError:
        pass
    try:
        cfg.max_cycles = int(_extract_kv("max_cycles", str(cfg.max_cycles)))
    except ValueError:
        pass

    # Extract list sections
    def extract_list(header: str) -> list[str]:
        pattern = rf"## {header}\s*\n.*?\n((?:\d+\..*?\n?)+)"
        m = re.search(pattern, content, re.DOTALL)
        if m:
            return [
                line.strip().lstrip("0123456789.-) ").strip()
                for line in m.group(1).strip().split("\n")
                if line.strip() and not line.strip().startswith("<!--")
            ]
        return []

    cfg.goals = extract_list("Goals")
    cfg.constraints = extract_list("Constraints")

    # Extract strategy hints (using - prefix)
    hints_match = re.search(r"## Strategy Hints\s*\n.*?\n((?:- .*?\n?)+)", content, re.DOTALL)
    if hints_match:
        cfg.strategy_hints = [
            line.strip().lstrip("- ").strip()
            for line in hints_match.group(1).strip().split("\n")
            if line.strip().startswith("-")
        ]

    return cfg


# ---------------------------------------------------------------------------
# Proof cleaner
# ---------------------------------------------------------------------------


def clean_llm_proof(raw: str, *, tactic_mode: bool = True) -> str:
    """Strip markdown fences and LLM artifacts from proof output.

    Args:
        raw: Raw LLM output text.
        tactic_mode: If True, the sorry is inside a `by` block, so
            a leading `by` in the output should be stripped (it would
            produce `by by ...`). If False, the sorry is in term mode,
            so a leading `by` should be preserved (the LLM is entering
            tactic mode).
    """
    text = raw.strip()

    # Remove markdown code fences
    text = re.sub(r"^```(?:lean4?|)\s*\n?", "", text)
    text = re.sub(r"\n?```\s*$", "", text)

    # Remove leading/trailing blank lines
    lines = text.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    # Strip leading `by` only in tactic mode (sorry is already inside `by`)
    if tactic_mode and lines and lines[0].strip() == "by":
        lines = lines[1:]
        # Also strip the resulting leading blank lines
        while lines and not lines[0].strip():
            lines.pop(0)

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


class AutoLeanAgent:
    """The autonomous proof agent."""

    def __init__(
        self,
        program_path: Path,
        *,
        dry_run: bool = False,
        verbose: bool = False,
    ):
        self.program_path = program_path.resolve()
        self.dry_run = dry_run
        self.verbose = verbose
        self._interrupted = False

        # Parse program.md
        self.config = parse_program(self.program_path)

        # Resolve lean project path (relative to program.md location)
        lean_root = self.program_path.parent / self.config.lean_project_path
        self.project = LeanProject(lean_root)

        # Initialize LLM client
        llm_cfg = LLMConfig(
            model=self.config.model,
            temperature=self.config.temperature,
        )
        self.llm = OllamaClient(config=llm_cfg)

        # Initialize tracker
        self.tracker = ExperimentTracker(project_root=self.project.root)

        # Track attempts per target
        self._attempts: dict[str, int] = {}
        self._failed_proofs: dict[str, list[str]] = {}
        self._last_error: dict[str, tuple[ErrorCategory, str]] = {}

        # Cache goal states per target (P0.3: avoids double builds)
        self._goal_cache: dict[str, str | None] = {}

        # Track initial sorry count for coverage metric
        self._initial_sorry_count: int = 0

    # -- Signal handling ----------------------------------------------------

    def _handle_interrupt(self, signum: int, frame: object) -> None:
        if self._interrupted:
            console.print("\n[red]Force quit.[/]")
            raise SystemExit(1)
        self._interrupted = True
        console.print(
            "\n[yellow]Interrupt received. Finishing current cycle, then stopping...[/]"
        )

    # -- Core loop ----------------------------------------------------------

    def run(self) -> None:
        """Main entry point — the autonomous loop."""
        signal.signal(signal.SIGINT, self._handle_interrupt)

        console.print(
            Panel(
                f"[bold]AutoLean Agent[/]\n"
                f"Mode: {self.config.mode}\n"
                f"Model: {self.config.model}\n"
                f"Project: {self.project.root}\n"
                f"Max retries/sorry: {self.config.max_retries_per_sorry}\n"
                f"Max cycles: {self.config.max_cycles or '∞'}",
                title="Starting",
                border_style="green",
            )
        )

        # Check LLM connectivity
        if not self.llm.ping():
            console.print("[red]Cannot connect to Ollama. Is it running?[/]")
            console.print(f"  Expected: {self.llm.config.base_url}")
            console.print(f"  Model: {self.llm.config.model}")
            console.print("\n  Try: ollama serve &")
            return

        console.print("[green]LLM connected.[/]")

        # Setup git branch
        if not self.dry_run:
            try:
                self.tracker.setup_branch()
            except Exception as e:
                console.print(f"[yellow]Git branch setup: {e}[/]")

        # Initial build check
        console.print("\n[bold]Initial build check...[/]")
        with console.status("[dim]Building...", spinner="dots"):
            build = self.project.build(timeout=self.config.cycle_timeout_seconds)
        if not build.success:
            n_errors = len(build.errors)
            console.print(
                f"[yellow]Project has {n_errors} build error(s) before we start.[/]"
            )
            if self.verbose:
                for d in build.errors[:5]:
                    console.print(f"  {d}")
        else:
            console.print(f"[green]Project builds clean ({build.duration_seconds:.1f}s).[/]")

        # P0.4: Scan ONCE before the loop (not every cycle)
        targets = scan_project(self.project.root)
        targets = prioritize_targets(targets)
        self._initial_sorry_count = len(targets)
        console.print(f"\n[bold]Found {len(targets)} sorry target(s).[/]")

        if not targets:
            console.print("[green]No sorries found — nothing to do![/]")
            return

        for t in targets[:10]:
            mode_label = "tactic" if t.tactic_mode else "term"
            console.print(f"  • {t} [{mode_label}]")
        if len(targets) > 10:
            console.print(f"  ... and {len(targets) - 10} more")

        # -- Main loop ------------------------------------------------------
        console.print(f"\n[bold green]Starting autonomous loop...[/]\n")
        session_start = time.monotonic()

        while not self._interrupted:
            cycle = self.tracker.next_cycle()

            # Check cycle budget
            if self.config.max_cycles > 0 and cycle > self.config.max_cycles:
                console.print(f"\n[yellow]Reached max_cycles ({self.config.max_cycles}). Stopping.[/]")
                break

            # P0.4: Filter active targets from persistent list (no rescan)
            active_targets = [
                t for t in targets
                if self._attempts.get(t.id, 0) < self.config.max_retries_per_sorry
            ]

            if not active_targets:
                console.print(
                    f"\n[green]All targets either proved or exhausted retries. Done![/]"
                )
                break

            target = active_targets[0]
            attempt_num = self._attempts.get(target.id, 0) + 1
            self._attempts[target.id] = attempt_num

            console.rule(
                f"Cycle {cycle} | {target.decl_name} | "
                f"attempt {attempt_num}/{self.config.max_retries_per_sorry}"
            )

            record = self._try_fill_sorry(cycle, target, attempt_num)
            self.tracker.log(record)

            # P0.4: On success, remove the target from the persistent list
            if record.outcome == Outcome.SUCCESS:
                targets = [t for t in targets if t.id != target.id]

            # Status line
            summary = self.tracker.summary()
            proved = summary.get("success", 0)
            remaining = len([
                t for t in targets
                if self._attempts.get(t.id, 0) < self.config.max_retries_per_sorry
            ])
            elapsed = time.monotonic() - session_start
            rate = proved / (elapsed / 3600) if elapsed > 0 else 0
            coverage = proved / self._initial_sorry_count * 100 if self._initial_sorry_count else 0

            console.print(
                f"  -> [{'green' if record.outcome == Outcome.SUCCESS else 'red'}]"
                f"{record.outcome.value}[/]"
                f"{f' ({record.error_category})' if record.error_category else ''} "
                f"({record.duration_seconds:.1f}s, {record.llm_tokens} tok) | "
                f"Proved: {proved}/{self._initial_sorry_count} ({coverage:.0f}%) | "
                f"Remaining: {remaining} | "
                f"Rate: {rate:.1f}/hr"
            )

        # -- Session complete -----------------------------------------------
        console.print()
        self.tracker.print_summary(initial_count=self._initial_sorry_count)

        elapsed_total = time.monotonic() - session_start
        console.print(f"\nTotal time: {elapsed_total / 60:.1f} minutes")
        console.print(f"Results: {self.tracker.results_file}")

    # -- Single sorry attempt -----------------------------------------------

    def _try_fill_sorry(
        self, cycle: int, target: SorryTarget, attempt: int
    ) -> ExperimentRecord:
        """Try to fill a single sorry target. Returns the experiment record."""
        t0 = time.monotonic()
        file_path = target.file
        original_content = self.project.read_file(file_path)

        # -- Step 1: Build context for LLM ----------------------------------
        lines = original_content.split("\n")
        start = max(0, target.decl_line - 1)
        end = min(len(lines), target.line + 20)
        file_context = "\n".join(
            f"{i + start + 1:4d} | {l}" for i, l in enumerate(lines[start:end])
        )

        # P0.2 + P0.3: Get goal state via hole-punch (cached per target)
        if target.id not in self._goal_cache:
            if self.verbose:
                console.print(f"  [dim]Extracting goal state (hole-punch)...[/]")
            with console.status("[dim]Extracting goal state...", spinner="dots"):
                self._goal_cache[target.id] = self.project.get_goal_via_hole_punch(
                    file_path, target.line, target.col,
                    timeout=self.config.cycle_timeout_seconds,
                )
        goal_state = self._goal_cache[target.id]

        if self.verbose and goal_state:
            console.print(f"  [dim]Goal: {goal_state[:120]}...[/]")

        # Format failed attempts with error-informed hints (P2.3)
        prev_fails = self._failed_proofs.get(target.id, [])
        if prev_fails:
            failed_parts = []
            for i, p in enumerate(prev_fails[-3:]):  # last 3
                failed_parts.append(f"Attempt {i + 1} (failed):\n```\n{p}\n```")
            failed_str = "\n".join(failed_parts)
            # Add error-informed hint from last failure
            last_err = self._last_error.get(target.id)
            if last_err:
                category, msg = last_err
                failed_str += f"\n\n{retry_hint_for(category, msg)}"
        else:
            failed_str = "(none)"

        # Add strategy hints to context
        hints = "\n".join(f"- {h}" for h in self.config.strategy_hints)
        if hints:
            file_context += f"\n\n## Strategy Hints\n{hints}"

        # -- Step 2: Ask LLM -----------------------------------------------
        user_prompt = SORRY_FILL_USER.format(
            file_context=file_context,
            line=target.line,
            decl_name=target.decl_name,
            goal_state=goal_state or "(goal state unavailable -- try to infer from context)",
            failed_attempts=failed_str,
        )

        if self.verbose:
            console.print(f"  [dim]Querying {self.config.model}...[/]")

        # P2.2: Cap temperature escalation
        temp = min(
            self.config.temperature + (attempt - 1) * TEMP_ESCALATION_STEP,
            TEMP_MAX,
        )

        try:
            response = self.llm.generate(
                system=SYSTEM_PROMPT,
                user=user_prompt,
                temperature=temp,
            )
        except Exception as e:
            return self._make_record(
                cycle, target, attempt, t0,
                outcome=Outcome.FAIL_BUILD,
                error_summary=f"LLM error: {e}",
            )

        # -- Step 3: Clean and apply proof ----------------------------------
        # P0.1: Pass tactic_mode to clean_llm_proof
        proof = clean_llm_proof(response.text, tactic_mode=target.tactic_mode)

        if not proof or re.search(r"\bsorry\b", proof):
            self._failed_proofs.setdefault(target.id, []).append(response.text)
            return self._make_record(
                cycle, target, attempt, t0,
                outcome=Outcome.FAIL_SORRY_REMAINS,
                llm_tokens=response.eval_count,
                llm_tok_per_sec=response.tokens_per_second,
                error_summary="LLM output contains sorry or is empty",
            )

        # P3.5: Reject proofs that are too long
        proof_lines = len(proof.splitlines())
        if proof_lines > self.config.max_proof_lines:
            self._failed_proofs.setdefault(target.id, []).append(proof)
            return self._make_record(
                cycle, target, attempt, t0,
                outcome=Outcome.FAIL_BUILD,
                llm_tokens=response.eval_count,
                llm_tok_per_sec=response.tokens_per_second,
                error_summary=f"Proof too long: {proof_lines} lines > {self.config.max_proof_lines} max",
                proof_length=proof_lines,
            )

        if self.verbose:
            console.print(f"  [dim]Generated proof ({proof_lines} lines):[/]")
            for line in proof.splitlines()[:10]:
                console.print(f"    [cyan]{line}[/]")

        if self.dry_run:
            console.print(f"  [yellow]DRY RUN -- skipping file write and build[/]")
            return self._make_record(
                cycle, target, attempt, t0,
                outcome=Outcome.SKIPPED,
                llm_tokens=response.eval_count,
                llm_tok_per_sec=response.tokens_per_second,
                proof_length=proof_lines,
            )

        # Apply the proof
        try:
            new_content = self.project.replace_sorry_at(
                file_path, target.line, proof, original_content=original_content
            )
            self.project.write_file(file_path, new_content)
        except Exception as e:
            return self._make_record(
                cycle, target, attempt, t0,
                outcome=Outcome.FAIL_BUILD,
                llm_tokens=response.eval_count,
                llm_tok_per_sec=response.tokens_per_second,
                error_summary=f"Replace error: {e}",
            )

        # -- Step 4: Build and check ----------------------------------------
        if self.verbose:
            console.print(f"  [dim]Building...[/]")

        with console.status("[dim]Building...", spinner="dots"):
            build = self.project.check_file(
                file_path, timeout=self.config.cycle_timeout_seconds
            )
        duration = time.monotonic() - t0

        # -- Step 5: Keep or revert -----------------------------------------
        if build.success and not any(
            "sorry" in e.message.lower() for e in build.warnings
        ):
            # SUCCESS -- sorry is gone and file builds clean
            rel_path = str(file_path.relative_to(self.project.root))
            record = ExperimentRecord(
                cycle=cycle,
                timestamp=datetime.now(timezone.utc).isoformat(),
                target_id=target.id,
                decl_name=target.decl_name,
                file=rel_path,
                line=target.line,
                outcome=Outcome.SUCCESS,
                attempt=attempt,
                duration_seconds=duration,
                llm_tokens=response.eval_count,
                llm_tok_per_sec=response.tokens_per_second,
                proof_length=proof_lines,
                build_duration_seconds=build.duration_seconds,
            )
            self.tracker.commit_success(record)
            self._failed_proofs.pop(target.id, None)
            self._last_error.pop(target.id, None)
            return record

        # FAILURE -- revert with error handling (P0.6)
        error_summary = ""
        error_category = ""
        if build.errors:
            error_summary = build.errors[0].message[:500]
            cat = classify_error(error_summary)
            error_category = cat.value
            self._last_error[target.id] = (cat, error_summary)
        elif not build.success:
            error_summary = build.stderr[:500] if build.stderr else "Build failed (unknown)"
            error_category = ErrorCategory.OTHER.value
        else:
            error_summary = f"sorry still present after replacement at line {target.line}"
            error_category = ErrorCategory.SORRY_REMAINS.value

        # P0.6: Safe revert with fallback
        try:
            self.project.write_file(file_path, original_content)
        except OSError as e:
            console.print(f"[red]CRITICAL: Failed to revert {file_path}: {e}[/]")
            try:
                from autolean.tracker import git_revert_file
                rel = str(file_path.relative_to(self.project.root))
                git_revert_file(self.project.root, rel)
                console.print(f"[yellow]Recovered via git checkout.[/]")
            except Exception:
                console.print("[red]Git revert also failed. Manual intervention needed.[/]")

        self._failed_proofs.setdefault(target.id, []).append(proof)

        return ExperimentRecord(
            cycle=cycle,
            timestamp=datetime.now(timezone.utc).isoformat(),
            target_id=target.id,
            decl_name=target.decl_name,
            file=str(file_path.relative_to(self.project.root)),
            line=target.line,
            outcome=Outcome.FAIL_BUILD if build.errors else Outcome.FAIL_SORRY_REMAINS,
            attempt=attempt,
            duration_seconds=duration,
            llm_tokens=response.eval_count,
            llm_tok_per_sec=response.tokens_per_second,
            error_summary=error_summary,
            error_category=error_category,
            build_duration_seconds=build.duration_seconds,
        )

    # -- Helpers ------------------------------------------------------------

    def _make_record(
        self,
        cycle: int,
        target: SorryTarget,
        attempt: int,
        t0: float,
        *,
        outcome: Outcome,
        error_summary: str = "",
        error_category: str = "",
        llm_tokens: int = 0,
        llm_tok_per_sec: float = 0.0,
        proof_length: int = 0,
        build_duration_seconds: float = 0.0,
    ) -> ExperimentRecord:
        """Helper to create an ExperimentRecord with common fields."""
        return ExperimentRecord(
            cycle=cycle,
            timestamp=datetime.now(timezone.utc).isoformat(),
            target_id=target.id,
            decl_name=target.decl_name,
            file=str(target.file.relative_to(self.project.root)),
            line=target.line,
            outcome=outcome,
            attempt=attempt,
            duration_seconds=time.monotonic() - t0,
            llm_tokens=llm_tokens,
            llm_tok_per_sec=llm_tok_per_sec,
            error_summary=error_summary,
            error_category=error_category,
            proof_length=proof_length,
            build_duration_seconds=build_duration_seconds,
        )
