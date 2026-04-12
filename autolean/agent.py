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
from autolean.llm_client import LLMBackend, LLMConfig, create_llm_client
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


# Common Lean tactic keywords for detecting tactic-like lines
_TACTIC_KEYWORDS = {
    "simp", "ring", "omega", "decide", "norm_num", "trivial", "rfl",
    "exact", "apply", "intro", "intros", "constructor", "cases", "rcases",
    "induction", "have", "let", "show", "calc", "conv", "rw", "rewrite",
    "unfold", "dsimp", "field_simp", "push_neg", "contradiction",
    "exfalso", "assumption", "tauto", "aesop", "linarith", "positivity",
    "ext", "funext", "use", "exists", "obtain", "refine", "by_contra",
    "by_cases", "split", "left", "right", "next", "case", "first",
    "try", "repeat", "all_goals", "any_goals", "focus",
    "by", "do", "return", "match", "if", "then", "else", "where",
    "|", "·", ".", "<;>",
}


def _is_tactic_line(line: str) -> bool:
    """Check if a line looks like Lean tactic code (not English text)."""
    stripped = line.strip()
    if not stripped:
        return True  # blank lines are fine in tactic blocks
    # Check if the first token is a known tactic keyword
    first_token = stripped.split()[0].rstrip("(").rstrip("{") if stripped.split() else ""
    if first_token in _TACTIC_KEYWORDS:
        return True
    # Indented lines in a tactic block are likely continuations
    if line.startswith("  ") or line.startswith("\t"):
        return True
    # Lines starting with | or · are case arms
    if stripped.startswith("|") or stripped.startswith("·"):
        return True
    # Lines with := or => are likely tactic fragments
    if ":=" in stripped or "=>" in stripped:
        return True
    return False


def clean_llm_proof(raw: str, *, tactic_mode: bool = True) -> str:
    """Strip markdown fences and LLM artifacts from proof output.

    This function is aggressive about extracting tactic code from verbose
    LLM responses. It handles:
    - Markdown code fences (```lean ... ```)
    - English text before/after the tactic block
    - Leading `by` keyword (in tactic mode)
    - Explanatory text that mentions "sorry"

    Args:
        raw: Raw LLM output text.
        tactic_mode: If True, the sorry is inside a `by` block, so
            a leading `by` in the output should be stripped.
    """
    text = raw.strip()

    # Strategy 1: If there's a fenced code block, extract it
    fenced = re.search(r"```(?:lean4?|)\s*\n(.*?)```", text, re.DOTALL)
    if fenced:
        text = fenced.group(1).strip()
    else:
        # Remove partial fences
        text = re.sub(r"^```(?:lean4?|)\s*\n?", "", text)
        text = re.sub(r"\n?```\s*$", "", text)

    # Remove leading/trailing blank lines
    lines = text.split("\n")
    while lines and not lines[0].strip():
        lines.pop(0)
    while lines and not lines[-1].strip():
        lines.pop()

    # Strategy 2: If lines mix English and tactics, extract only tactic lines
    # Detect if the output starts with English prose (not a tactic keyword)
    if lines and not _is_tactic_line(lines[0]):
        # Find the first tactic-like line
        tactic_start = None
        for i, line in enumerate(lines):
            if _is_tactic_line(line) and line.strip():
                tactic_start = i
                break
        if tactic_start is not None:
            lines = lines[tactic_start:]
        # Trim trailing English text
        while lines and not _is_tactic_line(lines[-1]):
            lines.pop()

    # Strip leading `by` only in tactic mode (sorry is already inside `by`)
    if tactic_mode and lines and lines[0].strip() == "by":
        lines = lines[1:]
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
        resume: bool = False,
    ):
        self.program_path = program_path.resolve()
        self.dry_run = dry_run
        self.verbose = verbose
        self.resume = resume
        self._interrupted = False

        # Parse program.md
        self.config = parse_program(self.program_path)

        # Resolve lean project path (relative to program.md location)
        lean_root = self.program_path.parent / self.config.lean_project_path
        self.project = LeanProject(lean_root)

        # Initialize LLM client via backend factory (P3.4)
        llm_cfg = LLMConfig(
            model=self.config.model,
            temperature=self.config.temperature,
        )
        self.llm: LLMBackend = create_llm_client(llm_cfg)

        # Initialize tracker
        self.tracker = ExperimentTracker(project_root=self.project.root)

        # Track attempts per target
        self._attempts: dict[str, int] = {}
        self._failed_proofs: dict[str, list[str]] = {}
        self._last_error: dict[str, tuple[ErrorCategory, str]] = {}

        # Cache goal states per target (P0.3: avoids double builds)
        self._goal_cache: dict[str, str | None] = {}
        self._search_cache: dict[str, str] = {}  # lemma search results per target

        # Track initial sorry count for coverage metric
        self._initial_sorry_count: int = 0

        # P3.3: Resume state
        self._proved_ids: set[str] = set()

        # Self-improving loop: data collection + skill memory
        from autolean.collector import TrainingDataCollector
        from autolean.skills import SkillMemory
        self.collector = TrainingDataCollector(
            output_dir=self.project.root / "training_data"
        )
        self.skill_memory = SkillMemory(
            skills_dir=self.project.root / "skills"
        )

    # -- Resume loading (P3.3) ----------------------------------------------

    def _load_resume_state(self) -> None:
        """Load attempt counts and proved IDs from a previous results.tsv."""
        import csv

        if not self.tracker.results_file.exists():
            console.print("[yellow]No results.tsv found — starting fresh.[/]")
            return

        with open(self.tracker.results_file) as f:
            reader = csv.DictReader(f, delimiter="\t")
            for row in reader:
                tid = row.get("target_id", "")
                attempt = int(row.get("attempt", "0") or "0")
                outcome = row.get("outcome", "")
                cycle = int(row.get("cycle", "0") or "0")

                self._attempts[tid] = max(self._attempts.get(tid, 0), attempt)
                self.tracker._cycle = max(self.tracker._cycle, cycle)

                if outcome == "success":
                    self._proved_ids.add(tid)

        console.print(
            f"[dim]  Loaded {len(self._proved_ids)} proved, "
            f"{len(self._attempts)} attempted from previous session.[/]"
        )

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
        import logging
        from autolean.tracker import setup_logging

        signal.signal(signal.SIGINT, self._handle_interrupt)

        # Setup structured logging (audit trail)
        log_file = setup_logging(self.project.root / "logs", verbose=self.verbose)
        log = logging.getLogger("autolean")
        log.info("Config: model=%s temp=%.2f retries=%d timeout=%ds",
                 self.config.model, self.config.temperature,
                 self.config.max_retries_per_sorry, self.config.cycle_timeout_seconds)

        console.print(
            Panel(
                f"[bold]AutoLean Agent[/]\n"
                f"Mode:             {self.config.mode}\n"
                f"Model:            {self.config.model}\n"
                f"Project:          {self.project.root}\n"
                f"Max retries:      {self.config.max_retries_per_sorry}\n"
                f"Max cycles:       {self.config.max_cycles or '∞'}\n"
                f"Self-correction:  [green]ON[/green] (error-informed retries)\n"
                f"Data collection:  [green]ON[/green] (SFT + DPO for fine-tuning)\n"
                f"Skill learning:   [green]ON[/green] ({len(self.skill_memory.skills)} skills loaded)",
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

        # P3.3: Resume from previous session
        if self.resume:
            self._load_resume_state()
            proved_ids = {
                tid for tid, attempts in self._attempts.items()
                if tid in self._proved_ids
            }
            targets = [t for t in targets if t.id not in proved_ids]
            console.print(
                f"[cyan]Resumed:[/] {len(proved_ids)} already proved, "
                f"{len(targets)} remaining, cycle {self.tracker.cycle}"
            )

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
                if self.config.max_cycles == 0 and targets:
                    # Overnight mode: reset retry counts and start a new epoch.
                    # Bump temperature slightly for variety in new epoch.
                    epoch = max(self._attempts.values()) // self.config.max_retries_per_sorry + 1
                    console.print(
                        f"\n[cyan]Epoch {epoch}:[/] All retries exhausted. "
                        f"Resetting {len(targets)} targets for another pass..."
                    )
                    for t in targets:
                        self._attempts[t.id] = 0
                        self._failed_proofs.pop(t.id, None)
                        self._last_error.pop(t.id, None)
                        self._goal_cache.pop(t.id, None)
                    self.config.temperature = min(
                        self.config.temperature + 0.05, 1.0
                    )
                    continue
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

            # On success, rescan the modified file (line numbers may have shifted
            # due to multi-line proof insertion) and update the target list.
            if record.outcome == Outcome.SUCCESS:
                changed_file = target.file
                # Remove ALL targets from the changed file
                targets = [t for t in targets if t.file != changed_file]
                # Rescan just that file for remaining sorrys
                from autolean.scanner import scan_file
                new_targets = scan_file(changed_file, project_root=self.project.root)
                targets.extend(new_targets)
                targets = prioritize_targets(targets)
                # Invalidate goal cache for targets in this file (lines shifted)
                stale_ids = [k for k in self._goal_cache if changed_file.name in k]
                for k in stale_ids:
                    del self._goal_cache[k]

            # -- Rich status output --
            summary = self.tracker.summary()
            proved = summary.get("success", 0)
            remaining = len([
                t for t in targets
                if self._attempts.get(t.id, 0) < self.config.max_retries_per_sorry
            ])
            elapsed = time.monotonic() - session_start
            rate = proved / (elapsed / 3600) if elapsed > 0 else 0
            coverage = proved / self._initial_sorry_count * 100 if self._initial_sorry_count else 0

            # Outcome with color
            if record.outcome == Outcome.SUCCESS:
                icon, style = "✓", "bold green"
            elif record.outcome == Outcome.SKIPPED:
                icon, style = "→", "yellow"
            else:
                icon, style = "✗", "red"

            console.print(
                f"  [{style}]{icon} {record.outcome.value}[/{style}]"
                f"{f' [dim]({record.error_category})[/dim]' if record.error_category else ''}"
                f" [dim]({record.duration_seconds:.1f}s, {record.llm_tokens} tok)[/dim]"
            )

            # Show build error details (DX improvement)
            if record.error_summary and record.outcome != Outcome.SUCCESS:
                err_lines = record.error_summary.strip().split("\n")
                for eline in err_lines[:4]:
                    console.print(f"    [dim red]{eline[:120]}[/dim red]")
                if len(err_lines) > 4:
                    console.print(f"    [dim]... ({len(err_lines) - 4} more lines)[/dim]")

            # Progress bar
            bar_width = 30
            filled = int(coverage / 100 * bar_width) if self._initial_sorry_count else 0
            bar = "█" * filled + "░" * (bar_width - filled)
            console.print(
                f"  [{bar}] "
                f"[bold]{proved}[/bold]/{self._initial_sorry_count} "
                f"({coverage:.0f}%) | "
                f"{remaining} left | "
                f"{rate:.1f}/hr | "
                f"{elapsed / 60:.0f}m elapsed"
            )

        # -- Session complete — full report -----------------------------------
        self._print_final_report(targets, session_start)

    # -- Single sorry attempt -----------------------------------------------

    def _step(self, msg: str, style: str = "dim") -> None:
        """Print a timestamped agent step — always visible for full observability."""
        from datetime import datetime
        ts = datetime.now().strftime("%H:%M:%S")
        console.print(f"  [dim]{ts}[/dim] [{style}]{msg}[/{style}]")

    def _try_fill_sorry(
        self, cycle: int, target: SorryTarget, attempt: int
    ) -> ExperimentRecord:
        """Try to fill a single sorry target. Returns the experiment record."""
        t0 = time.monotonic()
        file_path = target.file
        original_content = self.project.read_file(file_path)

        # -- Step 1: Build context for LLM ----------------------------------
        self._step(f"Reading {file_path.name}:{target.line} ({target.decl_name})")
        lines = original_content.split("\n")
        start = max(0, target.decl_line - 1)
        end = min(len(lines), target.line + 20)
        file_context = "\n".join(
            f"{i + start + 1:4d} | {l}" for i, l in enumerate(lines[start:end])
        )

        # P0.2 + P0.3: Get goal state via hole-punch (cached per target)
        if target.id not in self._goal_cache:
            self._step("Extracting goal state (hole-punch: sorry -> ?_)")
            with console.status("[dim]Extracting goal state...", spinner="dots"):
                self._goal_cache[target.id] = self.project.get_goal_via_hole_punch(
                    file_path, target.line, target.col,
                    timeout=self.config.cycle_timeout_seconds,
                )
            if self._goal_cache[target.id]:
                goal_preview = self._goal_cache[target.id].replace("\n", " ")[:100]
                self._step(f"Goal: {goal_preview}", "cyan")
            else:
                self._step("Goal state unavailable (will infer from context)", "yellow")
        else:
            self._step("Goal state cached from previous attempt")
        goal_state = self._goal_cache[target.id]

        # Store context for training data collection
        self.collector.set_context(target.id, goal_state or "", file_context)

        # Format failed attempts with error-informed hints (P2.3)
        prev_fails = self._failed_proofs.get(target.id, [])
        if prev_fails:
            self._step(f"Self-correction: {len(prev_fails)} previous failures inform this attempt", "yellow")
            failed_parts = []
            for i, p in enumerate(prev_fails[-3:]):  # last 3
                failed_parts.append(f"Attempt {i + 1} (failed):\n```\n{p}\n```")
            failed_str = "\n".join(failed_parts)
            # Add error-informed hint from last failure
            last_err = self._last_error.get(target.id)
            if last_err:
                category, msg = last_err
                self._step(f"Last error: {category.value} — feeding hint to LLM", "yellow")
                failed_str += f"\n\n{retry_hint_for(category, msg)}"
        else:
            failed_str = "(none)"

        # Add strategy hints to context
        hints = "\n".join(f"- {h}" for h in self.config.strategy_hints)
        if hints:
            file_context += f"\n\n## Strategy Hints\n{hints}"

        # Inject learned skills (Hermes-inspired self-improving loop)
        skill_injection = self.skill_memory.get_prompt_injection(
            goal_state or file_context, max_skills=5
        )
        if skill_injection:
            n_skills = skill_injection.count("**")  // 2
            self._step(f"Injecting {n_skills} learned skills into prompt", "magenta")
            file_context += f"\n\n{skill_injection}"

        # Search mathlib for relevant lemmas (Loogle + LeanSearch)
        if attempt == 1:  # only search on first attempt (cached for retries)
            from autolean.search import search_relevant_lemmas, format_search_results_for_prompt
            self._step("Searching mathlib for relevant lemmas...")
            search_results = search_relevant_lemmas(
                goal_state or "", target.decl_name,
            )
            if search_results:
                self._step(f"Found {len(search_results)} relevant lemmas", "cyan")
                search_context = format_search_results_for_prompt(search_results)
                file_context += f"\n\n{search_context}"
                self._search_cache[target.id] = search_context
            else:
                self._step("No relevant lemmas found in mathlib", "dim")
        elif target.id in self._search_cache:
            file_context += f"\n\n{self._search_cache[target.id]}"

        # -- Step 2: Ask LLM -----------------------------------------------
        user_prompt = SORRY_FILL_USER.format(
            file_context=file_context,
            line=target.line,
            decl_name=target.decl_name,
            goal_state=goal_state or "(goal state unavailable -- try to infer from context)",
            failed_attempts=failed_str,
        )

        # P2.2: Cap temperature escalation
        temp = min(
            self.config.temperature + (attempt - 1) * TEMP_ESCALATION_STEP,
            TEMP_MAX,
        )

        self._step(f"Querying {self.config.model} (temp={temp:.2f})")

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

        # Reject if proof is empty or contains `sorry` as a standalone tactic.
        # Only match sorry at line start (possibly indented) — not in English text.
        has_sorry_tactic = bool(re.search(r"(?m)^\s*sorry\s*$", proof))
        if not proof or has_sorry_tactic:
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

        # Always show the generated proof (key DX: see what the LLM produces)
        if proof_lines <= 5 or self.verbose:
            console.print(f"  [bold]Proof[/] ({proof_lines} lines):")
            for pline in proof.splitlines()[:12]:
                console.print(f"    [cyan]{pline}[/]")
            if proof_lines > 12:
                console.print(f"    [dim]... ({proof_lines - 12} more lines)[/]")
        else:
            first = proof.splitlines()[0].strip()
            console.print(f"  [bold]Proof[/] ({proof_lines} lines): [cyan]{first}[/] ...")

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
        self._step("Verifying with lake build...")

        with console.status("[dim]Building...", spinner="dots"):
            build = self.project.check_file(
                file_path, timeout=self.config.cycle_timeout_seconds
            )
        duration = time.monotonic() - t0

        # -- Step 5: Keep or revert -----------------------------------------
        # Check success: build succeeded AND the sorry at our target line is gone.
        # We re-read the file and check if `sorry` is still at the original line.
        # This is robust: other sorrys in the file don't interfere.
        sorry_gone = False
        if build.success:
            new_file_content = self.project.read_file(file_path)
            new_lines = new_file_content.split("\n")
            if target.line <= len(new_lines):
                sorry_gone = "sorry" not in new_lines[target.line - 1]
            else:
                sorry_gone = True  # line shifted, original sorry is gone
        if sorry_gone:
            # SUCCESS -- sorry is gone and file builds clean!
            console.print(
                f"  [bold green]PROVED![/bold green] "
                f"[green]{target.decl_name}[/green] "
                f"[dim]({build.duration_seconds:.1f}s build)[/dim]"
            )
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

            # Self-improving loop: collect training data + learn skill
            self._step("Committing to git + collecting training data", "green")
            self.collector.record_attempt(record, proof)
            skill = self.skill_memory.learn_from_proof(
                theorem_name=target.decl_name,
                theorem_statement=target.context_before[:200],
                proof=proof,
                goal_state=goal_state or "",
            )
            if skill:
                self._step(f"Learned skill: {skill.name} ({skill.description[:60]})", "magenta")
            return record

        # FAILURE -- revert with error handling (P0.6)
        self._step("Build failed — analyzing error...", "red")
        error_summary = ""
        error_category = ""
        if build.errors:
            error_summary = build.errors[0].message[:500]
            cat = classify_error(error_summary)
            error_category = cat.value
            self._last_error[target.id] = (cat, error_summary)

            # Auto-detect missing definitions and try to fill gaps
            from autolean.library import detect_missing_definitions, fill_gap
            gaps = detect_missing_definitions(error_summary, file_context, str(file_path))
            if gaps:
                for gap in gaps[:2]:  # max 2 gaps per attempt
                    self._step(f"Detected missing: {gap.name} — attempting to define it", "yellow")
                    definition = fill_gap(gap, file_path, self.llm.generate)
                    if definition:
                        self._step(f"Generated definition for {gap.name} ({len(definition)} chars)", "cyan")
                        # Prepend the definition to the file for next attempt
                        prepend = f"\n-- Auto-generated by AutoLean (missing from mathlib)\n{definition}\n\n"
                        current = self.project.read_file(file_path)
                        # Insert after imports but before theorems
                        import_end = 0
                        for i, line in enumerate(current.split("\n")):
                            if line.strip().startswith("import ") or line.strip().startswith("open "):
                                import_end = i + 1
                        lines_list = current.split("\n")
                        lines_list.insert(import_end, prepend)
                        self.project.write_file(file_path, "\n".join(lines_list))
                        log.info("Auto-defined %s in %s", gap.name, file_path.name)
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

        fail_record = ExperimentRecord(
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

        # Collect failed attempt for DPO training data
        self.collector.record_attempt(fail_record, proof)

        return fail_record

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

    # -- Final report -------------------------------------------------------

    def _print_final_report(
        self,
        remaining_targets: list[SorryTarget],
        session_start: float,
    ) -> None:
        """Print a comprehensive end-of-session report with proper Rich tables."""
        import logging
        from rich.columns import Columns
        from rich.panel import Panel
        from rich.table import Table

        log = logging.getLogger("autolean")
        elapsed = time.monotonic() - session_start
        proved = [r for r in self.tracker.records if r.outcome == Outcome.SUCCESS]
        total_tokens = sum(r.llm_tokens for r in self.tracker.records)
        remaining = [
            t for t in remaining_targets
            if self._attempts.get(t.id, 0) < self.config.max_retries_per_sorry
        ]
        exhausted = [
            t for t in remaining_targets
            if self._attempts.get(t.id, 0) >= self.config.max_retries_per_sorry
        ]

        # ── Header panel ──
        console.print()
        header_lines = [
            f"[bold]Session Complete[/bold]",
            f"Duration:  {elapsed / 60:.1f} min ({elapsed / 3600:.1f} hr)",
            f"Cycles:    {self.tracker.cycle}",
            f"Proved:    [bold green]{len(proved)}[/bold green] / {self._initial_sorry_count}",
            f"Remaining: [yellow]{len(remaining)}[/yellow]  Exhausted: [red]{len(exhausted)}[/red]",
            f"Tokens:    {total_tokens:,}",
        ]
        console.print(Panel(
            "\n".join(header_lines),
            title="AutoLean Report",
            border_style="cyan",
            width=70,
        ))

        # ── Metrics tables (from tracker) ──
        self.tracker.print_summary(initial_count=self._initial_sorry_count)

        # ── Proved theorems table ──
        if proved:
            proved_table = Table(
                title=f"Proved Theorems ({len(proved)})",
                show_header=True,
                header_style="bold green",
                min_width=70,
            )
            proved_table.add_column("Theorem", style="green", min_width=28)
            proved_table.add_column("File", style="dim", min_width=20)
            proved_table.add_column("Att", justify="right", min_width=4)
            proved_table.add_column("Time", justify="right", min_width=8)
            proved_table.add_column("Tokens", justify="right", min_width=8)

            for r in proved:
                proved_table.add_row(
                    r.decl_name,
                    f"{r.file}:{r.line}",
                    str(r.attempt),
                    f"{r.duration_seconds:.1f}s",
                    f"{r.llm_tokens:,}",
                )
            console.print(proved_table)

        # ── Remaining targets table ──
        if remaining:
            rem_table = Table(
                title=f"Remaining ({len(remaining)})",
                show_header=True,
                header_style="bold yellow",
                min_width=70,
            )
            rem_table.add_column("Target", style="yellow", min_width=28)
            rem_table.add_column("File", style="dim", min_width=20)
            rem_table.add_column("Attempts", justify="right", min_width=10)

            for t in remaining[:20]:
                attempts = self._attempts.get(t.id, 0)
                rem_table.add_row(
                    t.decl_name,
                    f"{t.rel_path or t.file.name}:{t.line}",
                    f"{attempts}/{self.config.max_retries_per_sorry}",
                )
            if len(remaining) > 20:
                rem_table.add_row(f"... +{len(remaining) - 20} more", "", "")
            console.print(rem_table)

        # ── Exhausted targets table ──
        if exhausted:
            exh_table = Table(
                title=f"Exhausted Retries ({len(exhausted)})",
                show_header=True,
                header_style="bold red",
                min_width=70,
            )
            exh_table.add_column("Target", style="red", min_width=28)
            exh_table.add_column("Last Error", style="dim", min_width=30)

            for t in exhausted[:15]:
                last_err = self._last_error.get(t.id)
                err_text = last_err[0].value if last_err else "unknown"
                exh_table.add_row(t.decl_name, err_text)
            if len(exhausted) > 15:
                exh_table.add_row(f"... +{len(exhausted) - 15} more", "")
            console.print(exh_table)

        # ── Timing panel ──
        timing_lines = [
            f"Wall time:      {elapsed / 60:.1f} min ({elapsed / 3600:.1f} hr)",
        ]
        if proved:
            avg_time = sum(r.duration_seconds for r in proved) / len(proved)
            rate = len(proved) / (elapsed / 3600) if elapsed > 0 else 0
            timing_lines.append(f"Avg proof time: {avg_time:.1f}s")
            timing_lines.append(f"Proof rate:     {rate:.1f}/hr")
        timing_lines.append(f"Total tokens:   {total_tokens:,}")
        if total_tokens > 0 and proved:
            timing_lines.append(f"Tokens/proof:   {total_tokens / len(proved):,.0f}")

        console.print(Panel(
            "\n".join(timing_lines),
            title="Timing",
            border_style="dim",
            width=70,
        ))

        # ── Files & next steps ──
        files_lines = [
            f"Results TSV:  {self.tracker.results_file}",
        ]
        log_file = self.project.root / "overnight.log"
        if log_file.exists():
            files_lines.append(f"Session log:  {log_file}")
        # Find structured log
        log_dir = self.project.root / "logs"
        if log_dir.exists():
            latest = sorted(log_dir.glob("autolean_*.log"))
            if latest:
                files_lines.append(f"Audit log:    {latest[-1]}")

        if remaining or exhausted:
            files_lines.append("")
            files_lines.append("[bold]Next steps:[/bold]")
            if remaining:
                files_lines.append("  uv run autolean run --resume")
            files_lines.append("  uv run autolean diff")
            files_lines.append("  uv run autolean results")
            if exhausted:
                files_lines.append("  uv run autolean run --model deepseek-prover --resume")
        else:
            files_lines.append("")
            files_lines.append("[bold green]All sorry targets resolved![/bold green]")

        console.print(Panel(
            "\n".join(files_lines),
            title="Files & Next Steps",
            border_style="dim",
            width=70,
        ))

        # ── Export training data + fine-tuning trigger ──
        stats = self.collector.stats()
        if stats["total_examples"] > 0:
            exported = self.collector.export_all()
            data_lines = [
                f"[bold]Training Data Collected[/bold]",
                f"Total examples:    {stats['total_examples']}",
                f"Positive (SFT):    {stats['positive']}",
                f"Negative (DPO):    {stats['negative']}",
                f"Unique theorems:   {stats['unique_theorems']}",
            ]
            for fmt, path in (exported or {}).items():
                data_lines.append(f"  {fmt}: {path}")

            # Auto fine-tuning trigger
            if self.collector.should_finetune(threshold=50):
                data_lines.append("")
                data_lines.append("[bold magenta]Fine-tuning ready![/bold magenta] 50+ proof examples collected.")
                data_lines.append("  uv run autolean finetune-config")
                data_lines.append("  accelerate launch -m axolotl.cli.train ...")
                data_lines.append("  ollama create autolean-v1 -f Modelfile")
                data_lines.append("  uv run autolean run --model autolean-v1")
            elif stats["positive"] > 0:
                data_lines.append(f"  ({50 - stats['positive']} more proofs until fine-tuning trigger)")

            console.print(Panel(
                "\n".join(data_lines),
                title="Self-Improving Loop",
                border_style="magenta",
                width=70,
            ))

        # ── Log the final summary to structured log ──
        log.info(
            "SESSION COMPLETE: proved=%d/%d (%.1f%%) cycles=%d time=%.1fm tokens=%d training_examples=%d",
            len(proved), self._initial_sorry_count,
            len(proved) / self._initial_sorry_count * 100 if self._initial_sorry_count else 0,
            self.tracker.cycle, elapsed / 60, total_tokens, stats.get("total_examples", 0),
        )
        for r in proved:
            log.info("  PROVED: %s (attempt %d, %.1fs)", r.decl_name, r.attempt, r.duration_seconds)

        console.print()
