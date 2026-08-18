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
    from scripts.record_paper_demo import (
        _autolean,
        _clear_runtime_state,
        _copy_workspace,
        _file_identity,
        _initialize_repository,
        _sha256,
        recording_environment,
    )
except ImportError:
    from record_paper_demo import (  # type: ignore[no-redef]
        _autolean,
        _clear_runtime_state,
        _copy_workspace,
        _file_identity,
        _initialize_repository,
        _sha256,
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


@contextmanager
def prove_workspace(repository: Path) -> Iterator[Path]:
    """Yield an isolated project backed by the pinned local Lean closure."""
    with tempfile.TemporaryDirectory(prefix="autolean-prove-demo-") as temporary:
        root = Path(temporary)
        workspace = root / "workspace"
        _copy_workspace(repository / "workspace", workspace)
        _clear_runtime_state(workspace)
        _initialize_repository(workspace)
        # The shared demo .gitignore excludes AutoLean/Generated/ for the
        # paper workflow; this demo commits an accepted generated proof.
        marker = workspace / "AutoLean" / ".gitignore"
        marker.write_text("!Generated/\n", encoding="utf-8")
        subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "add", str(marker)],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )
        subprocess.run(
            ["git", "-c", "core.fsmonitor=false", "commit", "-qm", "Demo: Track generated proofs"],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )
        shutil.copy2(repository / "program.md", root / "program.md")
        yield root


def _assert_session(root: Path) -> dict[str, object]:
    sessions = list((root / "workspace" / ".autolean" / "sessions").glob("*.json"))
    records = [json.loads(path.read_text(encoding="utf-8")) for path in sessions]
    completed = [
        record
        for record in records
        if record.get("schema") == "autolean.proof-session.v1"
        and record.get("kind") == "theorem"
        and record.get("status") == "completed"
    ]
    if len(completed) != 1 or completed[0].get("remaining_targets") != 0:
        raise SystemExit("demo theorem session is not complete")
    return completed[0]


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


def _assert_compiles(root: Path) -> None:
    """Compile the recorded source through the pinned project environment."""
    workspace = root / "workspace"
    generated = _assert_generated(root)
    result = subprocess.run(
        ["lake", "env", "lean", str(generated.relative_to(workspace))],
        cwd=workspace,
        capture_output=True,
        text=True,
        timeout=300,
    )
    if result.returncode != 0:
        diagnostics = "\n".join(part.strip() for part in (result.stdout, result.stderr) if part.strip())
        raise SystemExit(f"pinned Lean rejected the recorded proof:\n{diagnostics}")


def _assert_demo(root: Path) -> None:
    _assert_session(root)
    _assert_generated(root)
    _assert_export(root)
    _assert_compiles(root)


def _playback_speed(tape: Path) -> float:
    """Read the factor by which the tape plays its recording back."""
    for line in tape.read_text(encoding="utf-8").splitlines():
        if line.startswith("Set PlaybackSpeed"):
            return float(line.split()[-1])
    return 1.0


def _session_seconds(session: dict[str, object]) -> float:
    """Return the wall-clock time the recorded proof session took."""
    started = datetime.fromisoformat(str(session["created_at"]))
    ended = datetime.fromisoformat(str(session["updated_at"]))
    return round((ended - started).total_seconds(), 3)


def _write_demo_manifest(repository: Path, root: Path) -> Path:
    """Bind the recording to its live provider and Lean evidence."""
    session = _assert_session(root)
    generated = _assert_generated(root)
    media = [
        repository / "docs" / "assets" / "autolean-pythagorean.gif",
        repository / "docs" / "assets" / "autolean-pythagorean.mp4",
    ]
    tape = repository / "docs" / "demos" / "pythagorean.tape"
    record = {
        "backend": session["backend"],
        "export_manifest_sha256": _sha256(root / EXPORT_DIRECTORY / "manifest.json"),
        "generated_module": generated.name,
        "generated_sha256": _sha256(generated),
        "media": [_file_identity(path) for path in media],
        "model": session["model"],
        "playback_speed": _playback_speed(tape),
        "recorded_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "schema": "autolean.demo.v1",
        "session": session["id"],
        "session_seconds": _session_seconds(session),
        "statement": STATEMENT,
        "tape_sha256": _sha256(tape),
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
    session = _assert_session(root)
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
    if shutil.which("vhs") is None:
        raise SystemExit("vhs is unavailable; enter `nix develop`")
    subprocess.run(
        ["vhs", "docs/demos/pythagorean.tape"],
        cwd=repository,
        env=recording_environment(AUTOLEAN_DEMO_ROOT=str(root)),
        check=True,
        timeout=2400,
    )
    _assert_demo(root)
    for name in ("autolean-pythagorean.gif", "autolean-pythagorean.mp4"):
        path = repository / "docs" / "assets" / name
        if not path.is_file() or path.stat().st_size == 0:
            raise SystemExit(f"recording did not produce {path}")
        print(f"{path.relative_to(repository)} ({path.stat().st_size:,} bytes)")
    manifest = _write_demo_manifest(repository, root)
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
    with prove_workspace(repository) as root:
        if options.check:
            _run_prove(root)
        else:
            _record(repository, root)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
