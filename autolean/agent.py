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
from rich.live import Live
from rich.panel import Panel
from rich.progress import Progress, SpinnerColumn, TextColumn, TimeElapsedColumn
from rich.text import Text

from autolean.lean_interface import BuildResult, LeanProject
from autolean.llm_client import LLMConfig, LLMResponse, OllamaClient
from autolean.prompts import SORRY_FILL_USER, SYSTEM_PROMPT
from autolean.scanner import SorryTarget, prioritize_targets, scan_project
from autolean.tracker import ExperimentRecord, ExperimentTracker, Outcome

console = Console()


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

    # Extract LLM config
    model_match = re.search(r"model:\s*(\S+)", content)
    if model_match:
        cfg.model = model_match.group(1)

    temp_match = re.search(r"temperature:\s*([\d.]+)", content)
    if temp_match:
        cfg.temperature = float(temp_match.group(1))

    retries_match = re.search(r"max_retries_per_sorry:\s*(\d+)", content)
    if retries_match:
        cfg.max_retries_per_sorry = int(retries_match.group(1))

    timeout_match = re.search(r"cycle_timeout_seconds:\s*(\d+)", content)
    if timeout_match:
        cfg.cycle_timeout_seconds = int(timeout_match.group(1))

    cycles_match = re.search(r"max_cycles:\s*(\d+)", content)
    if cycles_match:
        cfg.max_cycles = int(cycles_match.group(1))

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


def clean_llm_proof(raw: str) -> str:
    """Strip markdown fences and other LLM artifacts from proof output."""
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

    # If the proof is wrapped in `by\n...`, strip the `by`
    if lines and lines[0].strip() == "by":
        lines = lines[1:]

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
                branch = self.tracker.setup_branch()
            except Exception as e:
                console.print(f"[yellow]Git branch setup: {e}[/]")

        # Initial build check
        console.print("\n[bold]Initial build check...[/]")
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
            console.print("[green]Project builds clean (with sorry warnings).[/]")

        # Scan for targets
        targets = scan_project(self.project.root)
        targets = prioritize_targets(targets)
        console.print(f"\n[bold]Found {len(targets)} sorry target(s).[/]")

        if not targets:
            console.print("[green]No sorries found — nothing to do![/]")
            return

        for t in targets[:10]:
            console.print(f"  • {t}")
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

            # Re-scan for remaining targets (some may have been resolved)
            targets = scan_project(self.project.root)
            targets = prioritize_targets(targets)

            # Filter to targets we haven't exhausted retries on
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
                f"Cycle {cycle} │ {target.decl_name} │ "
                f"attempt {attempt_num}/{self.config.max_retries_per_sorry}"
            )

            record = self._try_fill_sorry(cycle, target, attempt_num)
            self.tracker.log(record)

            # Status line
            summary = self.tracker.summary()
            proved = summary.get("success", 0)
            remaining = len(active_targets) - (1 if record.outcome == Outcome.SUCCESS else 0)
            elapsed = time.monotonic() - session_start
            rate = proved / (elapsed / 3600) if elapsed > 0 else 0

            console.print(
                f"  → [{'green' if record.outcome == Outcome.SUCCESS else 'red'}]"
                f"{record.outcome.value}[/] "
                f"({record.duration_seconds:.1f}s, {record.llm_tokens} tok) │ "
                f"Proved: {proved} │ Remaining: {remaining} │ "
                f"Rate: {rate:.1f}/hr"
            )

        # -- Session complete -----------------------------------------------
        console.print()
        self.tracker.print_summary()

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
        # Get the file content around the sorry
        lines = original_content.split("\n")
        start = max(0, target.decl_line - 1)
        end = min(len(lines), target.line + 20)
        file_context = "\n".join(
            f"{i + start + 1:4d} │ {l}" for i, l in enumerate(lines[start:end])
        )

        # Get goal state from build diagnostics
        goal_state = self._get_goal_state(file_path, target.line)

        # Format failed attempts
        prev_fails = self._failed_proofs.get(target.id, [])
        if prev_fails:
            failed_str = "\n".join(
                f"Attempt {i + 1} (failed):\n```\n{p}\n```"
                for i, p in enumerate(prev_fails[-3:])  # last 3
            )
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
            goal_state=goal_state or "(goal state unavailable — try to infer from context)",
            failed_attempts=failed_str,
        )

        if self.verbose:
            console.print(f"  [dim]Querying {self.config.model}...[/]")

        try:
            response = self.llm.generate(
                system=SYSTEM_PROMPT,
                user=user_prompt,
                temperature=self.config.temperature + (attempt - 1) * 0.1,  # increase temp on retries
            )
        except Exception as e:
            return ExperimentRecord(
                cycle=cycle,
                timestamp=datetime.now(timezone.utc).isoformat(),
                target_id=target.id,
                decl_name=target.decl_name,
                file=str(file_path.relative_to(self.project.root)),
                line=target.line,
                outcome=Outcome.FAIL_BUILD,
                attempt=attempt,
                duration_seconds=time.monotonic() - t0,
                llm_tokens=0,
                llm_tok_per_sec=0,
                error_summary=f"LLM error: {e}",
            )

        # -- Step 3: Clean and apply proof ----------------------------------
        proof = clean_llm_proof(response.text)

        if not proof or "sorry" in proof.lower():
            self._failed_proofs.setdefault(target.id, []).append(response.text)
            return ExperimentRecord(
                cycle=cycle,
                timestamp=datetime.now(timezone.utc).isoformat(),
                target_id=target.id,
                decl_name=target.decl_name,
                file=str(file_path.relative_to(self.project.root)),
                line=target.line,
                outcome=Outcome.FAIL_SORRY_REMAINS,
                attempt=attempt,
                duration_seconds=time.monotonic() - t0,
                llm_tokens=response.eval_count,
                llm_tok_per_sec=response.tokens_per_second,
                error_summary="LLM output contains sorry or is empty",
            )

        if self.verbose:
            console.print(f"  [dim]Generated proof ({len(proof.splitlines())} lines):[/]")
            for line in proof.splitlines()[:10]:
                console.print(f"    [cyan]{line}[/]")

        if self.dry_run:
            console.print(f"  [yellow]DRY RUN — skipping file write and build[/]")
            return ExperimentRecord(
                cycle=cycle,
                timestamp=datetime.now(timezone.utc).isoformat(),
                target_id=target.id,
                decl_name=target.decl_name,
                file=str(file_path.relative_to(self.project.root)),
                line=target.line,
                outcome=Outcome.SKIPPED,
                attempt=attempt,
                duration_seconds=time.monotonic() - t0,
                llm_tokens=response.eval_count,
                llm_tok_per_sec=response.tokens_per_second,
                proof_length=len(proof.splitlines()),
            )

        # Apply the proof
        try:
            new_content = self.project.replace_sorry_at(
                file_path, target.line, proof, original_content=original_content
            )
            self.project.write_file(file_path, new_content)
        except Exception as e:
            return ExperimentRecord(
                cycle=cycle,
                timestamp=datetime.now(timezone.utc).isoformat(),
                target_id=target.id,
                decl_name=target.decl_name,
                file=str(file_path.relative_to(self.project.root)),
                line=target.line,
                outcome=Outcome.FAIL_BUILD,
                attempt=attempt,
                duration_seconds=time.monotonic() - t0,
                llm_tokens=response.eval_count,
                llm_tok_per_sec=response.tokens_per_second,
                error_summary=f"Replace error: {e}",
            )

        # -- Step 4: Build and check ----------------------------------------
        if self.verbose:
            console.print(f"  [dim]Building...[/]")

        build = self.project.check_file(
            file_path, timeout=self.config.cycle_timeout_seconds
        )
        duration = time.monotonic() - t0

        # -- Step 5: Keep or revert -----------------------------------------
        if build.success and not any(
            "sorry" in e.message.lower() for e in build.warnings
        ):
            # SUCCESS — the sorry is gone and the file builds clean
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
                proof_length=len(proof.splitlines()),
            )
            self.tracker.commit_success(record)
            # Clear failed attempts for this target
            self._failed_proofs.pop(target.id, None)
            return record

        # FAILURE — revert
        error_summary = ""
        if build.errors:
            error_summary = build.errors[0].message[:200]
        elif not build.success:
            error_summary = build.stderr[:200] if build.stderr else "Build failed (unknown)"
        else:
            error_summary = "sorry still present after replacement"

        self.project.write_file(file_path, original_content)
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
        )

    # -- Helpers ------------------------------------------------------------

    def _get_goal_state(self, file_path: Path, line: int) -> str | None:
        """Extract goal state from build diagnostics at a sorry line."""
        result = self.project.build(timeout=self.config.cycle_timeout_seconds)
        rel = str(file_path.relative_to(self.project.root))

        for diag in result.diagnostics:
            if rel in diag.file and abs(diag.line - line) <= 2:
                if "unsolved goals" in diag.message.lower():
                    return diag.message
                if "expected type" in diag.message.lower():
                    return diag.message

        # Fallback: look for any diagnostic near the sorry line
        for diag in result.diagnostics:
            if rel in diag.file and abs(diag.line - line) <= 5:
                return diag.message

        return None
