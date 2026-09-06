"""Record live supporting-lemma learning and Gromov's open question."""

from __future__ import annotations

import argparse
import csv
import json
import os
import shutil
import subprocess
import sys
import tempfile
from datetime import UTC, datetime
from pathlib import Path

from autolean.lean_interface import LeanProject
from autolean.program import parse_program
from autolean.progress import PROGRESS_PREFIX, ProgressEvent, ProgressKind
from autolean.scanner import scan_file
from scripts.demo_media import (
    assert_tape_contract,
    file_identity,
    playback_speed,
    preserve_workspace,
    publish_outputs,
    render_tape,
    require_media_tools,
    sha256,
    staging_outputs,
    validate_media,
    vhs_version,
)
from scripts.lean_workspace import copy_workspace
from scripts.record_paper_demo import recording_environment

_SUPPORT = "residually_finite_of_finite_quotients"
_QUESTION = "residual_finiteness_question"


def prepare(repository: Path, root: Path) -> None:
    """Create a research project with its own source and pinned artifacts."""
    subprocess.run(
        [sys.executable, "-m", "autolean", "init", "lean", "--example", "gromov", "--no-cslib"],
        cwd=root,
        check=True,
    )
    copy_workspace(repository / "workspace/.lake", root / "lean/.lake")
    shutil.copy2(repository / "workspace/lake-manifest.json", root / "lean/lake-manifest.json")
    program = root / "program.md"
    text = program.read_text().replace("model: auto", "model: codex\nllm_timeout_seconds: 1200")
    text = text.replace("max_cycles: 5", "max_cycles: 3")
    text = text.replace("max_retries_per_sorry: 5", "max_retries_per_sorry: 3")
    text = text.replace("escalation_policy: ask", "escalation_policy: never")
    text = text.replace("search_scope: auto", "search_scope: local")
    hints = (
        "- For the converse use Group.residuallyFinite_iff_exists_finiteIndexNormalSubgroup.\n"
        "- Separate the pair {g, 1}; use QuotientGroup.eq_one_iff.\n"
        "- QuotientGroup.eq_one_iff takes the element explicitly: "
        "use (QuotientGroup.eq_one_iff g).mpr for quotient membership.\n"
    )
    text = text.replace("## LLM Configuration", hints + "\n## LLM Configuration")
    program.write_text(text)


def _assert_research(events: list[ProgressEvent], source: Path) -> None:
    """Require accepted learning and an observed, unresolved open attempt."""
    if not any(
        event.target == _SUPPORT and event.kind is ProgressKind.FEEDBACK and event.message == "success"
        for event in events
    ):
        raise SystemExit("the live model did not prove the supporting lemma")
    for kind in (ProgressKind.CANDIDATE, ProgressKind.FEEDBACK, ProgressKind.FINISHED):
        if not any(event.target == _QUESTION and event.kind is kind for event in events):
            raise SystemExit(f"the open attempt has no {kind.value} observation")
    if not any(
        event.target == _QUESTION
        and event.kind is ProgressKind.LEARNING
        and "from accepted proofs" in event.message
        for event in events
    ):
        raise SystemExit("the open attempt did not reuse an accepted proof pattern")
    names = {target.decl_name for target in scan_file(source)}
    if _SUPPORT in names or _QUESTION not in names:
        raise SystemExit("research source does not preserve the expected proven/open boundary")


def record(repository: Path, root: Path) -> None:
    """Publish media only after checking the live run's evidence."""
    project = root / "lean"
    source = project / "LeanProofs.lean"
    before = sha256(source)
    config = parse_program(root / "program.md").llm_config()
    if (config.model, config.effort) != ("gpt-6-astra", "max"):
        raise SystemExit("the live recording requires gpt-6-astra at max effort")
    existing_journals = set((project / "logs").glob("*.events.jsonl"))
    tape = repository / "docs/demos/gromov.tape"
    tape_sources = assert_tape_contract(
        repository, tape, ("autolean", "bash", "codex", "clear"), interactive=True
    )
    environment = recording_environment(AUTOLEAN_DEMO_ROOT=str(root))
    environment["PATH"] = str(Path(sys.executable).parent) + os.pathsep + environment["PATH"]
    with staging_outputs(repository, "autolean-gromov") as staged:
        render_tape(repository, tape, staged, environment=environment, timeout=6000)
        journals = sorted(set((project / "logs").glob("*.events.jsonl")) - existing_journals)
        lines = [line for journal in journals for line in journal.read_text().splitlines()]
        events = [
            event for line in lines if (event := ProgressEvent.from_line(PROGRESS_PREFIX + line)) is not None
        ]
        _assert_research(events, source)
        with (project / "results.tsv").open(newline="") as handle:
            records = list(csv.DictReader(handle, delimiter="\t"))
        accepted = [row for row in records if row["decl_name"] == _SUPPORT and row["outcome"] == "success"]
        if not accepted or accepted[-1]["model"] != config.model:
            raise SystemExit("the supporting proof lacks the expected live model record")
        if accepted[-1]["source_after_sha256"] != sha256(source):
            raise SystemExit("the source differs from the accepted proof record")
        line = next(
            index
            for index, text in enumerate(source.read_text().splitlines(), 1)
            if text.startswith(f"theorem {_SUPPORT}")
        )
        audit = LeanProject(project).validate_candidate(
            source,
            source.read_text(),
            declaration=f"Gromov.{_SUPPORT}",
            declaration_line=line,
            timeout=240,
            expected_environment=accepted[-1]["environment_sha256"],
        )
        if not audit.success:
            raise SystemExit(f"the recorded proof failed its independent audit: {audit.errors}")
        media = validate_media(staged)
        publish_outputs(repository, staged)
    evidence = repository / "docs/demos"
    activity = evidence / "gromov-events.jsonl"
    activity.write_text("\n".join(lines) + "\n")
    snapshot = evidence / "gromov-run.lean"
    shutil.copy2(source, snapshot)
    receipt = {
        "schema": "autolean.research-demo.v1",
        "recorded_at": datetime.now(UTC).isoformat(),
        "model": "gpt-6-astra",
        "effort": "max",
        "source_before_sha256": before,
        "source_after": file_identity(snapshot),
        "program_sha256": sha256(root / "program.md"),
        "activity": file_identity(activity),
        "accepted_support": accepted[-1],
        "open_target": _QUESTION,
        "open_targets": sorted(target.decl_name for target in scan_file(source)),
        "media": media,
        "tape_sources": tape_sources,
        "playback_speed": playback_speed(repository),
        "hidden_waits": "Model and Lean waits are omitted from playback.",
        "vhs_version": vhs_version(),
    }
    (evidence / "gromov.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n")
    print("Published the live Gromov research recording and its evidence.")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--workspace", type=Path, help="Prepared demo directory containing program.md and lean/"
    )
    options = parser.parse_args()
    require_media_tools()
    repository = Path(__file__).resolve().parents[1]
    if options.workspace is not None:
        root = options.workspace.resolve()
        with preserve_workspace(repository, root):
            record(repository, root)
        return
    with tempfile.TemporaryDirectory(prefix="autolean-gromov-demo-") as directory:
        root = Path(directory)
        with preserve_workspace(repository, root):
            prepare(repository, root)
            record(repository, root)


if __name__ == "__main__":
    main()
