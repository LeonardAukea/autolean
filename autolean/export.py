"""Portable Lean project and companion-paper exports."""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import Any

EXPORT_SCHEMA = "autolean.project-export.v1"
_PROJECT_FILES = {"lake-manifest.json", "lakefile.lean", "lakefile.toml", "lean-toolchain"}
_EXCLUDED_PARTS = {
    ".autolean",
    ".git",
    ".lake",
    "build",
    "lake-packages",
    "logs",
    "training_data",
}
_LATEX_SPECIAL = re.compile(r"([#$%&_{}])")


class ExportError(ValueError):
    """A portable project export cannot be created safely."""


@dataclass(frozen=True)
class ExportResult:
    """Identity and paths for one completed export."""

    path: Path
    manifest_sha256: str
    source_count: int


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return digest.hexdigest()


def _source_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for path in root.rglob("*"):
        relative = path.relative_to(root)
        if any(part in _EXCLUDED_PARTS for part in relative.parts):
            continue
        if path.is_symlink():
            raise ExportError(f"project export does not follow symbolic links: {relative}")
        if path.is_file() and (path.suffix == ".lean" or path.name in _PROJECT_FILES):
            files.append(path)
    return sorted(files, key=lambda path: path.relative_to(root).as_posix())


def _latex_escape(text: str) -> str:
    escaped = _LATEX_SPECIAL.sub(r"\\\1", text)
    return escaped.replace("~", r"\textasciitilde{}")


def _paper_source(title: str, lean_files: list[str], environment_sha256: str) -> str:
    sections = []
    for path in lean_files:
        sections.append(
            "\\subsection*{\\texttt{\\detokenize{" + path + "}}}\n"
            "\\VerbatimInput[fontsize=\\scriptsize]{../project/" + path + "}\n"
        )
    environment = environment_sha256 or "not recorded"
    return (
        "\\documentclass[11pt]{article}\n"
        "\\usepackage{fontspec}\n"
        "\\usepackage{ucharclasses}\n"
        "\\setmonofont{DejaVuSansMono.ttf}[\n"
        "  BoldFont=DejaVuSansMono-Bold.ttf,\n"
        "  ItalicFont=DejaVuSansMono-Oblique.ttf,\n"
        "  Scale=MatchLowercase\n"
        "]\n"
        "\\newfontfamily\\mathfallbackfont{DejaVuSans.ttf}\n"
        "\\setTransitionsForMathematics{\\mathfallbackfont}{\\ttfamily}\n"
        "\\usepackage{amsmath,amsthm}\n"
        "\\usepackage[margin=1in]{geometry}\n"
        "\\usepackage{fancyvrb}\n"
        "\\usepackage[hidelinks]{hyperref}\n"
        f"\\title{{{_latex_escape(title)}}}\n"
        "\\author{AutoLean proof artifact}\n"
        "\\date{}\n"
        "\\begin{document}\n"
        "\\maketitle\n"
        "\\section*{Verification contract}\n"
        "The mathematical declarations in this artifact are checked by the pinned "
        "Lean toolchain. The accompanying manifest binds every source file to its "
        "SHA-256 digest.\\par\n"
        f"\\noindent Environment SHA-256: \\texttt{{{environment}}}\n"
        "\\section*{Lean source}\n" + "\n".join(sections) + "\\end{document}\n"
    )


def _readme(title: str, environment_sha256: str) -> str:
    environment = environment_sha256 or "not recorded"
    return (
        f"# {title}\n\n"
        "This directory is a standalone Lean project exported by AutoLean.\n\n"
        "Build the formal artifact with:\n\n"
        "```console\n"
        "cd project\n"
        "lake build\n"
        "```\n\n"
        "Build the companion paper with a TeX distribution that provides "
        "`fancyvrb`:\n\n"
        "```console\n"
        "cd paper\n"
        "latexmk -xelatex main.tex\n"
        "```\n\n"
        f"Proof environment SHA-256: `{environment}`.\n"
        "See `manifest.json` for exact source identities.\n"
    )


def export_project(
    project_root: Path,
    output: Path,
    *,
    title: str,
    environment_sha256: str = "",
    session: dict[str, Any] | None = None,
) -> ExportResult:
    """Create one atomic, source-only Lean and LaTeX artifact."""
    root = project_root.resolve()
    destination = output.resolve()
    try:
        destination.relative_to(root)
    except ValueError:
        pass
    else:
        raise ExportError("export destination must be outside the Lean project")
    if destination.exists():
        raise ExportError(f"export destination already exists: {destination}")
    if not (root / "lean-toolchain").is_file():
        raise ExportError(f"Lean project has no lean-toolchain: {root}")
    if not ((root / "lakefile.lean").is_file() or (root / "lakefile.toml").is_file()):
        raise ExportError(f"Lean project has no Lake configuration: {root}")

    destination.parent.mkdir(parents=True, exist_ok=True)
    staging = Path(tempfile.mkdtemp(prefix=f".{destination.name}.", dir=destination.parent))
    try:
        project_output = staging / "project"
        sources = _source_files(root)
        for source in sources:
            relative = source.relative_to(root)
            target = project_output / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)

        lean_files = [
            source.relative_to(root).as_posix()
            for source in sources
            if source.suffix == ".lean" and source.name not in _PROJECT_FILES
        ]
        paper = staging / "paper" / "main.tex"
        paper.parent.mkdir(parents=True)
        paper.write_text(
            _paper_source(title, lean_files, environment_sha256),
            encoding="utf-8",
        )
        (staging / "README.md").write_text(
            _readme(title, environment_sha256),
            encoding="utf-8",
        )
        if session is not None:
            (staging / "session.json").write_text(
                json.dumps(session, indent=2, sort_keys=True) + "\n",
                encoding="utf-8",
            )

        artifact_files = sorted(
            (path for path in staging.rglob("*") if path.is_file() and path.name != "manifest.json"),
            key=lambda path: path.relative_to(staging).as_posix(),
        )
        manifest = {
            "environment_sha256": environment_sha256,
            "files": [
                {
                    "path": path.relative_to(staging).as_posix(),
                    "sha256": _sha256(path),
                    "size": path.stat().st_size,
                }
                for path in artifact_files
            ],
            "schema": EXPORT_SCHEMA,
            "title": title,
        }
        manifest_path = staging / "manifest.json"
        manifest_path.write_text(
            json.dumps(manifest, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        manifest_sha256 = _sha256(manifest_path)
        os.replace(staging, destination)
    except Exception:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    return ExportResult(destination, manifest_sha256, len(lean_files))
