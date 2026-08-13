#!/usr/bin/env python3
"""Record the reviewed Ionescu-Tulcea paper workflow with a live model."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from autolean.paper import fetch_arxiv

ARXIV_ID = "2506.18616v5"
PDF_SHA256 = "39db363898dfb4a51c0e344a6154f76dd6c3e8768a414d516853e6cdc12dfe2d"
PROFILE_ID = "arxiv-2506.18616v5"
ITEM_COUNT = 25
DECLARATION_COUNT = 33
REVIEW_GUIDANCE = (
    "Separate the executable alias-elaboration audit from follow-up work. "
    "Do not claim signature comparison, mapping grades, paper-form equivalence, "
    "or per-declaration axiom audits."
)


@dataclass(frozen=True)
class DemoWorkspace:
    """Paths owned by one isolated recording."""

    root: Path
    pdf: Path


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _copy_workspace(source: Path, destination: Path) -> None:
    """Create an independent copy-on-write workspace for the recording."""
    command = (
        ["/bin/cp", "-cR", str(source), str(destination)]
        if sys.platform == "darwin"
        else ["cp", "--archive", "--reflink=auto", str(source), str(destination)]
    )
    subprocess.run(command, check=True, capture_output=True, text=True)


def _clear_runtime_state(workspace: Path) -> None:
    """Remove research state that does not belong to this recording."""
    for relative in (
        ".autolean",
        ".codedb",
        "logs",
        "skills",
        "training_data",
        "workspace",
        "AutoLean/Generated",
        "AutoLean/Papers",
    ):
        shutil.rmtree(workspace / relative, ignore_errors=True)
    for path in workspace.glob("AutoLean/Paper_*.lean"):
        path.unlink()
    for relative in (
        ".overnight.pid",
        "overnight.log",
        "results.tsv",
        "AutoLean/UserTheorems.lean",
    ):
        (workspace / relative).unlink(missing_ok=True)


def _initialize_repository(workspace: Path) -> None:
    """Prepare the isolated project for exact source commits."""
    (workspace / ".gitignore").write_text(
        ".autolean/\n"
        ".codedb/\n"
        ".lake/\n"
        "logs/\n"
        "skills/\n"
        "training_data/\n"
        "workspace/\n"
        "AutoLean/Generated/\n"
        "AutoLean/Papers/\n"
        "AutoLean/UserTheorems.lean\n"
        "results.tsv\n"
        "overnight.log\n",
        encoding="utf-8",
    )
    commands = (
        ("init", "-q"),
        ("config", "user.name", "AutoLean Demo"),
        ("config", "user.email", "demo@autolean.invalid"),
        ("config", "commit.gpgsign", "false"),
        ("add", "."),
        ("commit", "-qm", "Demo: Record initial project"),
    )
    for arguments in commands:
        subprocess.run(
            ["git", "-c", "core.fsmonitor=false", *arguments],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )


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
    actual = _sha256(pdf)
    if actual != PDF_SHA256:
        raise SystemExit(f"reviewed PDF SHA-256 differs: expected {PDF_SHA256}, got {actual}")
    return pdf


@contextmanager
def demo_workspace(repository: Path) -> Iterator[DemoWorkspace]:
    """Yield an isolated project backed by the pinned local Lean closure."""
    with tempfile.TemporaryDirectory(prefix="autolean-paper-demo-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        _copy_workspace(repository / "workspace", workspace)
        _clear_runtime_state(workspace)
        _initialize_repository(workspace)
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


def _assert_session(root: Path) -> str:
    sessions = list((root / "workspace" / ".autolean" / "sessions").glob("*.json"))
    records = [json.loads(path.read_text(encoding="utf-8")) for path in sessions]
    completed = [
        record
        for record in records
        if record.get("schema") == "autolean.proof-session.v1"
        and record.get("kind") == "paper"
        and record.get("status") == "completed"
    ]
    if len(completed) != 1 or completed[0].get("remaining_targets") != 0:
        raise SystemExit("demo paper session is not complete")
    return str(completed[0]["id"])


def _assert_export(root: Path) -> None:
    export = root / "paper-artifact"
    manifest = json.loads((export / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != "autolean.project-export.v1":
        raise SystemExit("demo export manifest has an unsupported schema")
    project = export / "project"
    lakefile = project / "lakefile.lean"
    if not lakefile.is_file():
        raise SystemExit("demo export omits lakefile.lean")
    sources = sorted(path for path in project.rglob("*.lean") if path != lakefile)
    if len(sources) != 2 or any(path.name == "UserTheorems.lean" for path in sources):
        raise SystemExit(f"demo export contains an unexpected Lean source set: {sources}")
    evidence = next(path for path in sources if path.name.startswith("Paper_"))
    text = evidence.read_text(encoding="utf-8")
    if "import Mathlib.Probability.ProductMeasure" not in text:
        raise SystemExit("demo evidence does not use the reviewed import closure")
    if re.search(r"(?m)^[ \t]*sorry\b", text):
        raise SystemExit("demo evidence contains a proof placeholder")


def _assert_demo(root: Path) -> None:
    _assert_plan(root)
    _assert_coverage(root)
    _assert_session(root)
    _assert_export(root)


def _file_identity(path: Path) -> dict[str, object]:
    return {
        "name": path.name,
        "sha256": _sha256(path),
        "size": path.stat().st_size,
    }


def _write_demo_manifest(repository: Path, root: Path) -> Path:
    """Bind the recording to its live provider and Lean evidence."""
    plan_path, plan = _single_record(root, "*_plan_*.json", "autolean.paper-plan.v2")
    coverage_path, coverage = _single_record(
        root,
        "*_coverage_*.json",
        "autolean.paper-coverage.v2",
    )
    session = _assert_session(root)
    media = [
        repository / "docs" / "assets" / "autolean-ionescu-tulcea.gif",
        repository / "docs" / "assets" / "autolean-ionescu-tulcea.mp4",
    ]
    record = {
        "arxiv_id": ARXIV_ID,
        "coverage_sha256": _sha256(coverage_path),
        "evidence": coverage["lean_evidence"],
        "export_manifest_sha256": _sha256(root / "paper-artifact" / "manifest.json"),
        "media": [_file_identity(path) for path in media],
        "model": plan["accepted_response_model"],
        "pdf_sha256": PDF_SHA256,
        "plan_artifact_sha256": _sha256(plan_path),
        "plan_sha256": plan["plan_sha256"],
        "plan_trace_sha256": plan["trace_sha256"],
        "recorded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "response_sha256": plan["accepted_response_sha256"],
        "schema": "autolean.demo.v1",
        "session": session,
        "tape_sha256": _sha256(repository / "docs" / "demos" / "ionescu-tulcea.tape"),
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
        "--review-plan",
        "--max-cycles",
        "5",
    ]
    result = subprocess.run(
        command,
        cwd=workspace.root,
        input=f"n\n{REVIEW_GUIDANCE}\ny\n",
        text=True,
        timeout=900,
    )
    if result.returncode != 0:
        raise SystemExit(result.returncode)
    session = _assert_session(workspace.root)
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


def _recording_environment(workspace: DemoWorkspace) -> dict[str, str]:
    """Build the environment VHS hands to the recorded shell.

    A color-suppressing variable inherited from the invoking session would
    strip the CLI theme from the published media.
    """
    environment = os.environ.copy()
    for name in ("NO_COLOR", "CLICOLOR", "CLICOLOR_FORCE"):
        environment.pop(name, None)
    environment.update(
        {
            "AUTOLEAN_DEMO_ROOT": str(workspace.root),
            "AUTOLEAN_DEMO_PDF": str(workspace.pdf),
        }
    )
    return environment


def _record(repository: Path, workspace: DemoWorkspace) -> None:
    """Render the GIF and MP4 from the versioned live VHS tape."""
    if shutil.which("vhs") is None:
        raise SystemExit("vhs is unavailable; enter `nix develop`")
    environment = _recording_environment(workspace)
    subprocess.run(
        ["vhs", "docs/demos/ionescu-tulcea.tape"],
        cwd=repository,
        env=environment,
        check=True,
        timeout=1200,
    )
    _assert_demo(workspace.root)
    for name in ("autolean-ionescu-tulcea.gif", "autolean-ionescu-tulcea.mp4"):
        path = repository / "docs" / "assets" / name
        if not path.is_file() or path.stat().st_size == 0:
            raise SystemExit(f"recording did not produce {path}")
        print(f"{path.relative_to(repository)} ({path.stat().st_size:,} bytes)")
    manifest = _write_demo_manifest(repository, workspace.root)
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
    repository = Path(__file__).resolve().parents[1]
    with demo_workspace(repository) as workspace:
        if options.check:
            _run_check(workspace)
        else:
            _record(repository, workspace)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
