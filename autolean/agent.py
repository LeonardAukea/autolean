"""Autonomous Lean 4 proof search with sandboxed candidate acceptance."""

from __future__ import annotations

import logging
import re
import signal
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from rich.panel import Panel
from rich.text import Text

from autolean import ui
from autolean.error_classifier import (
    STRUCTURAL_ERRORS,
    ErrorCategory,
    classify_error,
    retry_hint_for,
)
from autolean.generated_code import (
    GeneratedCodeError,
    validate_generated_closed_declarations,
    validate_generated_proof,
)
from autolean.lean_interface import (
    COMPOUND_TACTICS,
    FAST_TACTICS,
    STANDARD_TACTICS,
    BuildResult,
    Diagnostic,
    LeanProject,
)
from autolean.llm import (
    LLMAuthenticationError,
    LLMBackend,
    LLMError,
    LLMRateLimitError,
    LLMTransientError,
    create_llm_client,
)
from autolean.program import parse_program
from autolean.prompts import LEAN_TACTICS, SORRY_FILL_USER, SYSTEM_PROMPT
from autolean.proof_loop import (
    EscalationDecision,
    EscalationRouter,
    ModelTransition,
    ProofContextBuilder,
    ProofContextError,
)
from autolean.provenance import ProofEnvironmentError, sha256_text
from autolean.scanner import (
    SorryTarget,
    count_sorries,
    difficulty_score,
    prioritize_targets,
    scan_project,
)
from autolean.structure import LeanStructureProvider
from autolean.tracker import FAILURE_OUTCOMES, ExperimentRecord, ExperimentTracker, GitError, Outcome
from autolean.ui import GLYPH_FAIL, GLYPH_OK, GLYPH_SKIP, console

log = logging.getLogger("autolean")

# Temperature escalation per retry attempt (capped at 1.0)
TEMP_ESCALATION_STEP = 0.1
TEMP_MAX = 1.0

# Skip a target after this many consecutive failures in one error category.
MAX_REPEATED_ERRORS = 3
#: Rejected candidates carried into the next epoch. The prompt shows the
#: most recent few; the rest bound what one overnight run accumulates.
EPOCH_CANDIDATE_MEMORY = 10
MAX_REDUNDANT_TAIL_REPAIRS = 3


def _has_redundant_tail(build: BuildResult) -> bool:
    """Return whether Lean rejected only a tactic after all goals closed."""
    errors = build.errors
    return len(errors) == 1 and "No goals to be solved" in errors[0].message


def _locate_in_candidate(errors: list[Diagnostic], proof: str, first_line: int) -> str:
    """Describe rejections by their position inside the candidate.

    Lean reports a line in the file; the model only ever saw the block it
    wrote. Naming and quoting the line it wrote is what lets the next
    attempt change that line instead of rewriting everything around it.
    """
    lines = proof.split("\n")
    described: list[str] = []
    for diagnostic in errors[:3]:
        index = diagnostic.line - first_line
        if 0 <= index < len(lines) and lines[index].strip():
            described.append(
                f"line {index + 1} of your proof — `{lines[index].strip()}`:\n{diagnostic.message}"
            )
        else:
            described.append(diagnostic.message)
    return "\n\n".join(described)[:800]


def _failure_outcome(build: BuildResult) -> Outcome:
    """Name the failure Lean actually reported.

    A run that exhausted its budget produced no diagnostics, so treating a
    silent result as a surviving `sorry` would record a verdict the kernel
    never gave.
    """
    if build.timed_out:
        return Outcome.FAIL_TIMEOUT
    return Outcome.FAIL_BUILD if build.errors else Outcome.FAIL_SORRY_REMAINS


@dataclass(frozen=True)
class AgentRunResult:
    """Terminal status for one agent invocation."""

    successful: bool
    message: str = ""


# ---------------------------------------------------------------------------
# Proof cleaner
# ---------------------------------------------------------------------------


_WRAPPED_PROOF = re.compile(
    r"^\s*(?:theorem|lemma|example)\b.*?:=\s*by\b(?P<body>.*)$",
    re.DOTALL,
)


#: Lean keywords that open a line without being tactics. `_is_tactic_line`
#: separates Lean from English, so it recognises these as well as tactics.
_LEAN_LINE_OPENERS = frozenset({"by", "do", "else", "fun", "if", "match", "return", "then", "where", "with"})


def _is_tactic_line(line: str) -> bool:
    """Check if a line looks like Lean tactic code (not English text)."""
    stripped = line.strip()
    if not stripped:
        return True  # blank lines are fine in tactic blocks
    # Check if the first token is a known tactic keyword
    first_token = stripped.split()[0].rstrip("(").rstrip("{") if stripped.split() else ""
    if first_token in LEAN_TACTICS or first_token in _LEAN_LINE_OPENERS:
        return True
    # Indented lines in a tactic block are likely continuations
    if line.startswith("  ") or line.startswith("\t"):
        return True
    # Lines starting with | or · are case arms
    if stripped.startswith("|") or stripped.startswith("·"):
        return True
    # Lines with := or => are likely tactic fragments
    return ":=" in stripped or "=>" in stripped


def _unwrap_markdown_code(text: str) -> str:
    """Remove a Markdown wrapper around one Lean completion."""
    fenced = re.search(r"```(?:lean4?|)\s*\n(.*?)```", text, re.DOTALL)
    if fenced:
        return fenced.group(1).strip()
    inline = re.fullmatch(r"`([^`\r\n]+)`", text)
    if inline:
        return inline.group(1).strip()
    text = re.sub(r"^```(?:lean4?|)\s*\n?", "", text)
    return re.sub(r"\n?```\s*$", "", text)


def _strip_blank_edges(lines: list[str]) -> list[str]:
    """Remove blank lines at both edges while preserving proof indentation."""
    start = next((index for index, line in enumerate(lines) if line.strip()), len(lines))
    end = next(
        (index for index, line in enumerate(reversed(lines)) if line.strip()),
        len(lines),
    )
    return lines[start : len(lines) - end]


def _trim_explanatory_prose(lines: list[str]) -> list[str]:
    """Keep the tactic-shaped suffix of a prose-prefixed completion."""
    if not lines or _is_tactic_line(lines[0]):
        return lines
    tactic_start = next(
        (index for index, line in enumerate(lines) if line.strip() and _is_tactic_line(line)),
        len(lines),
    )
    tactics = lines[tactic_start:]
    while tactics and not _is_tactic_line(tactics[-1]):
        tactics.pop()
    # Recognising no tactic is not evidence that the completion held none.
    # Discarding it would elaborate a proof the model did not write, or an
    # empty one, and record either as the model's attempt.
    return tactics or lines


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
    text = _unwrap_markdown_code(raw.strip())

    wrapped = _WRAPPED_PROOF.match(text)
    if wrapped:
        body = wrapped.group("body")
        text = body.lstrip("\r\n") if body.startswith(("\r", "\n")) else body.lstrip(" \t")

    lines = _trim_explanatory_prose(_strip_blank_edges(text.split("\n")))

    # Strip leading `by` only in tactic mode (sorry is already inside `by`)
    if tactic_mode and lines and lines[0].strip() == "by":
        lines = _strip_blank_edges(lines[1:])

    return "\n".join(lines)


# ---------------------------------------------------------------------------
# Agent
# ---------------------------------------------------------------------------


@dataclass
class AttemptEvidence:
    """Content identities produced by one proof attempt.

    An attempt that returns before reaching the model leaves these empty,
    which is what its record must say.
    """

    input_tokens: int = 0
    prompt_sha256: str = ""
    structural_context_sha256: str = ""
    indexed_context_sha256: str = ""
    strategy_sha256: str = ""
    strategy_response_sha256: str = ""


class AutoLeanAgent:
    """The autonomous proof agent."""

    def __init__(
        self,
        program_path: Path,
        *,
        dry_run: bool = False,
        verbose: bool = False,
        resume: bool = False,
        target_filter: str | None = None,
        target_file: Path | None = None,
        confirm_escalation: Callable[[EscalationDecision], bool] | None = None,
    ):
        self.program_path = program_path.resolve()
        self.dry_run = dry_run
        self.verbose = verbose
        self.resume = resume
        self.target_filter = target_filter  # Only process targets matching this decl_name
        self.target_file = target_file.resolve() if target_file is not None else None
        self._interrupted = False
        self._accepting = False
        self._terminal_failure: str | None = None
        self._consecutive_llm_errors = 0
        self._environment_sha256 = ""
        self._model_router = EscalationRouter(confirm_escalation)

        # Parse program.md
        self.config = parse_program(self.program_path)

        # Resolve lean project path (relative to program.md location)
        lean_root = self.program_path.parent / self.config.lean_project_path
        self.project = LeanProject(lean_root)

        # Backend chosen by program.md; overridable by the caller afterwards.
        self.llm: LLMBackend = create_llm_client(self.config.llm_config())

        # Initialize tracker
        self.tracker = ExperimentTracker(
            project_root=self.project.root,
            persist=not self.dry_run,
        )

        # Track attempts per target
        self._attempts: dict[str, int] = {}
        self._failed_proofs: dict[str, list[str]] = {}
        self._last_error: dict[str, tuple[ErrorCategory, str]] = {}

        # Consecutive equal error categories stop an unproductive target.
        self._error_history: dict[str, list[ErrorCategory]] = {}

        # Structural verdict per file, keyed by the content it was reached
        # from: (content sha256, reason or None). Targets in a file holding a
        # reason are skipped until an edit changes that content.
        self._file_health: dict[Path, tuple[str, str | None]] = {}

        # Each target's source identity stays stable until its file is edited.
        self._goal_cache: dict[str, str | None] = {}
        self._evidence = AttemptEvidence()
        # An epoch of nothing but skips would reset into the same skips.
        self._epoch_reached_lean = False
        self.structure = LeanStructureProvider()
        self.proof_context = ProofContextBuilder(self.project.root, self._step)

        # Track initial sorry count for coverage metric
        self._initial_sorry_count: int = 0

        # Target identities already accepted by a persisted session.
        self._proved_ids: set[str] = set()

        from autolean.collector import TrainingDataCollector
        from autolean.skills import SkillMemory

        self.collector = TrainingDataCollector(output_dir=self.project.root / "training_data")
        self.skill_memory = SkillMemory(
            skills_dir=self.project.root / "skills",
            persist=not self.dry_run,
        )

    def close(self) -> None:
        """Release the backend owned by this agent."""
        self.llm.close()

    @property
    def model_transitions(self) -> tuple[ModelTransition, ...]:
        """Return the authorized model switches made by this invocation."""
        return self._model_router.transitions

    def __enter__(self) -> AutoLeanAgent:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- Resume loading -----------------------------------------------------

    def _load_resume_state(self) -> None:
        """Load attempt counts and proved IDs from a previous results.tsv."""
        import csv

        if not self.tracker.results_file.exists():
            console.print("[yellow]No results.tsv found — starting fresh.[/]")
            return

        with open(self.tracker.results_file, encoding="utf-8") as f:
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

    # -- File health --------------------------------------------------------

    def _in_scope(self, target: SorryTarget) -> bool:
        """Whether this run may touch one target.

        A rescan re-reads a whole file, so the scope a run was started with
        has to be re-applied to what comes back; otherwise proving one
        declaration admits every other placeholder in its file.
        """
        if not self.target_filter:
            return True
        return self.target_filter in (target.decl_name, target.id)

    def _check_file_health(self, lean_file: Path, content: str) -> str | None:
        """Check if a file has non-sorry structural errors.

        Returns None if the file is healthy (only sorry warnings),
        or an error description if the file is structurally broken.

        This prevents wasting LLM retries on targets in corrupted files
        (e.g., imports inserted mid-file by gap-filling). Both verdicts are
        a function of the file bytes, so each content identity compiles at
        most once and an edit is examined afresh.
        """
        content_sha256 = sha256_text(content)
        remembered = self._file_health.get(lean_file)
        if remembered is not None and remembered[0] == content_sha256:
            return remembered[1]

        result = self.project.check_file(lean_file, timeout=60, untrusted=True)
        for diag in result.errors:
            cat = classify_error(diag.message)
            if cat in STRUCTURAL_ERRORS:
                reason = f"{cat.value}: {diag.message[:120]}"
                self._file_health[lean_file] = (content_sha256, reason)
                return reason
        self._file_health[lean_file] = (content_sha256, None)
        return None

    def _should_bail_repeated_error(self, target_id: str) -> bool:
        """Return whether one failure category exhausted its retry budget."""
        history = self._error_history.get(target_id, [])
        if len(history) < MAX_REPEATED_ERRORS:
            return False
        # Check if the last N errors are all the same category
        recent = history[-MAX_REPEATED_ERRORS:]
        return len(set(recent)) == 1

    def _record_error_category(self, target_id: str, category: ErrorCategory) -> None:
        """Track error category for repeated-error detection."""
        self._error_history.setdefault(target_id, []).append(category)

    def _consider_model_escalation(
        self,
        target: SorryTarget,
        record: ExperimentRecord,
    ) -> None:
        """Offer or perform one evidence-backed model switch."""
        route = self._model_router.route(
            target_id=target.id,
            outcome=record.outcome.value,
            category=record.error_category,
            policy=self.config.escalation_policy,
            current_model=self.llm.config.model,
            current_backend=self.llm.config.backend,
            difficulty=difficulty_score(target),
            after_failures=self.config.escalation_after_failures,
            explicit_target=self.config.escalation_model,
            endpoint=self.config.endpoint,
            timeout=self.config.llm_timeout_seconds,
            max_output_tokens=self.config.max_output_tokens,
            effort=self.config.effort,
            create_backend=create_llm_client,
        )
        if route.decision is not None:
            console.print(
                Panel(
                    f"Current:  {route.decision.from_model} ({route.decision.from_backend})\n"
                    f"Next:     {route.decision.to_model} ({route.decision.to_backend})\n"
                    f"Evidence: {route.decision.reason}",
                    title="Model escalation",
                    border_style="yellow",
                )
            )
        if route.notice:
            console.print(f"[yellow]{route.notice}[/]")
        if route.backend is None or route.transition is None:
            return
        assert route.decision is not None
        previous = self.llm
        self.llm = route.backend
        previous.close()
        self.config.model = route.decision.to_profile
        self.config.backend = route.decision.to_backend
        self._consecutive_llm_errors = 0
        self.proof_context.invalidate_strategy(target.id)
        console.print(
            f"[bold cyan]Model switched:[/] {route.transition.from_model} → "
            f"{route.transition.to_model}; the total attempt budget is unchanged."
        )

    # -- Signal handling ----------------------------------------------------

    def _handle_interrupt(self, signum: int, frame: object) -> None:
        del signum, frame
        if self._interrupted:
            if self._accepting:
                # Between installing a proof and recording it. Quitting here
                # leaves a kernel-checked proof in the file with nothing
                # saying where it came from, and the next scan finds no
                # `sorry` to revisit it by.
                console.print("\n[yellow]Recording the accepted proof, then stopping...[/]")
                return
            console.print("\n[red]Force quit.[/]")
            raise SystemExit(1)
        self._interrupted = True
        console.print("\n[yellow]Interrupt received. Finishing current cycle, then stopping...[/]")

    # -- Core loop ----------------------------------------------------------

    def run(self) -> AgentRunResult:
        """Main entry point — the autonomous loop."""
        from autolean.tracker import setup_logging

        for received in (signal.SIGINT, signal.SIGTERM):
            signal.signal(received, self._handle_interrupt)

        try:
            self.config.validate()
        except ValueError as e:
            message = f"Invalid agent configuration: {e}"
            console.print(f"[red]{message}[/]")
            return AgentRunResult(False, message)

        try:
            environment = self.project.proof_environment()
        except (OSError, ProofEnvironmentError) as e:
            message = f"Proof environment identification failed: {e}"
            console.print(f"[red]{message}[/]")
            return AgentRunResult(False, message)
        self._environment_sha256 = environment.sha256

        if not self.dry_run:
            setup_logging(self.project.root / "logs", verbose=self.verbose)
        llm_cfg = self.llm.config
        log.info(
            "Config: model=%s backend=%s retries=%d timeout=%ds",
            llm_cfg.model,
            llm_cfg.backend,
            self.config.max_retries_per_sorry,
            self.config.cycle_timeout_seconds,
        )

        console.print(
            Panel(
                f"[bold]AutoLean Agent[/]\n"
                f"Mode:             {self.config.mode}\n"
                f"Model:            {llm_cfg.model}  [dim]({llm_cfg.backend})[/dim]\n"
                f"Project:          {self.project.root}\n"
                f"Environment:      sha256:{environment.sha256[:16]}\n"
                f"Max retries:      {self.config.max_retries_per_sorry}\n"
                f"Max cycles:       {self.config.max_cycles or '∞'}\n"
                f"Self-correction:  [green]ON[/green] (error-informed retries)\n"
                f"Data collection:  [green]ON[/green] (SFT + DPO for fine-tuning)\n"
                f"Skill learning:   [green]ON[/green] ({len(self.skill_memory.skills)} skills loaded)",
                title="Starting",
                border_style="green",
            )
        )

        try:
            with ui.status(f"Preflighting {llm_cfg.backend}..."):
                connected = self.llm.ping()
        except LLMError as e:
            message = f"Backend preflight failed: {e}"
            console.print(f"[red]{message}[/]")
            return AgentRunResult(False, message)
        if not connected:
            message = f"Backend '{llm_cfg.backend}' did not pass preflight for model '{llm_cfg.model}'."
            console.print(f"[red]{message}[/]")
            console.print("  Run [cyan]autolean models[/] to see what is ready.")
            return AgentRunResult(False, message)

        console.print("[green]Backend preflight passed.[/]")

        # Setup git branch
        if not self.dry_run:
            try:
                self.tracker.setup_branch()
            except GitError as e:
                message = f"Git branch setup failed: {e}"
                console.print(f"[red]{message}[/]")
                return AgentRunResult(False, message)

        # Rescan only a file whose accepted source changes.
        targets = scan_project(self.project.root)
        targets = prioritize_targets(targets)

        # Apply target filter (from `prove` command or --target flag)
        if self.target_filter:
            targets = [t for t in targets if self._in_scope(t)]
            console.print(
                f"\n[cyan]Target filter:[/] '{self.target_filter}' — {len(targets)} matching target(s)."
            )
        if self.target_file is not None:
            targets = [target for target in targets if target.file.resolve() == self.target_file]
            console.print(f"\n[cyan]Target file:[/] {self.target_file.name} — {len(targets)} target(s).")

        # Ahead of any recorded work: the pre-search below writes result rows,
        # and they continue an earlier run's numbering rather than restart it.
        if self.resume:
            self._load_resume_state()
            proved_ids = {tid for tid, attempts in self._attempts.items() if tid in self._proved_ids}
            targets = [t for t in targets if t.id not in proved_ids]
            console.print(
                f"[cyan]Resumed:[/] {len(proved_ids)} already proved, "
                f"{len(targets)} remaining, cycle {self.tracker.cycle}"
            )

        self._initial_sorry_count = len(targets)
        console.print(f"\n[bold]Found {len(targets)} sorry target(s).[/]")

        if not targets:
            console.print("[green]No sorries found — nothing to do![/]")
            return AgentRunResult(True)

        # Every target source is elaborated inside the generated-code sandbox.
        # Direct Lean invocation writes compiler artifacts only to scratch.
        target_files = sorted({target.file.resolve() for target in targets})
        ui.phase("Initial sandboxed Lean check")
        for target_file in target_files:
            with ui.status(f"Checking {target_file.name}..."):
                build = self.project.check_file(
                    target_file,
                    timeout=self.config.cycle_timeout_seconds,
                    untrusted=True,
                )
            if not build.success:
                detail = build.stderr or (str(build.errors[0]) if build.errors else "unknown error")
                console.print(f"[red]Sandboxed Lean check failed for {target_file.name}:[/] {detail[:300]}")
                return AgentRunResult(
                    False,
                    f"Sandboxed Lean check failed for {target_file.name}: {detail[:300]}",
                )
        console.print(f"[green]{len(target_files)} target file(s) checked.[/]")

        for t in targets[:10]:
            mode_label = "tactic" if t.tactic_mode else "term"
            console.print(f"  • {t} [{mode_label}]")
        if len(targets) > 10:
            console.print(f"  ... and {len(targets) - 10} more")

        # -- Deterministic tactic pre-search --------------------------------
        # Standard tactics run before any model request; trivial goals close
        # here. Fast tactics precede the more expensive compound tactics.
        extra_standard = [t for t in STANDARD_TACTICS if t not in FAST_TACTICS]
        all_presearch = [*FAST_TACTICS, *extra_standard, *COMPOUND_TACTICS]
        if self.dry_run:
            # Pre-search keeps whatever it proves, which a dry run must not do.
            console.print("\n[yellow]DRY RUN — skipping tactic pre-search.[/]")
            all_presearch = []
        else:
            ui.phase(
                f"Tactic pre-search ({len(all_presearch)} tactics: "
                f"{len(FAST_TACTICS)} fast + {len(extra_standard)} standard + "
                f"{len(COMPOUND_TACTICS)} compound)"
            )
        presearch_proved = 0
        presearch_targets = (
            [target for target in targets if target.qualified_decl_name] if all_presearch else []
        )
        for t in presearch_targets:
            if self._interrupted:
                break
            self._step(f"Trying {len(all_presearch)} tactics on {t.decl_name}...")
            tactic = self.project.try_tactics_fast(
                t.file,
                t.line,
                t.col,
                tactics=all_presearch,
                timeout_per_tactic=min(self.config.cycle_timeout_seconds, 15),
            )
            if tactic:
                # Apply the winning tactic permanently
                original = self.project.read_file(t.file)
                new_content = self.project.replace_sorry_at(
                    t.file,
                    t.line,
                    tactic,
                    original_content=original,
                    col=t.col,
                )
                audit = self.project.validate_candidate(
                    t.file,
                    new_content,
                    timeout=self.config.cycle_timeout_seconds,
                    declaration=t.qualified_decl_name,
                    declaration_line=t.line,
                    expected_environment=self._environment_sha256,
                )
                if not audit.success:
                    detail = audit.stderr or (
                        audit.errors[0].message if audit.errors else "axiom audit failed"
                    )
                    self._step(f"Tactic proof rejected: {detail[:160]}", "red")
                    continue
                # Read the goal while the placeholder is still in the file: a
                # training example carrying a proof and no goal teaches an
                # answer to an unstated question.
                goal_state = self._goal_cache.get(t.id) or self.project.get_goal_via_hole_punch(
                    t.file,
                    t.line,
                    t.col,
                    timeout=self.config.cycle_timeout_seconds,
                )
                # Record as success
                cycle = self.tracker.next_cycle()
                record = ExperimentRecord(
                    cycle=cycle,
                    timestamp=datetime.now(UTC).isoformat(),
                    target_id=t.id,
                    decl_name=t.decl_name,
                    file=str(t.file.relative_to(self.project.root)),
                    line=t.line,
                    outcome=Outcome.SUCCESS,
                    attempt=0,  # 0 = tactic search, not LLM
                    duration_seconds=0.0,
                    llm_tokens=0,
                    llm_tok_per_sec=0.0,
                    proof_length=len(tactic.splitlines()),
                    environment_sha256=self._environment_sha256,
                    proof_sha256=sha256_text(tactic),
                    axioms=",".join(audit.axioms) if audit.axioms else "none",
                    model="deterministic-tactic-search",
                    backend="lean",
                )
                self._accepting = True
                try:
                    self.project.write_file(
                        t.file,
                        new_content,
                        expected_content=original,
                    )
                    self.tracker.commit_success(record)
                except (GitError, OSError) as e:
                    self._accepting = False
                    try:
                        self.project.write_file(
                            t.file,
                            original,
                            expected_content=new_content,
                        )
                    except OSError as restore_error:
                        message = f"Could not accept tactic proof: {e}; rollback stopped: {restore_error}"
                        console.print(f"[red]{message}[/]")
                        return AgentRunResult(False, message)
                    message = f"Could not accept tactic proof: {e}"
                    console.print(f"[red]{message}[/]")
                    return AgentRunResult(False, message)
                presearch_proved += 1
                self.tracker.log(record)
                self._accepting = False
                console.print(
                    f"  [bold green]PROVED (tactic search):[/bold green] "
                    f"[green]{t.decl_name}[/green] — [cyan]{tactic}[/cyan]"
                )

                self.collector.set_context(t.id, goal_state or "", t.context_before)
                self.collector.record_attempt(record, tactic)
                self.skill_memory.learn_from_proof(
                    theorem_name=t.decl_name,
                    theorem_statement=t.context_before[:200],
                    proof=tactic,
                )

                # Remove from targets
                targets = [x for x in targets if x.id != t.id]

        if presearch_proved:
            console.print(f"\n[green]Tactic pre-search proved {presearch_proved} target(s).[/green]")
            # Rescan affected files
            affected_files = {t.file for t in presearch_targets if t.id not in {x.id for x in targets}}
            for f in affected_files:
                targets = [t for t in targets if t.file != f]
                from autolean.scanner import scan_file

                new_targets = scan_file(f, project_root=self.project.root)
                targets.extend(t for t in new_targets if self._in_scope(t))
            targets = prioritize_targets(targets)
            self._initial_sorry_count = len(targets) + presearch_proved
        else:
            console.print("[dim]  No targets solved by standard tactics.[/dim]")

        if not targets:
            console.print("[green]All targets solved by tactic pre-search![/]")
            self._print_final_report(targets, time.monotonic())
            return AgentRunResult(True)

        # Restore target state from a persisted session.
        # -- Main loop ------------------------------------------------------
        ui.phase("Autonomous loop")
        session_start = time.monotonic()

        run_cycles = 0
        while not self._interrupted:
            # The cycle budget belongs to this invocation. The tracker keeps a
            # monotonic experiment number across resumed runs.
            if self.config.max_cycles > 0 and run_cycles >= self.config.max_cycles:
                console.print(f"\n[yellow]Reached max_cycles ({self.config.max_cycles}). Stopping.[/]")
                break
            cycle = self.tracker.next_cycle()
            run_cycles += 1

            # Retry state filters the stable target set for this epoch.
            active_targets = [
                t for t in targets if self._attempts.get(t.id, 0) < self.config.max_retries_per_sorry
            ]

            if not active_targets:
                if self.config.max_cycles == 0 and targets:
                    # Overnight mode resets bounded target histories for a new
                    # experiment epoch. Backend sampling policy stays stable.
                    if not self._epoch_reached_lean:
                        console.print(
                            "\n[yellow]Every target in the last epoch was skipped before "
                            "reaching Lean. Another pass would repeat it.[/]"
                        )
                        break
                    self._epoch_reached_lean = False
                    epoch = max(self._attempts.values()) // self.config.max_retries_per_sorry + 1
                    console.print(
                        f"\n[cyan]Epoch {epoch}:[/] All retries exhausted. "
                        f"Resetting {len(targets)} targets for another pass..."
                    )
                    for t in targets:
                        # A new epoch renews the budget: the retry count and
                        # the repeated-error bail-out that spends it. The
                        # rejected candidates and the last diagnostic stay —
                        # dropping them sends the first epoch's prompt again,
                        # and Lean rejects the same proof again.
                        self._attempts[t.id] = 0
                        self._error_history.pop(t.id, None)
                        self._goal_cache.pop(t.id, None)
                        rejected = self._failed_proofs.get(t.id)
                        if rejected:
                            del rejected[:-EPOCH_CANDIDATE_MEMORY]
                    continue
                console.print("\n[green]All targets either proved or exhausted retries. Done![/]")
                break

            target = active_targets[0]
            attempt_num = self._attempts.get(target.id, 0) + 1
            self._attempts[target.id] = attempt_num

            console.rule(
                f"Cycle {cycle} | {target.decl_name} | "
                f"attempt {attempt_num}/{self.config.max_retries_per_sorry}"
            )

            record = self._try_fill_sorry(cycle, target, attempt_num)
            if record.outcome is Outcome.FAIL_PROVIDER:
                # The request never reached Lean, so this target's budget was
                # not spent on a proof. Consecutive provider failures still
                # end the run, so refunding cannot spin.
                self._attempts[target.id] = attempt_num - 1
            elif record.outcome is not Outcome.SKIPPED:
                self._epoch_reached_lean = True
            self.tracker.log(record)
            self._accepting = False

            # Accepted proof and gap edits can shift every later source line.
            if record.outcome in (Outcome.SUCCESS, Outcome.GAP_FILLED):
                changed_file = target.file
                # Remove ALL targets from the changed file
                targets = [t for t in targets if t.file != changed_file]
                # Rescan just that file for remaining sorrys
                from autolean.scanner import scan_file

                new_targets = scan_file(changed_file, project_root=self.project.root)
                targets.extend(t for t in new_targets if self._in_scope(t))
                targets = prioritize_targets(targets)
                # Invalidate goal cache for targets in this file (lines shifted)
                stale_ids = [k for k in self._goal_cache if changed_file.name in k]
                for k in stale_ids:
                    del self._goal_cache[k]

            # -- Rich status output --
            summary = self.tracker.summary()
            proved = summary.get("success", 0)
            remaining = len(
                [t for t in targets if self._attempts.get(t.id, 0) < self.config.max_retries_per_sorry]
            )
            elapsed = time.monotonic() - session_start
            rate = proved / (elapsed / 3600) if elapsed > 0 else 0
            coverage = proved / self._initial_sorry_count * 100 if self._initial_sorry_count else 0

            # Outcome with color
            if record.outcome in (Outcome.SUCCESS, Outcome.VALIDATED):
                icon, style = GLYPH_OK, "ok"
            elif record.outcome in (Outcome.SKIPPED, Outcome.GAP_FILLED):
                icon, style = GLYPH_SKIP, "skip"
            else:
                icon, style = GLYPH_FAIL, "fail"

            console.print(
                f"  [{style}]{icon} {record.outcome.value}[/{style}]"
                f"{f' [dim]({record.error_category})[/dim]' if record.error_category else ''}"
                f" [dim]({record.duration_seconds:.1f}s, {record.llm_tokens} tok)[/dim]"
            )

            if record.error_summary and record.outcome in FAILURE_OUTCOMES:
                err_lines = record.error_summary.strip().split("\n")
                for eline in err_lines[:4]:
                    console.print(f"    [dim red]{eline[:120]}[/dim red]")
                if len(err_lines) > 4:
                    console.print(f"    [dim]... ({len(err_lines) - 4} more lines)[/dim]")

            # Progress: the bar segment is animation, so logs keep only the
            # numeric stats.
            stats = (
                f"[bold]{proved}[/bold]/{self._initial_sorry_count} "
                f"({coverage:.0f}%) | "
                f"{remaining} left | "
                f"{rate:.1f}/hr | "
                f"{elapsed / 60:.0f}m elapsed"
            )
            if console.is_terminal:
                bar_width = 30
                filled = int(coverage / 100 * bar_width) if self._initial_sorry_count else 0
                bar = "█" * filled + "░" * (bar_width - filled)
                stats = f"[{bar}] {stats}"
            console.print(f"  {stats}")
            self._consider_model_escalation(target, record)

        # -- Session complete — full report -----------------------------------
        self._print_final_report(targets, session_start)
        if self._terminal_failure is not None:
            return AgentRunResult(False, self._terminal_failure)
        if self._interrupted:
            return AgentRunResult(False, "Agent interrupted before completion.")
        return AgentRunResult(True)

    # -- Single sorry attempt -----------------------------------------------

    def _step(self, msg: str, style: str = "dim") -> None:
        """Print a timestamped agent step."""
        from datetime import datetime

        ts = datetime.now().strftime("%H:%M:%S")
        console.print(f"  [dim]{ts}[/dim] [{style}]{msg}[/{style}]")

    def _try_fill_sorry(self, cycle: int, target: SorryTarget, attempt: int) -> ExperimentRecord:
        """Try to fill a single sorry target. Returns the experiment record."""
        self._evidence = AttemptEvidence()
        t0 = time.monotonic()
        file_path = target.file
        original_content = self.project.read_file(file_path)

        if not target.qualified_decl_name:
            self._attempts[target.id] = self.config.max_retries_per_sorry
            return self._make_record(
                cycle,
                target,
                attempt,
                t0,
                outcome=Outcome.SKIPPED,
                error_summary="A named declaration is required for the axiom audit",
                error_category="axiom_audit_unavailable",
            )

        # -- Pre-flight: structural error bail-out --------------------------
        # A proof body cannot repair a structural error in its source file.
        file_issue = self._check_file_health(file_path, original_content)
        if file_issue:
            self._step(f"SKIP: file has structural error: {file_issue[:80]}", "red")
            # Exhaust retries so the loop moves on
            self._attempts[target.id] = self.config.max_retries_per_sorry
            return self._make_record(
                cycle,
                target,
                attempt,
                t0,
                outcome=Outcome.SKIPPED,
                error_summary=f"File structural error: {file_issue}",
                error_category=classify_error(file_issue).value,
            )

        # Repeated failures in one category exhaust this target early.
        if self._should_bail_repeated_error(target.id):
            history = self._error_history.get(target.id, [])
            repeated_cat = history[-1].value if history else "unknown"
            self._step(
                f"SKIP: same error ({repeated_cat}) repeated {MAX_REPEATED_ERRORS}x — bailing out",
                "red",
            )
            self._attempts[target.id] = self.config.max_retries_per_sorry
            return self._make_record(
                cycle,
                target,
                attempt,
                t0,
                outcome=Outcome.SKIPPED,
                error_summary=f"Repeated error bail-out: {repeated_cat} x{MAX_REPEATED_ERRORS}",
                error_category=repeated_cat,
            )

        # -- Step 1: Build context for LLM ----------------------------------
        self._step(f"Reading {file_path.name}:{target.line} ({target.decl_name})")
        lines = original_content.split("\n")
        start = max(0, target.decl_line - 1)
        end = min(len(lines), target.line + 20)
        file_context = "\n".join(f"{i + start + 1:4d} | {text}" for i, text in enumerate(lines[start:end]))

        structural = self.structure.inspect(
            file_path,
            original_content,
            line=target.line,
            col=target.col,
            declaration_name=target.decl_name,
        )
        structural_text = structural.render()
        self._evidence.structural_context_sha256 = structural.sha256
        file_context += f"\n\n{structural_text}"
        self._step(
            f"Structure: {structural.quality.value}, "
            f"{len(structural.referenced_declarations)} local references",
            "cyan" if structural.target is not None else "yellow",
        )

        # Extract the goal once for this exact target source.
        if target.id not in self._goal_cache:
            self._step("Extracting goal state (hole-punch: sorry -> ?_)")
            with ui.status("Extracting goal state..."):
                self._goal_cache[target.id] = self.project.get_goal_via_hole_punch(
                    file_path,
                    target.line,
                    target.col,
                    timeout=self.config.cycle_timeout_seconds,
                )
            cached_goal = self._goal_cache[target.id]
            if cached_goal:
                self._step(f"Goal: {cached_goal.replace(chr(10), ' ')[:100]}", "cyan")
            else:
                self._step("Goal state unavailable (will infer from context)", "yellow")
        else:
            self._step("Goal state cached from previous attempt")
        goal_state = self._goal_cache[target.id]

        # Store context for training data collection
        self.collector.set_context(target.id, goal_state or "", file_context)

        # Failed candidates and diagnostics guide the next request.
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

        guidance: list[str] = []
        if self.config.goals:
            goals = "\n".join(f"- {goal}" for goal in self.config.goals)
            guidance.append(f"## Program Goals\n{goals}")
        if self.config.constraints:
            constraints = "\n".join(f"- {constraint}" for constraint in self.config.constraints)
            guidance.append(f"## Program Constraints\n{constraints}")
        if guidance:
            file_context += "\n\n" + "\n\n".join(guidance)

        # Add strategy hints to context
        hints = "\n".join(f"- {h}" for h in self.config.strategy_hints)
        if hints:
            file_context += f"\n\n## Strategy Hints\n{hints}"

        # Reusable successful patterns augment the request context.
        skill_injection = self.skill_memory.get_prompt_injection(goal_state or file_context, max_skills=5)
        if skill_injection:
            n_skills = skill_injection.count("**") // 2
            self._step(f"Injecting {n_skills} learned skills into prompt", "magenta")
            file_context += f"\n\n{skill_injection}"

        try:
            enrichment = self.proof_context.build(
                target,
                goal_state or "",
                structural_quality=structural.quality.value,
                local_references=tuple(
                    declaration.qualified_name for declaration in structural.referenced_declarations
                ),
                strategy_hints=tuple(self.config.strategy_hints),
                llm_generate=self.llm.generate,
                attempt=attempt,
            )
        except ProofContextError as error:
            message = f"Model strategy failed: {error}"
            self._terminal_failure = message
            self._interrupted = True
            self._step(message, "red")
            return self._make_record(
                cycle,
                target,
                attempt,
                t0,
                outcome=Outcome.FAIL_PROVIDER,
                error_summary=message,
                error_category="strategy_generation",
            )
        self._evidence.indexed_context_sha256 = enrichment.indexed_sha256
        self._evidence.strategy_sha256 = enrichment.strategy_sha256
        self._evidence.strategy_response_sha256 = enrichment.strategy_response_sha256
        file_context += f"\n\n{enrichment.text}"

        # -- Step 2: Ask LLM -----------------------------------------------
        user_prompt = SORRY_FILL_USER.format(
            file_context=file_context,
            line=target.line,
            decl_name=target.decl_name,
            goal_state=goal_state or "(goal state unavailable -- try to infer from context)",
            failed_attempts=failed_str,
        )
        self._evidence.prompt_sha256 = sha256_text(f"{SYSTEM_PROMPT}\0{user_prompt}")

        # Sampling backends can diversify bounded retries. Deterministic and
        # reasoning profiles retain their declared request configuration.
        temp: float | None = None
        configured_temperature = self.llm.config.temperature
        if self.llm.capabilities.temperature and configured_temperature is not None:
            retry_delta = (
                (attempt - 1) * TEMP_ESCALATION_STEP if self.llm.capabilities.retry_temperature else 0.0
            )
            temp = min(configured_temperature + retry_delta, TEMP_MAX)

        model = self.llm.config.model
        knob = f" (temp={temp:.2f})" if temp is not None else ""
        self._step(f"Querying {model}{knob}")

        try:
            with ui.status(f"Waiting for {model}..."):
                response = self.llm.generate(
                    system=SYSTEM_PROMPT,
                    user=user_prompt,
                    temperature=temp,
                )
        except LLMError as e:
            self._consecutive_llm_errors += 1
            if isinstance(e, (LLMAuthenticationError, LLMRateLimitError)):
                self._interrupted = True
                provider_category = (
                    "llm_authentication" if isinstance(e, LLMAuthenticationError) else "llm_rate_limit"
                )
                self._terminal_failure = f"Provider request failed: {e}"
            elif isinstance(e, LLMTransientError):
                provider_category = "llm_transient"
                if self._consecutive_llm_errors >= MAX_REPEATED_ERRORS:
                    self._interrupted = True
                    self._terminal_failure = (
                        f"Provider remained unavailable after {MAX_REPEATED_ERRORS} attempts: {e}"
                    )
                else:
                    delay = min(2 ** (self._consecutive_llm_errors - 1), 8)
                    self._step(f"Provider unavailable; retrying after {delay}s", "yellow")
                    time.sleep(delay)
            else:
                provider_category = "llm_error"
                if self._consecutive_llm_errors >= MAX_REPEATED_ERRORS:
                    self._interrupted = True
                    self._terminal_failure = (
                        f"Provider failed for {MAX_REPEATED_ERRORS} consecutive requests: {e}"
                    )
            return self._make_record(
                cycle,
                target,
                attempt,
                t0,
                outcome=Outcome.FAIL_PROVIDER,
                error_summary=f"LLM error: {e}",
                error_category=provider_category,
            )
        self._evidence.input_tokens = response.input_tokens
        self._consecutive_llm_errors = 0

        # -- Step 3: Clean and apply proof ----------------------------------
        proof = clean_llm_proof(response.text, tactic_mode=target.tactic_mode)
        try:
            proof = validate_generated_proof(proof)
        except GeneratedCodeError as e:
            self._failed_proofs.setdefault(target.id, []).append(response.text)
            return self._make_record(
                cycle,
                target,
                attempt,
                t0,
                outcome=Outcome.FAIL_SORRY_REMAINS,
                llm_tokens=response.output_tokens,
                llm_tok_per_sec=response.tokens_per_second,
                error_summary=f"Generated proof rejected: {e}",
                proof=response.text,
                model=response.model,
            )

        # Bound generated proof size before elaboration.
        proof_lines = len(proof.splitlines())
        if proof_lines > self.config.max_proof_lines:
            self._failed_proofs.setdefault(target.id, []).append(proof)
            return self._make_record(
                cycle,
                target,
                attempt,
                t0,
                outcome=Outcome.FAIL_BUILD,
                llm_tokens=response.output_tokens,
                llm_tok_per_sec=response.tokens_per_second,
                error_summary=f"Proof too long: {proof_lines} lines > {self.config.max_proof_lines} max",
                proof_length=proof_lines,
                proof=proof,
                model=response.model,
            )

        if proof_lines <= 5 or self.verbose:
            console.print(f"  [bold]Proof[/] ({proof_lines} lines):")
            for pline in proof.splitlines()[:12]:
                console.print(Text(f"    {pline}", style="cyan"))
            if proof_lines > 12:
                console.print(f"    [dim]... ({proof_lines - 12} more lines)[/]")
        else:
            first = proof.splitlines()[0].strip()
            summary = Text("  Proof", style="bold")
            summary.append(f" ({proof_lines} lines): ")
            summary.append(first, style="cyan")
            summary.append(" ...")
            console.print(summary)

        # Construct the candidate without changing the accepted source.
        try:
            new_content = self.project.replace_sorry_at(
                file_path,
                target.line,
                proof,
                original_content=original_content,
                col=target.col,
            )
        except (OSError, ValueError) as e:
            return self._make_record(
                cycle,
                target,
                attempt,
                t0,
                outcome=Outcome.FAIL_BUILD,
                llm_tokens=response.output_tokens,
                llm_tok_per_sec=response.tokens_per_second,
                error_summary=f"Replace error: {e}",
                proof=proof,
                model=response.model,
            )

        # -- Step 4: Build and check ----------------------------------------
        self._step("Verifying with the pinned Lean kernel...")

        try:
            with ui.status("Building..."):
                build = self.project.validate_candidate(
                    file_path,
                    new_content,
                    timeout=self.config.cycle_timeout_seconds,
                    declaration=target.qualified_decl_name,
                    declaration_line=target.line,
                    expected_environment=self._environment_sha256,
                )
                repairs = 0
                while (
                    repairs < MAX_REDUNDANT_TAIL_REPAIRS
                    and len(proof.splitlines()) > 1
                    and _has_redundant_tail(build)
                ):
                    proof = "\n".join(proof.splitlines()[:-1]).rstrip()
                    proof_lines = len(proof.splitlines())
                    new_content = self.project.replace_sorry_at(
                        file_path,
                        target.line,
                        proof,
                        original_content=original_content,
                        col=target.col,
                    )
                    repairs += 1
                    build = self.project.validate_candidate(
                        file_path,
                        new_content,
                        timeout=self.config.cycle_timeout_seconds,
                        declaration=target.qualified_decl_name,
                        declaration_line=target.line,
                        expected_environment=self._environment_sha256,
                    )
                if repairs and build.success:
                    self._step(
                        f"Removed {repairs} redundant trailing tactic(s); Lean accepted the prefix",
                        "green",
                    )
        except (OSError, ValueError) as e:
            return self._make_record(
                cycle,
                target,
                attempt,
                t0,
                outcome=Outcome.FAIL_BUILD,
                llm_tokens=response.output_tokens,
                llm_tok_per_sec=response.tokens_per_second,
                error_summary=f"Candidate validation failed: {e}",
                proof=proof,
                model=response.model,
            )
        duration = time.monotonic() - t0

        # -- Step 5: Keep or revert -----------------------------------------
        # Success removes exactly the selected placeholder from the candidate.
        original_sorries = count_sorries(original_content)
        candidate_sorries = count_sorries(new_content)
        sorry_gone = build.success and candidate_sorries == original_sorries - 1
        if self.dry_run and sorry_gone:
            console.print(
                f"  [bold green]VALIDATED[/bold green] [green]{target.decl_name}[/green] "
                f"[dim]({build.duration_seconds:.1f}s sandboxed Lean; no project changes)[/dim]"
            )
            return self._make_record(
                cycle,
                target,
                attempt,
                t0,
                outcome=Outcome.VALIDATED,
                llm_tokens=response.output_tokens,
                llm_tok_per_sec=response.tokens_per_second,
                proof_length=proof_lines,
                build_duration_seconds=build.duration_seconds,
                proof=proof,
                axioms=(",".join(build.axioms) if build.axioms else "none"),
                model=response.model,
            )
        if sorry_gone:
            self._accepting = True
            try:
                self.project.write_file(
                    file_path,
                    new_content,
                    expected_content=original_content,
                )
            except OSError as e:
                self._accepting = False
                return self._make_record(
                    cycle,
                    target,
                    attempt,
                    t0,
                    outcome=Outcome.FAIL_BUILD,
                    llm_tokens=response.output_tokens,
                    llm_tok_per_sec=response.tokens_per_second,
                    error_summary=f"Could not accept validated proof: {e}",
                    proof=proof,
                    model=response.model,
                )
            console.print(
                f"  [bold green]PROVED![/bold green] "
                f"[green]{target.decl_name}[/green] "
                f"[dim]({build.duration_seconds:.1f}s build)[/dim]"
            )
            rel_path = str(file_path.relative_to(self.project.root))
            record = ExperimentRecord(
                cycle=cycle,
                timestamp=datetime.now(UTC).isoformat(),
                target_id=target.id,
                decl_name=target.decl_name,
                file=rel_path,
                line=target.line,
                outcome=Outcome.SUCCESS,
                attempt=attempt,
                duration_seconds=duration,
                llm_tokens=response.output_tokens,
                llm_tok_per_sec=response.tokens_per_second,
                proof_length=proof_lines,
                build_duration_seconds=build.duration_seconds,
                environment_sha256=self._environment_sha256,
                proof_sha256=sha256_text(proof),
                axioms=",".join(build.axioms) if build.axioms else "none",
                model=response.model,
                backend=self.llm.config.backend,
                llm_input_tokens=response.input_tokens,
                prompt_sha256=self._evidence.prompt_sha256,
                structural_context_sha256=self._evidence.structural_context_sha256,
                indexed_context_sha256=self._evidence.indexed_context_sha256,
                strategy_sha256=self._evidence.strategy_sha256,
                strategy_response_sha256=self._evidence.strategy_response_sha256,
                model_revision=self.llm.config.model_revision or "",
                sampling_seed=self.llm.config.seed,
                model_artifact_sha256=self.llm.config.model_artifact_sha256 or "",
            )
            try:
                self.tracker.commit_success(record)
            except GitError as e:
                try:
                    self.project.write_file(
                        file_path,
                        original_content,
                        expected_content=new_content,
                    )
                except OSError as restore_error:
                    e = GitError(f"{e}; rollback stopped: {restore_error}")
                return self._make_record(
                    cycle,
                    target,
                    attempt,
                    t0,
                    outcome=Outcome.FAIL_BUILD,
                    llm_tokens=response.output_tokens,
                    llm_tok_per_sec=response.tokens_per_second,
                    error_summary=f"Git commit failed: {e}",
                    proof=proof,
                    model=response.model,
                )
            self._failed_proofs.pop(target.id, None)
            self._last_error.pop(target.id, None)
            self._error_history.pop(target.id, None)

            self._step("Committing to git + collecting training data", "green")
            self.collector.record_attempt(record, proof)
            skill = self.skill_memory.learn_from_proof(
                theorem_name=target.decl_name,
                theorem_statement=target.context_before[:200],
                proof=proof,
            )
            if skill:
                self._step(f"Learned skill: {skill.name} ({skill.description[:60]})", "magenta")
            return record

        # Classify the rejected candidate for bounded retry policy.
        self._step("Build failed — analyzing error...", "red")
        error_summary = ""
        error_category = ""
        cat = ErrorCategory.OTHER
        if build.errors:
            # Classify the diagnostic Lean wrote; locating it adds the
            # candidate's own text, which must not steer the category.
            cat = classify_error(build.errors[0].message[:500])
            error_category = cat.value
            error_summary = _locate_in_candidate(build.errors, proof, target.line)
            self._last_error[target.id] = (cat, error_summary)
            self._record_error_category(target.id, cat)

            # Structural error — exhaust retries immediately (LLM can't fix)
            if cat in STRUCTURAL_ERRORS:
                self._step(
                    f"Structural error ({cat.value}) — skipping target",
                    "red",
                )
                # The diagnostic came from elaborating this candidate, not the
                # file on disk, which Lean accepted at the top of this attempt.
                # Recording it against the file would skip every sibling target
                # in source that is known good.
                self._attempts[target.id] = self.config.max_retries_per_sorry

            # Auto-detect missing definitions and try to fill gaps
            # (only for non-structural errors, and with validation)
            elif cat == ErrorCategory.UNKNOWN_IDENTIFIER and not self.dry_run:
                from autolean.library import detect_missing_definitions, fill_gap

                gaps = detect_missing_definitions(error_summary, file_context, str(file_path))
                if gaps:
                    for gap in gaps[:2]:  # max 2 gaps per attempt
                        self._step(f"Detected missing: {gap.name} — attempting to define it", "yellow")
                        try:
                            definition = fill_gap(gap, file_path, self.llm.generate)
                            if definition:
                                definition = validate_generated_closed_declarations(definition)
                        except (LLMError, GeneratedCodeError) as e:
                            self._step(f"Gap generation rejected: {e}", "red")
                            definition = None
                        if definition:
                            self._step(
                                f"Generated definition for {gap.name} ({len(definition)} chars)",
                                "cyan",
                            )
                            # Insert after imports but before theorems
                            current = self.project.read_file(file_path)
                            prepend = (
                                f"\n-- Auto-generated by AutoLean (missing from mathlib)\n{definition}\n\n"
                            )
                            import_end = 0
                            for i, line in enumerate(current.split("\n")):
                                if line.strip().startswith("import ") or line.strip().startswith("open "):
                                    import_end = i + 1
                            lines_list = current.split("\n")
                            lines_list.insert(import_end, prepend)
                            new_content_with_gap = "\n".join(lines_list)
                            declaration_line = import_end + 3
                            try:
                                check = self.project.validate_candidate(
                                    file_path,
                                    new_content_with_gap,
                                    timeout=60,
                                    declaration=gap.name,
                                    declaration_line=declaration_line,
                                    expected_environment=self._environment_sha256,
                                )
                            except OSError as e:
                                self._step(f"Gap validation failed: {e}", "red")
                                continue
                            if not check.success:
                                self._step(
                                    f"Gap definition for {gap.name} did not compile",
                                    "red",
                                )
                            else:
                                gap_record = self._make_record(
                                    cycle,
                                    target,
                                    attempt,
                                    t0,
                                    outcome=Outcome.GAP_FILLED,
                                    llm_tokens=response.output_tokens,
                                    llm_tok_per_sec=response.tokens_per_second,
                                    error_summary=(
                                        f"Added missing definition {gap.name}; proof target will be rescanned"
                                    ),
                                    error_category=error_category,
                                    proof_length=proof_lines,
                                    build_duration_seconds=check.duration_seconds,
                                    proof=definition,
                                    axioms=(",".join(check.axioms) if check.axioms else "none"),
                                    model=response.model,
                                )
                                gap_record.decl_name = gap.name
                                self._accepting = True
                                try:
                                    self.project.write_file(
                                        file_path,
                                        new_content_with_gap,
                                        expected_content=current,
                                    )
                                    self.tracker.commit_success(gap_record)
                                except (GitError, OSError) as e:
                                    try:
                                        self.project.write_file(
                                            file_path,
                                            current,
                                            expected_content=new_content_with_gap,
                                        )
                                    except OSError as restore_error:
                                        e = GitError(f"{e}; rollback stopped: {restore_error}")
                                    return self._make_record(
                                        cycle,
                                        target,
                                        attempt,
                                        t0,
                                        outcome=Outcome.FAIL_BUILD,
                                        llm_tokens=response.output_tokens,
                                        llm_tok_per_sec=response.tokens_per_second,
                                        error_summary=f"Gap acceptance failed: {e}",
                                        error_category=error_category,
                                        proof=definition,
                                        model=response.model,
                                    )
                                log.info("Auto-defined %s in %s", gap.name, file_path.name)
                                self._failed_proofs.setdefault(target.id, []).append(proof)
                                return gap_record
        elif build.timed_out:
            error_summary = build.stderr[:500] or "Build timed out"
            error_category = ErrorCategory.TIMEOUT.value
            self._record_error_category(target.id, ErrorCategory.TIMEOUT)
        elif not build.success:
            error_summary = build.stderr[:500] if build.stderr else "Build failed (unknown)"
            error_category = ErrorCategory.OTHER.value
            self._record_error_category(target.id, ErrorCategory.OTHER)
        else:
            error_summary = f"sorry still present after replacement at line {target.line}"
            error_category = ErrorCategory.SORRY_REMAINS.value
            self._record_error_category(target.id, ErrorCategory.SORRY_REMAINS)

        self._failed_proofs.setdefault(target.id, []).append(proof)

        fail_record = ExperimentRecord(
            cycle=cycle,
            timestamp=datetime.now(UTC).isoformat(),
            target_id=target.id,
            decl_name=target.decl_name,
            file=str(file_path.relative_to(self.project.root)),
            line=target.line,
            outcome=_failure_outcome(build),
            attempt=attempt,
            duration_seconds=duration,
            llm_tokens=response.output_tokens,
            llm_tok_per_sec=response.tokens_per_second,
            error_summary=error_summary,
            error_category=error_category,
            build_duration_seconds=build.duration_seconds,
            environment_sha256=self._environment_sha256,
            proof_sha256=sha256_text(proof),
            model=response.model,
            backend=self.llm.config.backend,
            llm_input_tokens=response.input_tokens,
            prompt_sha256=self._evidence.prompt_sha256,
            structural_context_sha256=self._evidence.structural_context_sha256,
            indexed_context_sha256=self._evidence.indexed_context_sha256,
            strategy_sha256=self._evidence.strategy_sha256,
            strategy_response_sha256=self._evidence.strategy_response_sha256,
            model_revision=self.llm.config.model_revision or "",
            sampling_seed=self.llm.config.seed,
            model_artifact_sha256=self.llm.config.model_artifact_sha256 or "",
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
        proof: str = "",
        axioms: str = "",
        model: str | None = None,
    ) -> ExperimentRecord:
        """Helper to create an ExperimentRecord with common fields."""
        return ExperimentRecord(
            cycle=cycle,
            timestamp=datetime.now(UTC).isoformat(),
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
            environment_sha256=self._environment_sha256,
            proof_sha256=sha256_text(proof) if proof else "",
            axioms=axioms,
            model=model or self.llm.config.model,
            backend=self.llm.config.backend,
            llm_input_tokens=self._evidence.input_tokens,
            prompt_sha256=self._evidence.prompt_sha256,
            structural_context_sha256=self._evidence.structural_context_sha256,
            indexed_context_sha256=self._evidence.indexed_context_sha256,
            strategy_sha256=self._evidence.strategy_sha256,
            strategy_response_sha256=self._evidence.strategy_response_sha256,
            model_revision=self.llm.config.model_revision or "",
            sampling_seed=self.llm.config.seed,
            model_artifact_sha256=self.llm.config.model_artifact_sha256 or "",
        )

    # -- Final report -------------------------------------------------------

    def _print_final_report(
        self,
        remaining_targets: list[SorryTarget],
        session_start: float,
    ) -> None:
        """Print the end-of-session report."""
        from rich.table import Table

        elapsed = time.monotonic() - session_start
        proved = [r for r in self.tracker.records if r.outcome == Outcome.SUCCESS]
        validated = [r for r in self.tracker.records if r.outcome == Outcome.VALIDATED]
        total_tokens = sum(r.llm_tokens for r in self.tracker.records)
        remaining = [
            t for t in remaining_targets if self._attempts.get(t.id, 0) < self.config.max_retries_per_sorry
        ]
        exhausted = [
            t for t in remaining_targets if self._attempts.get(t.id, 0) >= self.config.max_retries_per_sorry
        ]

        # ── Header panel ──
        console.print()
        header_lines = [
            "[bold]Session Complete[/bold]",
            f"Duration:  {elapsed / 60:.1f} min ({elapsed / 3600:.1f} hr)",
            f"Cycles:    {self.tracker.cycle}",
            f"Proved:    [bold green]{len(proved)}[/bold green] / {self._initial_sorry_count}",
            f"Remaining: [yellow]{len(remaining)}[/yellow]  Exhausted: [red]{len(exhausted)}[/red]",
            f"Tokens:    {total_tokens:,}",
        ]
        if self.dry_run:
            header_lines.insert(
                4,
                f"Validated: [bold green]{len(validated)}[/bold green] dry-run candidate(s)",
            )
        console.print(
            Panel(
                "\n".join(header_lines),
                title="AutoLean Report",
                border_style="cyan",
                width=70,
            )
        )

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

        console.print(
            Panel(
                "\n".join(timing_lines),
                title="Timing",
                border_style="dim",
                width=70,
            )
        )

        # ── Files & next steps ──
        if self.dry_run:
            files_lines = ["Dry run: no project files were written."]
            if validated:
                files_lines.extend(
                    (
                        "",
                        "Re-run the same command without --dry-run to accept a validated proof.",
                    )
                )
        else:
            files_lines = [f"Results TSV:  {self.tracker.results_file}"]
            log_file = self.project.root / "overnight.log"
            if log_file.exists():
                files_lines.append(f"Session log:  {log_file}")
            log_dir = self.project.root / "logs"
            if log_dir.exists():
                latest = sorted(log_dir.glob("autolean_*.log"))
                if latest:
                    files_lines.append(f"Audit log:    {latest[-1]}")

        if not self.dry_run and (remaining or exhausted):
            files_lines.append("")
            files_lines.append("[bold]Next steps:[/bold]")
            if remaining:
                files_lines.append(f"  {ui.command()} solve --resume")
            files_lines.append(f"  {ui.command()} changes")
            files_lines.append(f"  {ui.command()} results")
            if exhausted:
                files_lines.append(f"  {ui.command()} solve --model deepseek-prover --resume")
        elif not self.dry_run:
            files_lines.append("")
            files_lines.append("[bold green]All sorry targets resolved![/bold green]")

        console.print(
            Panel(
                "\n".join(files_lines),
                title="Files & Next Steps",
                border_style="dim",
                width=70,
            )
        )

        # ── Export training data + fine-tuning trigger ──
        stats = self.collector.stats()
        if not self.dry_run and stats["total_examples"] > 0:
            exported = self.collector.export_all()
            data_lines = [
                f"Total examples:    {stats['total_examples']}",
                f"Positive (SFT):    {stats['positive']}",
                f"Negative (DPO):    {stats['negative']}",
                f"Unique theorems:   {stats['unique_theorems']}",
            ]
            for fmt, path in (exported or {}).items():
                data_lines.append(f"  {fmt}: {path}")

            from autolean.finetune import (
                FINETUNE_THRESHOLD,
                check_finetune_readiness,
                trigger_local_finetune,
            )

            ft_status = check_finetune_readiness(self.project.root / "training_data")
            if ft_status.ready:
                data_lines.append("")
                data_lines.append("[bold magenta]Fine-tuning auto-triggered![/bold magenta]")
                trigger_local_finetune(self.project.root / "training_data")
            elif stats["positive"] > 0:
                until_finetune = FINETUNE_THRESHOLD - ft_status.positive_examples
                data_lines.append(f"  ({until_finetune} more proofs until auto fine-tuning)")

            console.print(
                Panel(
                    "\n".join(data_lines),
                    title="Training data",
                    border_style="note",
                    width=70,
                )
            )

        # ── Log the final summary to structured log ──
        log.info(
            "SESSION COMPLETE: proved=%d/%d (%.1f%%) cycles=%d time=%.1fm tokens=%d training_examples=%d",
            len(proved),
            self._initial_sorry_count,
            len(proved) / self._initial_sorry_count * 100 if self._initial_sorry_count else 0,
            self.tracker.cycle,
            elapsed / 60,
            total_tokens,
            stats.get("total_examples", 0),
        )
        for r in proved:
            log.info("  PROVED: %s (attempt %d, %.1fs)", r.decl_name, r.attempt, r.duration_seconds)

        console.print()
