"""Command adapters for research, maintenance, and compatibility workflows."""

from __future__ import annotations

import re
import subprocess
import textwrap
from dataclasses import dataclass
from pathlib import Path

import click
from rich.panel import Panel

from autolean import cli_runtime, ui
from autolean.challenges import OpenProblem
from autolean.files import read_source
from autolean.lean_interface import BuildResult, LeanProject
from autolean.llm import LLMBackend, LLMError
from autolean.provenance import ProofEnvironment, ProofEnvironmentError, sha256_text
from autolean.scanner import (
    _DECL_RE,
    _find_enclosing_decl_details,
    _mask_lean_noncode,
)
from autolean.ui import console
from autolean.validation import require_instance, require_int, require_text, require_texts

_accept_generated_source = cli_runtime.accept_generated_source
_agent_for = cli_runtime.agent_for
_configure_escalation = cli_runtime.configure_escalation
_connected_llm = cli_runtime.connected_llm
_run_agent = cli_runtime.run_agent
_run_session_agent = cli_runtime.run_session_agent
provider_option = cli_runtime.provider_option
escalation_options = cli_runtime.escalation_options
model_option = cli_runtime.model_option
program_option = cli_runtime.program_option


@click.group()
def extra_commands() -> None:
    """Own supplementary commands registered on the root CLI."""


# ---------------------------------------------------------------------------
# changes — show what the agent has changed
# ---------------------------------------------------------------------------


@extra_commands.command("changes", hidden=True)
@click.option(
    "--project",
    "-d",
    type=click.Path(exists=True, path_type=Path),
    default="workspace",
    help="Path to Lean project root.",
)
def diff(project: Path) -> None:
    """Show what the agent has changed (git diff of .lean files)."""
    project = project.resolve()

    try:
        result = subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "diff", "--stat", "--", "*.lean"],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise click.ClickException(f"Could not inspect Git changes: {e}") from e
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise click.ClickException(f"Git diff failed: {detail or 'no detail'}")
    if result.stdout.strip():
        console.print("[bold]Uncommitted Lean changes:[/]\n")
        console.print(result.stdout)
    else:
        console.print("[dim]No uncommitted Lean changes.[/]")

    try:
        log_result = subprocess.run(
            [
                "git",
                "-c",
                "core.fsmonitor=false",
                "log",
                "-n",
                "50",
                "--format=%s",
                "--grep=^proof: Prove ",
            ],
            cwd=project,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as e:
        raise click.ClickException(f"Could not inspect proof history: {e}") from e
    if log_result.returncode != 0:
        detail = (log_result.stderr or log_result.stdout).strip()
        raise click.ClickException(f"Git log failed: {detail or 'no detail'}")
    proved = [entry for entry in log_result.stdout.strip().split("\n") if entry]
    if proved:
        console.print(f"\n[bold]{len(proved)} recent proofs:[/]", style="ok")
        for entry in proved:
            name = entry.removeprefix("proof: Prove ")
            ui.ok(name)


# ---------------------------------------------------------------------------
# export-training — export collected training data
# ---------------------------------------------------------------------------


@extra_commands.command("export-training", hidden=True)
@click.option(
    "--project",
    "-d",
    type=click.Path(exists=True, path_type=Path),
    default="workspace",
    help="Path to Lean project root.",
)
def export_training(project: Path) -> None:
    """List the training data written by proof runs.

    \b
    The agent writes these during runs:
      - SFT JSONL: instruction pairs from accepted proofs
      - ShareGPT JSONL: the same pairs in Hermes/Axolotl chat form
      - DPO JSONL: accepted-versus-rejected preference pairs
    """
    project = project.resolve()

    td = project / "training_data"
    if not td.exists() or not any(td.glob("*.jsonl")):
        console.print("[yellow]No training data found. Run the agent first:[/]")
        console.print(f"  {ui.command()} solve --max-cycles 20")
        return

    console.print("[bold]Training data files:[/]\n")
    for f in sorted(td.glob("*.jsonl")):
        with open(f, encoding="utf-8") as handle:
            lines = sum(1 for _ in handle)
        size = f.stat().st_size / 1024
        console.print(f"  {f.name} ({lines} examples, {size:.1f} KB)")

    console.print("\n[bold]Train from these files:[/]")
    console.print("  The SFT and ShareGPT JSONL suit any chat fine-tuning framework.")
    console.print(f"  Load {td}/dpo_*.jsonl preference pairs with TRL's DPOTrainer.")


# ---------------------------------------------------------------------------
# build-library — create missing types/structures for a mathematical field
# ---------------------------------------------------------------------------


@extra_commands.command("build-library", hidden=True)
@click.argument("topic")
@click.option(
    "--output",
    "-o",
    type=click.Path(path_type=Path),
    default=None,
    help="Output path inside the configured Lean project.",
)
@model_option
@provider_option
@click.option("--prove", is_flag=True, help="Immediately attempt proofs after generating.")
@program_option
def build_library(
    topic: str,
    output: Path | None,
    model: str | None,
    backend: str | None,
    prove: bool,
    program: Path,
) -> None:
    """Build a local Lean 4 library for a mathematical topic.

    \b
    Creates definitions, structures, and basic lemmas that supplement
    mathlib for a specific domain.

    \b
    Examples:
      autolean build-library "differential geometry"
      autolean build-library "graph theory" --prove
      autolean build-library "category theory basics" -o MyLib.lean
      autolean build-library "finite automata"
      autolean build-library "tropical geometry"
    """
    from autolean.library import generate_library_source
    from autolean.program import parse_program

    cfg = parse_program(program)
    llm = _connected_llm(model, backend, cfg, timeout=600.0)

    safe_topic = re.sub(r"[^a-zA-Z0-9]", "", topic.title().replace(" ", ""))
    lean_root = program.parent / cfg.lean_project_path
    if output is None:
        output = lean_root / "AutoLean" / f"Lib{safe_topic}.lean"
    elif not output.is_absolute():
        output = lean_root / output

    console.print(f"[bold]Building library for:[/] {topic}")
    console.print(f"[bold]Output:[/] {output}\n")

    try:
        with ui.status(f"Generating {topic} library..."):
            content = generate_library_source(topic, llm.generate)
    except LLMError as e:
        raise click.ClickException(str(e)) from e
    finally:
        llm.close()
    path, _ = _accept_generated_source(lean_root, output, content, timeout=300)

    from autolean.scanner import count_sorries

    n_defs = len(re.findall(r"\b(?:def|structure|class|instance|theorem|lemma)\b", content))
    n_sorrys = count_sorries(content)

    console.print(f"[green]Generated {n_defs} definitions/theorems ({n_sorrys} sorry targets)[/]")
    console.print(f"  File: {path}\n")

    for line in content.split("\n")[:20]:
        console.print(f"  [dim]{line}[/]")
    if len(content.split("\n")) > 20:
        console.print(f"  [dim]... ({len(content.split(chr(10))) - 20} more lines)[/]")

    if prove and n_sorrys > 0:
        console.print(f"\n[bold]Attempting to prove {n_sorrys} sorry targets...[/]\n")
        agent = _agent_for(
            program,
            model=model,
            backend=backend,
            verbose=True,
            target_file=path,
        )
        agent.config.max_cycles = n_sorrys * 3
        _run_agent(agent)
    elif n_sorrys > 0:
        console.print(f"\n  Next: [cyan]{ui.command()} solve[/] to attempt {n_sorrys} proofs")


# ---------------------------------------------------------------------------
# improve — rewrite an existing proof toward a --goal
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class _ProofSlice:
    """The exact tactic-proof slice selected for improvement."""

    file_path: Path
    source: str
    lines: tuple[str, ...]
    theorem_line: int
    proof_start: int
    proof_end: int
    declaration: str
    proof: str

    def __post_init__(self) -> None:
        require_instance(self.file_path, Path, "proof slice file must be a path")
        require_text(self.source, "proof slice source must not be empty")
        require_instance(self.lines, tuple, "proof slice lines must be a tuple")
        require_texts(self.lines, "proof slice lines must be text", allow_empty=True)
        for value in (self.theorem_line, self.proof_start, self.proof_end):
            require_int(value, "proof slice positions must be non-negative", minimum=0)
        if not self.theorem_line < self.proof_start <= self.proof_end <= len(self.lines):
            raise ValueError("proof slice positions are inconsistent")
        require_text(self.declaration, "proof slice declaration must not be empty")
        require_text(self.proof, "proof slice tactic body must not be empty")


@dataclass(frozen=True)
class _ProofAudit:
    """Kernel identity and source notation required for proof replacement."""

    environment: ProofEnvironment
    qualified_name: str
    baseline: BuildResult
    indent: str

    def __post_init__(self) -> None:
        require_instance(
            self.environment,
            ProofEnvironment,
            "proof audit requires a typed environment",
        )
        require_text(self.qualified_name, "proof audit declaration must not be empty")
        require_instance(self.baseline, BuildResult, "proof audit requires a build result")
        require_text(self.indent, "proof audit indentation must be text", allow_empty=True)
        if not self.baseline.success or not self.baseline.statement_sha256:
            raise ValueError("proof audit baseline must identify an accepted statement")


@dataclass(frozen=True)
class _ImprovementCandidate:
    """One generated proof and its complete replacement source."""

    proof: str
    lines: tuple[str, ...]
    source: str

    def __post_init__(self) -> None:
        require_text(self.proof, "improved proof must not be empty")
        require_instance(self.lines, tuple, "improved proof lines must be a tuple")
        require_texts(self.lines, "improved proof lines must be text", allow_empty=True)
        require_text(self.source, "improved source must not be empty")


def _proof_slice(file_path: Path, theorem_name: str) -> _ProofSlice:
    """Locate one named multiline tactic proof in a Lean source."""
    source = read_source(file_path)
    lines = tuple(source.split("\n"))
    masked_lines = tuple(_mask_lean_noncode(source).split("\n"))
    theorem_line = _theorem_line(masked_lines, theorem_name)
    proof_start = _proof_start(masked_lines, theorem_line, theorem_name)
    proof_end = _proof_end(lines, proof_start)
    proof = "\n".join(lines[proof_start:proof_end])
    if not proof.strip():
        raise click.ClickException(f"No multiline tactic proof found for '{theorem_name}'.")
    return _ProofSlice(
        file_path=file_path,
        source=source,
        lines=lines,
        theorem_line=theorem_line,
        proof_start=proof_start,
        proof_end=proof_end,
        declaration="\n".join(lines[theorem_line:proof_start]),
        proof=proof,
    )


def _theorem_line(lines: tuple[str, ...], theorem_name: str) -> int:
    """Return the zero-indexed declaration line for one source name."""
    for index, line in enumerate(lines):
        declaration = _DECL_RE.match(line)
        if declaration is None:
            continue
        kind, parsed_name = declaration.groups()
        if kind in {"theorem", "lemma", "def"} and parsed_name == theorem_name:
            return index
    raise click.ClickException(f"No theorem named '{theorem_name}' in the selected file.")


def _proof_start(lines: tuple[str, ...], theorem_line: int, theorem_name: str) -> int:
    """Return the first tactic-body line of one declaration."""
    for index in range(theorem_line, len(lines)):
        if index > theorem_line and _DECL_RE.match(lines[index]):
            break
        marker = re.search(r"\bby\b", lines[index])
        if marker is None:
            continue
        if lines[index][marker.end() :].strip():
            raise click.ClickException(f"No multiline tactic proof found for '{theorem_name}'.")
        return index + 1
    raise click.ClickException(f"No tactic proof found for '{theorem_name}'.")


def _proof_end(lines: tuple[str, ...], proof_start: int) -> int:
    """Return the first following unindented declaration line."""
    for index in range(proof_start, len(lines)):
        stripped = lines[index].strip()
        if stripped and not stripped.startswith("--") and not lines[index].startswith((" ", "\t")):
            return index
    return len(lines)


def _lean_project_root(file_path: Path) -> Path:
    """Return the nearest ancestor containing a Lake project file."""
    candidate = file_path.parent
    while candidate != candidate.parent:
        if (candidate / "lakefile.lean").exists() or (candidate / "lakefile.toml").exists():
            return candidate
        candidate = candidate.parent
    return file_path.parent


def _audit_proof(project: LeanProject, selected: _ProofSlice, theorem_name: str) -> _ProofAudit:
    """Bind the selected source slice to its kernel statement and environment."""
    try:
        environment = project.proof_environment(refresh=True)
    except (OSError, ProofEnvironmentError) as error:
        raise click.ClickException(f"Proof environment identification failed: {error}") from error

    local_name, qualified_name, _ = _find_enclosing_decl_details(
        _mask_lean_noncode(selected.source).split("\n"),
        selected.theorem_line + 1,
    )
    if local_name != theorem_name or not qualified_name:
        raise click.ClickException(f"Theorem '{theorem_name}' has no auditable source name.")
    proof_indents = [
        len(line) - len(line.lstrip())
        for line in selected.lines[selected.proof_start : selected.proof_end]
        if line.strip()
    ]
    if not proof_indents:
        raise click.ClickException(f"No multiline tactic proof found for '{theorem_name}'.")
    with ui.status("Auditing the current statement..."):
        baseline = project.validate_candidate(
            selected.file_path,
            selected.source,
            timeout=120,
            declaration=qualified_name,
            declaration_line=selected.theorem_line + 1,
            expected_environment=environment.sha256,
        )
    if not baseline.success or not baseline.statement_sha256:
        detail = baseline.stderr or (str(baseline.errors[0]) if baseline.errors else "unknown error")
        raise click.ClickException(f"'{theorem_name}' does not currently compile: {detail[:300]}")
    return _ProofAudit(environment, qualified_name, baseline, " " * min(proof_indents))


_IMPROVEMENT_GOALS = {
    "shorter": "Make this proof as short as possible. Minimize the tactics and lines.",
    "elegant": "Make this proof mathematically elegant with idiomatic Lean 4.",
    "faster": "Make this proof fast for the Lean kernel. Use targeted tactics on large goals.",
    "readable": "Make this proof readable with clear names and structure.",
}


def _improvement_system(goal: str) -> str:
    """Return the closed system instruction for one improvement objective."""
    return (
        "You are a Lean 4 proof improvement expert. "
        f"{_IMPROVEMENT_GOALS[goal]} "
        "Output only the improved tactic block."
    )


def _generate_improvement(
    llm: LLMBackend,
    selected: _ProofSlice,
    theorem_name: str,
    system: str,
    indent: str,
) -> _ImprovementCandidate | None:
    """Generate and normalize one distinct bounded proof candidate."""
    from autolean.agent import clean_llm_proof
    from autolean.generated_code import GeneratedCodeError, validate_generated_proof
    from autolean.prompts import PROOF_GOLF_USER

    user_prompt = PROOF_GOLF_USER.format(
        file_context=f"{selected.declaration}\n{selected.proof}",
        decl_name=theorem_name,
        line=selected.theorem_line + 1,
        current_proof=selected.proof,
    )
    try:
        with ui.status("Generating improved proof..."):
            response = llm.generate(system, user_prompt)
    except LLMError as error:
        raise click.ClickException(f"Proof improvement failed: {error}") from error
    proof = clean_llm_proof(response.text, tactic_mode=True)
    try:
        proof = validate_generated_proof(proof)
    except GeneratedCodeError as error:
        console.print(f"  [red]Generated proof rejected:[/] {error}")
        return None
    if textwrap.dedent(proof).strip() == textwrap.dedent(selected.proof).strip():
        console.print("  [yellow]No improvement generated.[/]")
        return None
    console.print("  [cyan]New proof:[/]")
    for line in proof.split("\n")[:8]:
        console.print(f"    [cyan]{line}[/]")
    normalized = tuple(textwrap.dedent(proof).strip("\n").split("\n"))
    replacement = tuple(f"{indent}{line}" if line else "" for line in normalized)
    source_lines = list(selected.lines)
    source_lines[selected.proof_start : selected.proof_end] = replacement
    return _ImprovementCandidate(proof, normalized, "\n".join(source_lines))


def _validate_improvement(
    project: LeanProject,
    selected: _ProofSlice,
    audit: _ProofAudit,
    candidate: _ImprovementCandidate,
) -> BuildResult:
    """Check one candidate against the exact statement and environment."""
    assert audit.baseline.statement_sha256 is not None
    with ui.status("Verifying proof and axioms..."):
        return project.validate_candidate(
            selected.file_path,
            candidate.source,
            timeout=120,
            declaration=audit.qualified_name,
            declaration_line=selected.theorem_line + 1,
            expected_environment=audit.environment.sha256,
            expected_statement=audit.baseline.statement_sha256,
        )


def _accept_improvement(
    project: LeanProject,
    selected: _ProofSlice,
    audit: _ProofAudit,
    candidate: _ImprovementCandidate,
    build: BuildResult,
) -> None:
    """Atomically install and report one kernel-accepted improvement."""
    try:
        project.write_file(
            selected.file_path,
            candidate.source,
            expected_content=selected.source,
        )
    except OSError as error:
        raise click.ClickException(f"Source changed during proof improvement: {error}") from error
    old_length = len(selected.proof.strip().split("\n"))
    new_length = len(candidate.lines)
    axioms = ", ".join(build.axioms) if build.axioms else "none"
    console.print(f"  [bold green]Improved![/] {old_length} lines -> {new_length} lines")
    console.print(f"  Environment: sha256:{audit.environment.sha256}")
    console.print(f"  Proof:       sha256:{sha256_text(candidate.proof)}")
    console.print(f"  Axioms:      {axioms}")
    if new_length < old_length:
        reduction = (old_length - new_length) / old_length * 100
        console.print(f"  [green]Reduced by {old_length - new_length} lines ({reduction:.0f}%)[/]")


@extra_commands.command(hidden=True)
@click.argument("file_path", type=click.Path(exists=True, path_type=Path))
@click.argument("theorem_name")
@click.option(
    "--goal",
    type=click.Choice(["shorter", "elegant", "faster", "readable"]),
    default="elegant",
    help="What to optimize for.",
)
@model_option
@provider_option
@click.option(
    "--max-attempts",
    type=click.IntRange(min=1),
    default=5,
    help="Max improvement attempts.",
)
@program_option
def improve(
    file_path: Path,
    theorem_name: str,
    goal: str,
    model: str | None,
    backend: str | None,
    max_attempts: int,
    program: Path,
) -> None:
    """Rewrite an existing proof toward the selected --goal.

    \b
    Reads the named theorem's proof, requests a rewrite, verifies the
    new version compiles, and replaces the original.

    \b
    Examples:
      autolean improve workspace/AutoLean/Medium.lean medium_add_comm
      autolean improve workspace/AutoLean/Medium.lean medium_add_comm --goal shorter
      autolean improve my_project/Foo.lean my_theorem --goal elegant
    """
    from autolean.program import parse_program

    cfg = parse_program(program)
    file_path = file_path.resolve()
    selected = _proof_slice(file_path, theorem_name)
    console.print(f"[bold]Improving:[/] {theorem_name}")
    console.print(f"[bold]Goal:[/] {goal}")
    console.print("[bold]Current proof:[/]")
    for line in selected.proof.split("\n")[:10]:
        console.print(f"  [dim]{line}[/]")
    console.print()
    project = LeanProject(_lean_project_root(file_path))
    audit = _audit_proof(project, selected, theorem_name)
    system = _improvement_system(goal)
    with _connected_llm(model, backend, cfg) as llm:
        for attempt in range(1, max_attempts + 1):
            console.print(f"[bold]Attempt {attempt}/{max_attempts}...[/]")
            candidate = _generate_improvement(
                llm,
                selected,
                theorem_name,
                system,
                audit.indent,
            )
            if candidate is None:
                continue
            build = _validate_improvement(project, selected, audit, candidate)
            if build.success:
                _accept_improvement(project, selected, audit, candidate, build)
                return
            detail = build.errors[0].message if build.errors else build.stderr or "unknown error"
            console.print(f"  [red]Build failed:[/] {' '.join(detail.split())[:160]}")
    raise click.ClickException(f"Could not improve after {max_attempts} attempts.")


# ---------------------------------------------------------------------------
# challenge — attempt an open mathematical problem
# ---------------------------------------------------------------------------


def _prepare_challenge_source(
    lean_root: Path,
    path: Path,
    problem_id: str,
    rendered_source: str,
) -> tuple[Path, bool]:
    """Create a challenge source or reopen the exact generated workspace."""
    if not path.exists():
        accepted, _ = _accept_generated_source(lean_root, path, rendered_source, timeout=300)
        return accepted, False

    content = path.read_text(encoding="utf-8")
    marker = f"Generated by: autolean challenge {problem_id}"
    if marker not in content:
        raise click.ClickException(
            f"Challenge path is owned by another source: {path}. "
            "Choose a different project or move the file explicitly."
        )
    return path, True


def _show_open_problem(problem: OpenProblem) -> None:
    """Display one curated problem and its formalization boundary."""
    colors = {
        "accessible": "green",
        "hard": "yellow",
        "very-hard": "red",
        "millennium": "bold magenta",
    }
    color = colors.get(problem.difficulty, "white")
    content = (
        f"[bold]{problem.name}[/bold]\n"
        f"Field:         {problem.field}\n"
        f"Difficulty:    [{color}]{problem.difficulty}[/{color}]\n"
        f"Formalization: {problem.formalization_status}\n\n"
        f"{problem.description}"
    )
    if problem.limitations:
        content += f"\n\nBoundary: {problem.limitations}"
    if problem.sub_results:
        content += f"\nSub-results: {len(problem.sub_results)} provable lemma(s)"
    if problem.references:
        content += f"\nRef: {problem.references[0]}"
    console.print(
        Panel(
            content,
            title=f"Problem: {problem.id}",
            border_style=color.split()[-1],
            width=75,
        )
    )


def _prepare_research_brief(lean_root: Path, problem: OpenProblem) -> tuple[Path, bool]:
    """Create or reopen the source-fidelity brief for a semantic scaffold."""
    from autolean.challenges import render_research_brief

    path = lean_root / "AutoLean" / "Research" / f"{problem.id}.md"
    if path.exists():
        return path, True
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("x", encoding="utf-8") as handle:
            handle.write(render_research_brief(problem))
    except FileExistsError:
        return path, True
    return path, False


@extra_commands.command(hidden=True)
@click.argument("problem_id", required=False)
@click.option("--field", type=str, default=None, help="Filter by field (e.g., 'number theory').")
@click.option(
    "--difficulty",
    type=click.Choice(["accessible", "hard", "very-hard", "millennium"]),
    default=None,
    help="Filter by difficulty.",
)
@click.option(
    "--max-cycles",
    type=click.IntRange(min=0),
    default=5,
    show_default=True,
    help="Cycle budget for this session (0 = unlimited).",
)
@model_option
@provider_option
@escalation_options
@click.option(
    "--guide",
    multiple=True,
    help="Add a mathematical constraint or preferred method.",
)
@program_option
def challenge(
    problem_id: str | None,
    field: str | None,
    difficulty: str | None,
    max_cycles: int,
    model: str | None,
    backend: str | None,
    escalation: str | None,
    escalate_to: str | None,
    escalate_after: int | None,
    guide: tuple[str, ...],
    program: Path,
) -> None:
    """Take on an open mathematical problem.

    \b
    Same catalog and workflow as `autolean problems`.
    Without arguments, lists all available problems.
    With a problem ID, generates the formalization and starts proving.

    \b
    Examples:
      autolean challenge                        # list all problems
      autolean challenge collatz                 # attempt Collatz conjecture
      autolean challenge goldbach --max-cycles 100
      autolean challenge --field "number theory" # filter by field
      autolean challenge --difficulty accessible # show easiest problems
    """
    from autolean.challenges import (
        OPEN_PROBLEMS,
        print_problems_table,
        render_challenge_source,
    )
    from autolean.program import parse_program

    if problem_id is None:
        print_problems_table(filter_field=field, filter_difficulty=difficulty)
        return

    problem = next((p for p in OPEN_PROBLEMS if p.id == problem_id), None)
    if not problem:
        needle = problem_id.lower()
        matches = [p for p in OPEN_PROBLEMS if needle in p.id.lower() or needle in p.name.lower()]
        if len(matches) == 1:
            problem = matches[0]
        elif matches:
            console.print(f"[yellow]Multiple matches for '{problem_id}':[/]")
            for m in matches:
                console.print(f"  {m.id}: {m.name}")
            raise click.ClickException(f"Problem ID '{problem_id}' is ambiguous.")
        else:
            raise click.ClickException(
                f"No problem with ID '{problem_id}'; run `autolean challenge` to list IDs."
            )

    _show_open_problem(problem)

    cfg = parse_program(program)
    lean_root = program.parent / cfg.lean_project_path

    if problem.formalization_status != "formalized":
        brief, continued = _prepare_research_brief(lean_root, problem)
        action = "Continuing" if continued else "Created"
        console.print(
            f"\n[cyan]{action} formalization research:[/] {brief}\n"
            "[yellow]Proof search waits for a source-faithful Lean statement.[/]\n"
            f'Review the semantic boundary, then run `autolean plan "{problem.description}"` '
            "with source-derived guidance."
        )
        return

    filename = f"Challenge_{problem.id.replace('-', '_').title()}.lean"
    path = lean_root / "AutoLean" / filename
    path, continued = _prepare_challenge_source(
        lean_root,
        path,
        problem.id,
        render_challenge_source(problem),
    )
    action = "Continuing" if continued else "Accepted"
    console.print(f"\n[green]{action}:[/] {path}")

    from autolean.scanner import count_sorries

    n_sorry = count_sorries(path.read_text(encoding="utf-8"))
    console.print(f"[cyan]{n_sorry} auditable sorry target(s)[/]")
    if n_sorry == 0:
        console.print("[bold green]Challenge workspace is complete.[/]")
        return

    console.print(f"\n[bold]Starting proof attempts ({max_cycles} cycles)...[/]\n")

    agent = _agent_for(
        program,
        model=model,
        backend=backend,
        verbose=True,
        resume=continued,
        target_file=path,
    )
    _configure_escalation(
        agent,
        escalation=escalation,
        escalate_to=escalate_to,
        escalate_after=escalate_after,
    )
    agent.config.max_cycles = max_cycles

    from autolean.session import SessionKind, SessionStore

    store = SessionStore(agent.project.root)
    session = store.find_target(path) if continued else None
    settings = cli_runtime.session_settings(agent)
    if session is None:
        session = store.create(
            kind=SessionKind.PROBLEM,
            title=problem.name,
            target_file=path,
            guidance=guide,
            **settings,
        )
    else:
        guidance = tuple(dict.fromkeys([*session.guidance, *guide]))
        session = store.save(session.update(guidance=guidance, **settings))
    _run_session_agent(agent, store, session)


@extra_commands.group("problems", invoke_without_command=True)
@click.pass_context
def problems(ctx: click.Context) -> None:
    """Discover, inspect, and work on curated open problems."""
    if ctx.invoked_subcommand is None:
        ctx.invoke(problems_list)


@problems.command("list")
@click.option("--field", type=str, default=None, help="Filter by mathematical field.")
@click.option(
    "--difficulty",
    type=click.Choice(["accessible", "hard", "very-hard", "millennium"]),
    default=None,
    help="Filter by difficulty.",
)
def problems_list(field: str | None, difficulty: str | None) -> None:
    """List the curated open-problem catalog."""
    from autolean.challenges import print_problems_table

    print_problems_table(filter_field=field, filter_difficulty=difficulty)


@problems.command("search")
@click.argument("query")
def problems_search(query: str) -> None:
    """Search names, fields, descriptions, boundaries, and tags."""
    from rich.table import Table

    from autolean.challenges import search_problems

    matches = search_problems(query)
    if not matches:
        raise click.ClickException(f"No curated problem matches '{query}'.")
    table = Table(title=f"Open-problem search: {query}")
    table.add_column("ID", style="cyan")
    table.add_column("Problem")
    table.add_column("Field")
    table.add_column("Readiness")
    for problem in matches:
        table.add_row(problem.id, problem.name, problem.field, problem.formalization_status)
    console.print(table)


@problems.command("show")
@click.argument("problem_id")
def problems_show(problem_id: str) -> None:
    """Show one problem and its exact semantic boundary."""
    from autolean.challenges import OPEN_PROBLEMS

    problem = next((item for item in OPEN_PROBLEMS if item.id == problem_id), None)
    if problem is None:
        raise click.ClickException(f"No problem with ID '{problem_id}'.")
    _show_open_problem(problem)


@problems.command("suggest")
@click.option("--field", type=str, default=None, help="Prefer a mathematical field.")
@click.option(
    "--difficulty",
    type=click.Choice(["accessible", "hard", "very-hard", "millennium"]),
    default=None,
    help="Require a difficulty level.",
)
def problems_suggest(field: str | None, difficulty: str | None) -> None:
    """Suggest work with the strongest current formalization footing."""
    from autolean.challenges import suggest_problems

    matches = suggest_problems(field=field, difficulty=difficulty)
    if not matches:
        raise click.ClickException("No curated problem matches those constraints.")
    console.print("[bold]Suggested problems[/]")
    for index, problem in enumerate(matches, 1):
        reason = (
            f"{len(problem.sub_results)} bounded sub-results"
            if problem.sub_results
            else "source formalization research"
        )
        console.print(
            f"  [cyan]{index}. {problem.id}[/] — {problem.name}\n"
            f"     {problem.formalization_status}; {reason}"
        )
    console.print(f"\n[dim]Start:[/] autolean problems work {matches[0].id}")


@problems.command("work")
@click.argument("problem_id")
@click.option(
    "--max-cycles",
    type=click.IntRange(min=0),
    default=5,
    show_default=True,
    help="Cycle budget for this session (0 = unlimited).",
)
@model_option
@provider_option
@escalation_options
@click.option(
    "--guide",
    multiple=True,
    help="Add a mathematical constraint or preferred method.",
)
@program_option
@click.pass_context
def problems_work(
    ctx: click.Context,
    problem_id: str,
    max_cycles: int,
    model: str | None,
    backend: str | None,
    escalation: str | None,
    escalate_to: str | None,
    escalate_after: int | None,
    guide: tuple[str, ...],
    program: Path,
) -> None:
    """Create or continue a formalization or proof workspace."""
    ctx.invoke(
        challenge,
        problem_id=problem_id,
        field=None,
        difficulty=None,
        max_cycles=max_cycles,
        model=model,
        backend=backend,
        escalation=escalation,
        escalate_to=escalate_to,
        escalate_after=escalate_after,
        guide=guide,
        program=program,
    )


def register_commands(root: click.Group) -> None:
    """Register supplementary workflows on the root command group."""
    for command in extra_commands.commands.values():
        root.add_command(command)
