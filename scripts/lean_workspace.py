"""Isolated pinned Lean projects for executable examples and recordings."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path


def copy_workspace(source: Path, destination: Path) -> None:
    """Create an independent copy-on-write workspace for the recording."""
    if destination.exists():
        raise FileExistsError(f"workspace destination already exists: {destination}")
    command = (
        ["/bin/cp", "-cR", str(source), str(destination)]
        if sys.platform == "darwin"
        else ["cp", "--archive", "--reflink=auto", str(source), str(destination)]
    )
    subprocess.run(command, check=True, capture_output=True, text=True)


def clear_runtime_state(workspace: Path) -> None:
    """Remove research state that does not belong to this recording."""
    for relative in (
        ".git",
        ".autolean",
        ".codedb",
        "logs",
        "skills",
        "training_data",
        "workspace",
        "AutoLean/Generated",
        "AutoLean/Papers",
    ):
        path = workspace / relative
        if path.is_symlink() or path.is_file():
            path.unlink()
        elif path.exists():
            shutil.rmtree(path)
    for pattern in (
        "AutoLean/Paper*.lean",
        "AutoLean/Challenge_*.lean",
        "AutoLean/Lib*.lean",
    ):
        for path in workspace.glob(pattern):
            path.unlink()
    for relative in (
        ".overnight.pid",
        "overnight.log",
        "results.tsv",
        "AutoLean/UserTheorems.lean",
    ):
        (workspace / relative).unlink(missing_ok=True)


def initialize_repository(workspace: Path) -> None:
    """Prepare the isolated project for exact source commits."""
    (workspace / ".gitignore").write_text(
        ".autolean/\n"
        ".codedb/\n"
        ".lake/\n"
        "logs/\n"
        "skills/\n"
        "training_data/\n"
        "workspace/\n"
        "AutoLean/Papers/\n"
        "AutoLean/Paper*.lean\n"
        "AutoLean/Challenge_*.lean\n"
        "AutoLean/Lib*.lean\n"
        "AutoLean/UserTheorems.lean\n"
        "results.tsv\n"
        "overnight.log\n",
        encoding="utf-8",
    )
    commands = (
        ("init", "-q"),
        ("config", "user.name", "AutoLean Smoke"),
        ("config", "user.email", "smoke@autolean.invalid"),
        ("config", "commit.gpgsign", "false"),
        ("add", "."),
        ("commit", "-qm", "Workspace: Record initial source"),
    )
    for arguments in commands:
        subprocess.run(
            ["git", "-c", "core.fsmonitor=false", *arguments],
            cwd=workspace,
            check=True,
            capture_output=True,
            text=True,
        )


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path)
    parser.add_argument("destination", type=Path)
    options = parser.parse_args()
    copy_workspace(options.source, options.destination)
