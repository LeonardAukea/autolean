"""Scan Lean files for `sorry` targets and extract context."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path


# ---------------------------------------------------------------------------
# Sorry Target
# ---------------------------------------------------------------------------


@dataclass
class SorryTarget:
    """A single sorry placeholder found in a Lean file."""

    file: Path
    line: int  # 1-indexed
    col: int  # 0-indexed
    decl_name: str  # enclosing declaration name
    decl_line: int  # where the declaration starts
    context_before: str  # lines above for LLM context
    context_after: str  # lines below for LLM context

    @property
    def id(self) -> str:
        """Unique identifier for this sorry target."""
        rel = self.file.name
        return f"{rel}:{self.line}:{self.decl_name}"

    def __str__(self) -> str:
        return f"{self.id} (col {self.col})"


# ---------------------------------------------------------------------------
# Declaration finder
# ---------------------------------------------------------------------------

# Matches theorem/lemma/def/instance/example declarations
_DECL_RE = re.compile(
    r"^(private\s+|protected\s+|noncomputable\s+|unsafe\s+)*"
    r"(theorem|lemma|def|instance|example|abbrev)\s+(\S+)?",
    re.MULTILINE,
)


def _find_enclosing_decl(lines: list[str], sorry_line: int) -> tuple[str, int]:
    """Find the declaration that encloses a sorry at the given line (1-indexed)."""
    best_name = "<unknown>"
    best_line = 1

    for m in _DECL_RE.finditer("\n".join(lines)):
        # Convert character offset to line number
        line_num = "\n".join(lines)[: m.start()].count("\n") + 1
        if line_num <= sorry_line:
            name = m.group(3) or m.group(2)  # example has no name
            best_name = name.split(":")[0].split("(")[0].strip()  # clean up
            best_line = line_num
        else:
            break

    return best_name, best_line


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

# Match sorry token (not in comments or strings)
_SORRY_RE = re.compile(r"\bsorry\b")

# Simple check for single-line comments
_COMMENT_RE = re.compile(r"--.*$")


def _is_in_comment(line: str, col: int) -> bool:
    """Check if position is inside a single-line comment."""
    m = _COMMENT_RE.search(line)
    if m and m.start() < col:
        return True
    return False


def _is_in_string(line: str, col: int) -> bool:
    """Rough check if position is inside a string literal."""
    in_string = False
    i = 0
    while i < min(col, len(line)):
        if line[i] == '"' and (i == 0 or line[i - 1] != "\\"):
            in_string = not in_string
        i += 1
    return in_string


def _is_in_block_comment(lines: list[str], line_idx: int, col: int) -> bool:
    """Rough check for /- -/ block comments."""
    text = "\n".join(lines[: line_idx + 1])
    # Count open /- and close -/ before this position
    opens = len(re.findall(r"/-", text[:col + sum(len(l) + 1 for l in lines[:line_idx])]))
    closes = len(re.findall(r"-/", text[:col + sum(len(l) + 1 for l in lines[:line_idx])]))
    return opens > closes


CONTEXT_WINDOW = 40  # lines of context above/below sorry


def scan_file(path: Path, context_lines: int = CONTEXT_WINDOW) -> list[SorryTarget]:
    """Scan a single Lean file for sorry targets."""
    content = path.read_text(encoding="utf-8")
    lines = content.split("\n")
    targets: list[SorryTarget] = []

    for i, line in enumerate(lines):
        for m in _SORRY_RE.finditer(line):
            col = m.start()

            # Skip sorry in comments and strings
            if _is_in_comment(line, col):
                continue
            if _is_in_string(line, col):
                continue

            line_num = i + 1  # 1-indexed
            decl_name, decl_line = _find_enclosing_decl(lines, line_num)

            # Extract context window
            ctx_start = max(0, decl_line - 1)  # from declaration start
            ctx_end = min(len(lines), i + context_lines + 1)

            context_before = "\n".join(lines[ctx_start:i])
            context_after = "\n".join(lines[i + 1 : ctx_end])

            targets.append(
                SorryTarget(
                    file=path,
                    line=line_num,
                    col=col,
                    decl_name=decl_name,
                    decl_line=decl_line,
                    context_before=context_before,
                    context_after=context_after,
                )
            )

    return targets


def scan_project(project_root: Path) -> list[SorryTarget]:
    """Scan all .lean files in a project for sorry targets."""
    targets: list[SorryTarget] = []
    for path in sorted(project_root.rglob("*.lean")):
        parts = path.relative_to(project_root).parts
        if ".lake" in parts or "lake-packages" in parts or "build" in parts:
            continue
        if path.name == "lakefile.lean":
            continue
        targets.extend(scan_file(path))
    return targets


def prioritize_targets(targets: list[SorryTarget]) -> list[SorryTarget]:
    """
    Sort targets by priority — files with fewer sorries first (low-hanging fruit).
    Within a file, sort by line number (top-to-bottom).
    """
    # Count sorries per file
    file_counts: dict[Path, int] = {}
    for t in targets:
        file_counts[t.file] = file_counts.get(t.file, 0) + 1

    return sorted(targets, key=lambda t: (file_counts[t.file], t.line))
