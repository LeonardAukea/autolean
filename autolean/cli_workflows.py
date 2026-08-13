"""Command adapters for research, maintenance, and compatibility workflows."""

from __future__ import annotations

import subprocess
from pathlib import Path

import click
from rich.panel import Panel

from autolean import cli_runtime, ui
from autolean.challenges import OpenProblem
from autolean.llm import LLMError
from autolean.provenance import ProofEnvironmentError
from autolean.ui import console

_accept_generated_source = cli_runtime.accept_generated_source
_agent_for = cli_runtime.agent_for
_configure_escalation = cli_runtime.configure_escalation
_connected_llm = cli_runtime.connected_llm
_run_agent = cli_runtime.run_agent
_run_session_agent = cli_runtime.run_session_agent
backend_option = cli_runtime.backend_option
escalation_options = cli_runtime.escalation_options
model_option = cli_runtime.model_option
program_option = cli_runtime.program_option


@click.group()
def extra_commands() -> None:
    """Own supplementary commands registered on the root CLI."""


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
    """Export training data from previous runs (SFT, ShareGPT, DPO).

    \b
    Uses results.tsv + cached proof data to generate:
      - SFT JSONL (instruction tuning with successful proofs)
      - ShareGPT JSONL (Hermes/Axolotl compatible)
      - DPO JSONL (preference pairs: good proof vs bad proof)

    \b
    Use the exported data to fine-tune Gemma or other models:
      pip install unsloth
      # See workspace/training_data/finetune_config.yaml
    """
    project = project.resolve()

    # Read from existing training data files
    td = project / "training_data"
    if not td.exists() or not any(td.glob("*.jsonl")):
        console.print("[yellow]No training data found. Run the agent first:[/]")
        console.print("  uv run autolean solve --max-cycles 20")
        return

    # Show existing files
    console.print("[bold]Training data files:[/]\n")
    for f in sorted(td.glob("*.jsonl")):
        with open(f, encoding="utf-8") as handle:
            lines = sum(1 for _ in handle)
        size = f.stat().st_size / 1024
        console.print(f"  {f.name} ({lines} examples, {size:.1f} KB)")

    # Show stats
    console.print("\n[bold]Usage:[/]")
    console.print("  Fine-tune with Unsloth:")
    console.print(f"    unsloth train --data {td}/sft_*.jsonl --model gemma4:26b")
    console.print("  Fine-tune with Axolotl:")
    console.print(f"    axolotl train {td}/finetune_config.yaml")
    console.print("  DPO training:")
    console.print(f"    Use {td}/dpo_*.jsonl with TRL DPOTrainer")


# ---------------------------------------------------------------------------
# finetune-config — generate training configuration
# ---------------------------------------------------------------------------


@extra_commands.command("finetune-config", hidden=True)
@click.option(
    "--project",
    "-d",
    type=click.Path(exists=True, path_type=Path),
    default="workspace",
    help="Path to Lean project root.",
)
@click.option("--model", "-m", default="google/gemma-4-E2B", help="Base model for fine-tuning.")
@click.option(
    "--framework",
    type=click.Choice(["unsloth", "axolotl", "trl"]),
    default="axolotl",
    help="Training framework.",
)
def finetune_config(project: Path, model: str, framework: str) -> None:
    """Generate a fine-tuning config for Lean 4 proof models.

    Writes a config file for the selected framework.
    """
    import yaml

    project = project.resolve()
    td = project / "training_data"
    td.mkdir(parents=True, exist_ok=True)

    sft_files = sorted(td.glob("sft_*.jsonl"))
    dpo_files = sorted(td.glob("dpo_*.jsonl"))

    if framework == "axolotl":
        config = {
            "base_model": model,
            "model_type": "AutoModelForCausalLM",
            "tokenizer_type": "AutoTokenizer",
            "load_in_4bit": True,
            "adapter": "qlora",
            "lora_r": 64,
            "lora_alpha": 64,
            "lora_dropout": 0.0,
            "lora_target_modules": [
                "q_proj",
                "k_proj",
                "v_proj",
                "o_proj",
                "gate_proj",
                "up_proj",
                "down_proj",
            ],
            "datasets": [
                {
                    "path": str(sft_files[-1]) if sft_files else "training_data/sft.jsonl",
                    "type": "sharegpt",
                    "conversation": "chatml",
                },
            ],
            "sequence_len": 8192,
            "micro_batch_size": 1,
            "gradient_accumulation_steps": 8,
            "num_epochs": 3,
            "learning_rate": 2e-4,
            "lr_scheduler": "cosine",
            "warmup_ratio": 0.1,
            "optimizer": "adamw_8bit",
            "bf16": True,
            "gradient_checkpointing": True,
            "output_dir": str(td / "output"),
            "logging_steps": 10,
            "save_strategy": "epoch",
            "wandb_project": "autolean-finetune",
        }
        config_path = td / "axolotl_config.yaml"
        config_path.write_text(
            yaml.dump(config, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        console.print(f"[green]Generated Axolotl config:[/] {config_path}")
        console.print(f"\n  Run: accelerate launch -m axolotl.cli.train {config_path}")

    elif framework == "unsloth":
        config = {
            "model_name": model,
            "max_seq_length": 8192,
            "load_in_4bit": True,
            "lora_r": 64,
            "lora_alpha": 64,
            "dataset": str(sft_files[-1]) if sft_files else "training_data/sft.jsonl",
            "dataset_type": "messages",
            "num_epochs": 3,
            "learning_rate": 2e-4,
            "batch_size": 1,
            "gradient_accumulation": 8,
            "output_dir": str(td / "output"),
        }
        config_path = td / "unsloth_config.yaml"
        config_path.write_text(
            yaml.dump(config, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        console.print(f"[green]Generated Unsloth config:[/] {config_path}")
        console.print("\n  pip install unsloth")
        console.print(f"  python -m unsloth.train --config {config_path}")

    elif framework == "trl":
        config = {
            "model_name": model,
            "dataset_path": str(dpo_files[-1]) if dpo_files else "training_data/dpo.jsonl",
            "lora_r": 64,
            "lora_alpha": 64,
            "beta": 0.1,
            "num_epochs": 1,
            "learning_rate": 5e-6,
            "batch_size": 1,
            "gradient_accumulation_steps": 8,
            "output_dir": str(td / "dpo_output"),
        }
        config_path = td / "trl_dpo_config.yaml"
        config_path.write_text(
            yaml.dump(config, default_flow_style=False, sort_keys=False),
            encoding="utf-8",
        )
        console.print(f"[green]Generated TRL DPO config:[/] {config_path}")
        console.print("\n  pip install trl")
        console.print(f"  Use DPOTrainer with config from {config_path}")

    # Summary
    console.print("\n[bold]Fine-tuning loop:[/]")
    console.print("  1. Run agent:      uv run autolean solve --overnight")
    console.print("  2. Export data:     uv run autolean export-training")
    console.print(f"  3. Fine-tune:      {framework} train ...")
    console.print("  4. Import model:   ollama create autolean-v1 -f Modelfile")
    console.print("  5. Run again:      uv run autolean solve --model autolean-v1")


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
@backend_option
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
    import re as _re

    from autolean.library import generate_library_source
    from autolean.program import parse_program

    cfg = parse_program(program)
    llm = _connected_llm(model, backend, cfg, timeout=600.0)

    safe_topic = _re.sub(r"[^a-zA-Z0-9]", "", topic.title().replace(" ", ""))
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

    # Count generated declarations and proof targets.
    n_defs = len(_re.findall(r"\b(?:def|structure|class|instance|theorem|lemma)\b", content))
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
        console.print(f"\n  Next: [cyan]uv run autolean solve[/] to attempt {n_sorrys} proofs")


# ---------------------------------------------------------------------------
# improve — rewrite an existing proof toward a --goal
# ---------------------------------------------------------------------------


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
@backend_option
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
    import re

    from autolean.lean_interface import LeanProject
    from autolean.program import parse_program
    from autolean.prompts import PROOF_GOLF_USER

    cfg = parse_program(program)
    file_path = file_path.resolve()

    # Find the theorem and its proof in the file
    content = file_path.read_text(encoding="utf-8")
    lines = content.split("\n")

    # Locate the theorem declaration
    theorem_line = None
    for i, line in enumerate(lines):
        if re.search(rf"\b{re.escape(theorem_name)}\b", line) and re.match(
            r"\s*(theorem|lemma|def)\s+", line
        ):
            theorem_line = i
            break

    if theorem_line is None:
        raise click.ClickException(f"Theorem '{theorem_name}' was not found in {file_path}.")

    # The current proof ends at the next declaration.
    proof_start = None
    proof_end = None
    for i in range(theorem_line, len(lines)):
        if "by" in lines[i] and proof_start is None:
            proof_start = i + 1
        elif proof_start is not None and i > proof_start:
            stripped = lines[i].strip()
            unindented = not lines[i].startswith((" ", "\t"))
            if stripped and not stripped.startswith("--") and unindented:
                proof_end = i
                break
    if proof_start is None:
        raise click.ClickException(f"No tactic proof found for '{theorem_name}'.")
    if proof_end is None:
        proof_end = len(lines)

    current_proof = "\n".join(lines[proof_start:proof_end])
    decl_line = "\n".join(lines[theorem_line:proof_start])

    console.print(f"[bold]Improving:[/] {theorem_name}")
    console.print(f"[bold]Goal:[/] {goal}")
    console.print("[bold]Current proof:[/]")
    for line in current_proof.split("\n")[:10]:
        console.print(f"  [dim]{line}[/]")
    console.print()

    # Find project root
    lean_root = file_path.parent
    while lean_root != lean_root.parent:
        if (lean_root / "lakefile.lean").exists() or (lean_root / "lakefile.toml").exists():
            break
        lean_root = lean_root.parent
    project = LeanProject(lean_root)
    try:
        proof_environment = project.proof_environment(refresh=True)
    except (OSError, ProofEnvironmentError) as e:
        raise click.ClickException(f"Proof environment identification failed: {e}") from e

    from autolean.scanner import _find_enclosing_decl_details, _mask_lean_noncode

    local_name, qualified_name, _ = _find_enclosing_decl_details(
        _mask_lean_noncode(content).split("\n"),
        theorem_line + 1,
    )
    if local_name != theorem_name or not qualified_name:
        raise click.ClickException(f"Theorem '{theorem_name}' has no auditable source name.")

    import textwrap

    proof_indents = [len(line) - len(line.lstrip()) for line in lines[proof_start:proof_end] if line.strip()]
    if not proof_indents:
        raise click.ClickException(f"No multiline tactic proof found for '{theorem_name}'.")
    proof_indent = " " * min(proof_indents)

    goal_prompts = {
        "shorter": "Make this proof as SHORT as possible. Minimize the number of tactics and lines.",
        "elegant": "Make this proof more ELEGANT and mathematically beautiful. Use clean, idiomatic Lean 4.",
        "faster": "Make this proof FASTER for the Lean kernel to check. "
        "Avoid slow tactics like simp on large goals.",
        "readable": "Make this proof more READABLE. Use descriptive names, add comments, structure clearly.",
    }

    system = (
        "You are a Lean 4 proof golf expert. "
        f"{goal_prompts[goal]} "
        "Output ONLY the improved tactic block. No explanation, no markdown."
    )

    from autolean.agent import clean_llm_proof
    from autolean.generated_code import GeneratedCodeError, validate_generated_proof
    from autolean.provenance import sha256_text

    with _connected_llm(model, backend, cfg) as llm:
        for attempt in range(1, max_attempts + 1):
            console.print(f"[bold]Attempt {attempt}/{max_attempts}...[/]")

            context = f"{decl_line}\n{current_proof}"
            user_prompt = PROOF_GOLF_USER.format(
                file_context=context,
                decl_name=theorem_name,
                line=theorem_line + 1,
                current_proof=current_proof,
            )

            try:
                with ui.status("Generating improved proof..."):
                    response = llm.generate(system, user_prompt)
            except LLMError as e:
                raise click.ClickException(f"Proof improvement failed: {e}") from e

            new_proof = clean_llm_proof(response.text, tactic_mode=True)
            try:
                new_proof = validate_generated_proof(new_proof)
            except GeneratedCodeError as e:
                console.print(f"  [red]Generated proof rejected:[/] {e}")
                continue

            if not new_proof or textwrap.dedent(new_proof).strip() == textwrap.dedent(current_proof).strip():
                console.print("  [yellow]No improvement generated.[/]")
                continue

            console.print("  [cyan]New proof:[/]")
            for line in new_proof.split("\n")[:8]:
                console.print(f"    [cyan]{line}[/]")

            normalized_lines = textwrap.dedent(new_proof).strip("\n").split("\n")
            replacement = [f"{proof_indent}{line}" if line else "" for line in normalized_lines]
            new_lines = lines.copy()
            new_lines[proof_start:proof_end] = replacement
            new_content = "\n".join(new_lines)

            with ui.status("Verifying proof and axioms..."):
                build = project.validate_candidate(
                    file_path,
                    new_content,
                    timeout=120,
                    declaration=qualified_name,
                    declaration_line=theorem_line + 1,
                    expected_environment=proof_environment.sha256,
                )

            if build.success:
                try:
                    project.write_file(
                        file_path,
                        new_content,
                        expected_content=content,
                    )
                except OSError as e:
                    raise click.ClickException(f"Source changed during proof improvement: {e}") from e
                old_len = len(current_proof.strip().split("\n"))
                new_len = len(normalized_lines)
                axioms = ", ".join(build.axioms) if build.axioms else "none"
                console.print(f"  [bold green]Improved![/] {old_len} lines -> {new_len} lines")
                console.print(f"  Environment: sha256:{proof_environment.sha256}")
                console.print(f"  Proof:       sha256:{sha256_text(new_proof)}")
                console.print(f"  Axioms:      {axioms}")
                if new_len < old_len:
                    pct = (old_len - new_len) / old_len * 100
                    console.print(f"  [green]Reduced by {old_len - new_len} lines ({pct:.0f}%)[/]")
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
@backend_option
@escalation_options
@click.option(
    "--guide",
    multiple=True,
    help="Add a mathematical constraint or preferred method.",
)
@click.option(
    "--program",
    "-p",
    type=click.Path(exists=True, path_type=Path),
    default="program.md",
    help="Path to program.md.",
)
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
        # Try partial match
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
                f"Problem '{problem_id}' was not found; run `autolean challenge` to list IDs."
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

    # Generate the challenge file
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

    # Count sorry targets
    from autolean.scanner import count_sorries

    n_sorry = count_sorries(path.read_text(encoding="utf-8"))
    console.print(f"[cyan]{n_sorry} auditable sorry target(s)[/]")
    if n_sorry == 0:
        console.print("[bold green]Challenge workspace is complete.[/]")
        return

    # Ask if they want to start proving
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
    if session is None:
        session = store.create(
            kind=SessionKind.PROBLEM,
            title=problem.name,
            model=agent.llm.config.model,
            backend=agent.llm.config.backend,
            max_cycles=max_cycles,
            escalation_policy=agent.config.escalation_policy,
            escalation_model=agent.config.escalation_model or "",
            escalation_after_failures=agent.config.escalation_after_failures,
            target_file=path,
            guidance=guide,
        )
    else:
        guidance = tuple(dict.fromkeys([*session.guidance, *guide]))
        session = store.save(
            session.update(
                model=agent.llm.config.model,
                backend=agent.llm.config.backend,
                max_cycles=max_cycles,
                escalation_policy=agent.config.escalation_policy,
                escalation_model=agent.config.escalation_model or "",
                escalation_after_failures=agent.config.escalation_after_failures,
                guidance=guidance,
            )
        )
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
        raise click.ClickException(f"Problem was not found: {problem_id}")
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
@backend_option
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
