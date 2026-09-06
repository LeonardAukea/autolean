#!/usr/bin/env python3
"""Record the natural-language Pythagorean prove workflow with a live model."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import tempfile
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import UTC, datetime
from pathlib import Path

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

try:
    from scripts.record_paper_demo import (
        _autolean,
        recording_environment,
    )
except ImportError:
    from record_paper_demo import (  # type: ignore[no-redef]
        _autolean,
        recording_environment,
    )

STATEMENT = "the Pythagorean theorem"
GUIDANCE = (
    "State it geometrically: for points p1 p2 p3 in a Euclidean affine space "
    "with angle p1 p2 p3 = pi / 2, dist p1 p3 ^ 2 = dist p1 p2 ^ 2 + "
    "dist p2 p3 ^ 2. Close with Mathlib's "
    "EuclideanGeometry.dist_sq_eq_dist_sq_add_dist_sq_iff_angle_eq_pi_div_two."
)
EXPORT_TITLE = "A checked Pythagorean theorem"
EXPORT_DIRECTORY = "pythagorean-artifact"
MODEL_PROFILE = "codex"
TAPE_REQUIREMENTS = (
    "autolean",
    "bash",
    "codex",
    "clear",
    "find",
    "git",
    "jq",
    "lake",
    "python3",
    "sort",
)


@contextmanager
def prove_workspace(repository: Path) -> Iterator[Path]:
    """Yield an isolated project backed by the pinned local Lean closure."""
    with tempfile.TemporaryDirectory(prefix="autolean-prove-demo-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        copy_workspace(repository / "workspace", workspace)
        clear_runtime_state(workspace)
        initialize_repository(workspace)
        shutil.copy2(repository / "program.md", root / "program.md")
        yield root


def _assert_generated(root: Path) -> Path:
    generated = root / "workspace" / "AutoLean" / "Generated"
    sources = sorted(generated.glob("*.lean"))
    if len(sources) != 1:
        raise SystemExit(f"demo requires one generated Lean source, found {len(sources)}")
    text = sources[0].read_text(encoding="utf-8")
    if "theorem " not in text:
        raise SystemExit("demo evidence contains no theorem declaration")
    if count_sorries(text):
        raise SystemExit("demo evidence contains a proof placeholder")
    return sources[0]


def _assert_export(root: Path) -> None:
    export = root / EXPORT_DIRECTORY
    manifest = json.loads((export / "manifest.json").read_text(encoding="utf-8"))
    if manifest.get("schema") != "autolean.project-export.v1":
        raise SystemExit("demo export manifest has an unsupported schema")
    project = export / "project"
    if not (project / "lakefile.lean").is_file():
        raise SystemExit("demo export omits lakefile.lean")
    sources = sorted(project.rglob("Generated/*.lean"))
    if len(sources) != 1:
        raise SystemExit(f"demo export contains an unexpected Lean source set: {sources}")
    if count_sorries(sources[0].read_text(encoding="utf-8")):
        raise SystemExit("demo export contains a proof placeholder")
    if sources[0].read_bytes() != _assert_generated(root).read_bytes():
        raise SystemExit("demo export differs from the accepted proof")


def _assert_compiles(root: Path) -> None:
    """Compile the exported proof inside its standalone project."""
    workspace = root / "workspace"
    project = root / EXPORT_DIRECTORY / "project"
    generated = _assert_generated(root)
    if not (project / ".lake").exists():
        copy_workspace(workspace / ".lake", project / ".lake")
    result = subprocess.run(
        ["lake", "env", "lean", str(generated.relative_to(workspace))],
        cwd=project,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        diagnostics = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
        raise SystemExit(f"pinned Lean rejected the recorded proof:\n{diagnostics}")


def _assert_demo(root: Path) -> None:
    completed_session(root, "theorem")
    _assert_generated(root)
    _assert_export(root)
    _assert_compiles(root)


def _session_seconds(session: dict[str, object]) -> float:
    """Return the wall-clock time the recorded proof session took."""
    started = datetime.fromisoformat(str(session["created_at"]))
    ended = datetime.fromisoformat(str(session["updated_at"]))
    return round((ended - started).total_seconds(), 3)


def _write_demo_manifest(
    repository: Path,
    root: Path,
    media: list[dict[str, object]],
    tape_sources: list[dict[str, object]],
    recorder_version: str,
) -> Path:
    """Bind the recording to its live provider and Lean evidence."""
    session = completed_session(root, "theorem")
    generated = _assert_generated(root)
    record = {
        "backend": session["backend"],
        "export_manifest_sha256": sha256(root / EXPORT_DIRECTORY / "manifest.json"),
        "generated_module": generated.name,
        "generated_sha256": sha256(generated),
        "media": media,
        "model": session["model"],
        "playback_speed": playback_speed(repository),
        "recorded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "schema": "autolean.demo.v1",
        "session": session["id"],
        "session_seconds": _session_seconds(session),
        "statement": STATEMENT,
        "tape_sha256": tape_sources[0]["sha256"],
        "tape_sources": tape_sources,
        "vhs_version": recorder_version,
    }
    path = repository / "docs" / "demos" / "pythagorean.json"
    path.write_text(json.dumps(record, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def _run_prove(root: Path) -> None:
    """Run the live workflow without rendering media."""
    result = subprocess.run(
        [
            _autolean(),
            "prove",
            STATEMENT,
            "--model",
            MODEL_PROFILE,
            "--review-plan",
            "--guide",
            GUIDANCE,
            "--max-attempts",
            "5",
        ],
        cwd=root,
        input="y\n",
        text=True,
        timeout=1800,
    )
    if result.returncode != 0:
        raise SystemExit(result.returncode)
    session = completed_session(root, "theorem")
    subprocess.run(
        [
            _autolean(),
            "export",
            EXPORT_DIRECTORY,
            "--session",
            str(session["id"]),
            "--title",
            EXPORT_TITLE,
        ],
        cwd=root,
        check=True,
        timeout=120,
    )
    _assert_demo(root)


def _record(repository: Path, root: Path) -> None:
    """Render the GIF and MP4 from the versioned live VHS tape."""
    tape = repository / "docs" / "demos" / "pythagorean.tape"
    tape_sources = assert_tape_contract(repository, tape, TAPE_REQUIREMENTS)
    recorder_version = vhs_version()
    with staging_outputs(repository, "autolean-pythagorean") as staged:
        render_tape(
            repository,
            tape,
            staged,
            environment=recording_environment(
                AUTOLEAN_DEMO_ROOT=str(root),
                AUTOLEAN_DEMO_WORKSPACE_HELPER=str(repository / "scripts" / "lean_workspace.py"),
            ),
            timeout=3000,
        )
        _assert_demo(root)
        media = validate_media(staged)
        media_paths = publish_outputs(repository, staged)
    for path in media_paths:
        print(f"{path.relative_to(repository)} ({path.stat().st_size:,} bytes)")
    manifest = _write_demo_manifest(
        repository,
        root,
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
    with prove_workspace(repository) as root, preserve_workspace(repository, root):
        if options.check:
            _run_prove(root)
        else:
            _record(repository, root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
