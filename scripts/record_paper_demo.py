#!/usr/bin/env python3
"""Record the reviewed Ionescu-Tulcea paper workflow with a live model."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from autolean.paper import fetch_arxiv
from autolean.scanner import count_sorries

try:
    from scripts.demo_media import (
        assert_tape_contract,
        completed_session,
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
    from scripts.lean_workspace import clear_runtime_state, copy_workspace, initialize_repository
except ImportError:
    from demo_media import (  # type: ignore[no-redef]
        assert_tape_contract,
        completed_session,
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
    from lean_workspace import clear_runtime_state, copy_workspace, initialize_repository

ARXIV_ID = "2506.18616v5"
PDF_SHA256 = "39db363898dfb4a51c0e344a6154f76dd6c3e8768a414d516853e6cdc12dfe2d"
PROFILE_ID = "arxiv-2506.18616v5"
ITEM_COUNT = 25
DECLARATION_COUNT = 33
MODEL_PROFILE = "codex"
REVIEW_GUIDANCE = (
    "Separate the executable alias-elaboration audit from follow-up work. "
    "Do not claim signature comparison, mapping grades, paper-form equivalence, "
    "or per-declaration axiom audits."
)
TAPE_REQUIREMENTS = (
    "autolean",
    "bash",
    "clear",
    "codex",
    "find",
    "jq",
    "lake",
    "python3",
    "sort",
)


@dataclass(frozen=True)
class DemoWorkspace:
    """Paths owned by one isolated recording."""

    root: Path
    pdf: Path


def _materialize_pdf(root: Path) -> Path:
    """Acquire the exact reviewed PDF revision and verify its identity."""
    configured = os.environ.get("AUTOLEAN_DEMO_PDF")
    if configured:
        source = Path(configured).expanduser().resolve()
        if not source.is_file():
            raise SystemExit(f"AUTOLEAN_DEMO_PDF is not a file: {source}")
        pdf = root / f"arxiv_{ARXIV_ID}.pdf"
        shutil.copy2(source, pdf)
    else:
        pdf = fetch_arxiv(ARXIV_ID, root)
    actual = sha256(pdf)
    if actual != PDF_SHA256:
        raise SystemExit(f"reviewed PDF SHA-256 differs: expected {PDF_SHA256}, got {actual}")
    return pdf


@contextmanager
def demo_workspace(repository: Path) -> Iterator[DemoWorkspace]:
    """Yield an isolated project backed by the pinned local Lean closure."""
    with tempfile.TemporaryDirectory(prefix="autolean-paper-demo-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        copy_workspace(repository / "workspace", workspace)
        clear_runtime_state(workspace)
        initialize_repository(workspace)
        shutil.copy2(repository / "program.md", root / "program.md")
        yield DemoWorkspace(root=root, pdf=_materialize_pdf(root))


def _autolean() -> str:
    executable = shutil.which("autolean")
    if executable is None:
        raise SystemExit("autolean is unavailable; enter `nix develop`")
    return executable


def _single_record(root: Path, pattern: str, schema: str) -> tuple[Path, dict[str, object]]:
    paths = list((root / "workspace" / "AutoLean" / "Papers").glob(pattern))
    records: list[tuple[Path, dict[str, object]]] = []
    for path in paths:
        record = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(record, dict) and record.get("schema") == schema:
            records.append((path, record))
    if len(records) != 1:
        raise SystemExit(f"demo requires one {schema} record, found {len(records)}")
    return records[0]


def _assert_plan(root: Path) -> Path:
    path, plan = _single_record(root, "*_plan_*.json", "autolean.paper-plan.v2")
    responses = plan.get("responses")
    if not isinstance(responses, list) or len(responses) < 2:
        raise SystemExit("paper plan does not show a provider repair or human revision")
    accepted = responses[-1]
    if not isinstance(accepted, dict) or accepted.get("validation_error"):
        raise SystemExit("paper plan has no valid final provider response")
    response = accepted.get("response")
    if not isinstance(response, str):
        raise SystemExit("paper plan omits the exact accepted provider response")
    if hashlib.sha256(response.encode()).hexdigest() != plan.get("accepted_response_sha256"):
        raise SystemExit("paper plan accepted-response identity differs")
    if accepted.get("model") != plan.get("accepted_response_model"):
        raise SystemExit("paper plan accepted-model identity differs")
    return path


def _assert_coverage(root: Path) -> Path:
    path, coverage = _single_record(root, "*_coverage_*.json", "autolean.paper-coverage.v2")
    profile = coverage.get("profile")
    evidence = coverage.get("lean_evidence")
    claims = coverage.get("claims")
    valid = (
        isinstance(profile, dict)
        and profile.get("id") == PROFILE_ID
        and coverage.get("total_items") == ITEM_COUNT
        and coverage.get("elaborated_items") == ITEM_COUNT
        and isinstance(claims, list)
        and len(claims) == ITEM_COUNT
        and isinstance(evidence, dict)
        and evidence.get("success") is True
        and evidence.get("error_count") == 0
        and evidence.get("declaration_count") == DECLARATION_COUNT
    )
    if not valid:
        raise SystemExit("paper coverage does not contain the complete 25/33 Lean result")
    return path


def _assert_compiles(root: Path) -> None:
    """Compile the paper module from the standalone exported project."""
    project = root / "paper-artifact" / "project"
    sources = sorted((project / "AutoLean").glob("Paper_*.lean"))
    if len(sources) != 1:
        raise SystemExit(f"demo export contains an unexpected paper source set: {sources}")
    source = sources[0]
    if not (project / ".lake").exists():
        copy_workspace(root / "workspace" / ".lake", project / ".lake")
    result = subprocess.run(
        ["lake", "env", "lean", str(source.relative_to(project))],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        diagnostics = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
        raise SystemExit(f"pinned Lean rejected the exported paper module:\n{diagnostics}")


def _assert_export(root: Path) -> None:
    export = root / "paper-artifact"
    manifest = json.loads((export / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != "autolean.project-export.v1":
        raise SystemExit("demo export manifest has an unsupported schema")
    project = export / "project"
    lakefile = project / "lakefile.lean"
    if not lakefile.is_file():
        raise SystemExit("demo export omits lakefile.lean")
    sources = sorted(
        path
        for path in project.rglob("*.lean")
        if path != lakefile and ".lake" not in path.relative_to(project).parts
    )
    if len(sources) != 2 or any(path.name == "UserTheorems.lean" for path in sources):
        names = [path.relative_to(project).as_posix() for path in sources]
        raise SystemExit(f"demo export contains an unexpected Lean source set: {names}")
    evidence = next(path for path in sources if path.name.startswith("Paper_"))
    text = evidence.read_text(encoding="utf-8")
    if "import Mathlib.Probability.ProductMeasure" not in text:
        raise SystemExit("demo evidence does not use the reviewed import closure")
    if count_sorries(text):
        raise SystemExit("demo evidence contains a proof placeholder")


def _assert_demo(root: Path) -> None:
    _assert_plan(root)
    _assert_coverage(root)
    completed_session(root, "paper")
    _assert_export(root)
    _assert_compiles(root)


def _write_demo_manifest(
    repository: Path,
    root: Path,
    media: list[dict[str, object]],
    tape_sources: list[dict[str, object]],
    recorder_version: str,
) -> Path:
    """Bind the recording to its live provider and Lean evidence."""
    plan_path, plan = _single_record(root, "*_plan_*.json", "autolean.paper-plan.v2")
    coverage_path, coverage = _single_record(
        root,
        "*_coverage_*.json",
        "autolean.paper-coverage.v2",
    )
    session = str(completed_session(root, "paper")["id"])
    record = {
        "arxiv_id": ARXIV_ID,
        "coverage_sha256": sha256(coverage_path),
        "evidence": coverage["lean_evidence"],
        "export_manifest_sha256": sha256(root / "paper-artifact" / "manifest.json"),
        "media": media,
        "model": plan["accepted_response_model"],
        "pdf_sha256": PDF_SHA256,
        "playback_speed": playback_speed(repository),
        "plan_artifact_sha256": sha256(plan_path),
        "plan_sha256": plan["plan_sha256"],
        "plan_trace_sha256": plan["trace_sha256"],
        "recorded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "response_sha256": plan["accepted_response_sha256"],
        "schema": "autolean.demo.v1",
        "session": session,
        "tape_sha256": tape_sources[0]["sha256"],
        "tape_sources": tape_sources,
        "vhs_version": recorder_version,
    }
    path = repository / "docs" / "demos" / "ionescu-tulcea.json"
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _run_check(workspace: DemoWorkspace) -> None:
    """Run the live workflow without rendering media."""
    command = [
        _autolean(),
        "verify",
        str(workspace.pdf),
        "--model",
        MODEL_PROFILE,
        "--review-plan",
        "--max-cycles",
        "5",
    ]
    result = subprocess.run(
        command,
        cwd=workspace.root,
        input=f"n\n{REVIEW_GUIDANCE}\ny\n",
        text=True,
        timeout=2400,
    )
    if result.returncode != 0:
        raise SystemExit(result.returncode)
    session = str(completed_session(workspace.root, "paper")["id"])
    subprocess.run(
        [
            _autolean(),
            "export",
            "paper-artifact",
            "--session",
            session,
            "--title",
            "Ionescu-Tulcea paper audit",
        ],
        cwd=workspace.root,
        check=True,
        timeout=120,
    )
    _assert_demo(workspace.root)


def recording_environment(**demo_variables: str) -> dict[str, str]:
    """Build the environment VHS hands to the recorded shell.

    A color-suppressing variable inherited from the invoking session would
    strip the CLI theme from the published media.
    """
    environment = os.environ.copy()
    for name in ("NO_COLOR", "CLICOLOR", "CLICOLOR_FORCE"):
        environment.pop(name, None)
    environment.update(demo_variables)
    return environment


def _record(repository: Path, workspace: DemoWorkspace) -> None:
    """Render the GIF and MP4 from the versioned live VHS tape."""
    tape = repository / "docs" / "demos" / "ionescu-tulcea.tape"
    tape_sources = assert_tape_contract(repository, tape, TAPE_REQUIREMENTS)
    recorder_version = vhs_version()
    environment = recording_environment(
        AUTOLEAN_DEMO_ROOT=str(workspace.root),
        AUTOLEAN_DEMO_PDF=str(workspace.pdf),
        AUTOLEAN_DEMO_WORKSPACE_HELPER=str(repository / "scripts" / "lean_workspace.py"),
    )
    with staging_outputs(repository, "autolean-ionescu-tulcea") as staged:
        render_tape(
            repository,
            tape,
            staged,
            environment=environment,
            timeout=3600,
        )
        _assert_demo(workspace.root)
        media = validate_media(staged)
        media_paths = publish_outputs(repository, staged)
    for path in media_paths:
        print(f"{path.relative_to(repository)} ({path.stat().st_size:,} bytes)")
    manifest = _write_demo_manifest(
        repository,
        workspace.root,
        media,
        tape_sources,
        recorder_version,
    )
    print(f"{manifest.relative_to(repository)} ({manifest.stat().st_size:,} bytes)")


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check",
        action="store_true",
        help="run the live workflow without rendering media",
    )
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    """Prepare an isolated project, then run or record the live demo."""
    options = _parser().parse_args(arguments)
    if not options.check:
        require_media_tools()
    repository = Path(__file__).resolve().parents[1]
    with demo_workspace(repository) as workspace, preserve_workspace(repository, workspace.root):
        if options.check:
            _run_check(workspace)
        else:
            _record(repository, workspace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
