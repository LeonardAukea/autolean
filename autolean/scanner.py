"""Scan Lean files for `sorry` targets and extract context."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
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
    tactic_mode: bool = True  # True if sorry is inside a `by` block
    rel_path: str = ""  # relative path from project root (set by scan_project)

    @property
    def id(self) -> str:
        """Unique identifier for this sorry target.

        Uses rel_path (if set by scan_project) to avoid collisions
        when two directories contain files with the same name.
        """
        path_part = self.rel_path or self.file.name
        return f"{path_part}:{self.line}:{self.decl_name}"

    def __str__(self) -> str:
        return f"{self.id} (col {self.col})"


# ---------------------------------------------------------------------------
# Tactic mode detection
# ---------------------------------------------------------------------------


def _is_tactic_mode(lines: list[str], sorry_line: int) -> bool:
    """Determine if a sorry at `sorry_line` (1-indexed) is in tactic mode.

    Walk backward from the sorry line looking for `by` keyword.
    If we find `by` before hitting the declaration keyword or file start,
    the sorry is in tactic mode. If we find `:=` without a subsequent `by`,
    it is in term mode.
    """
    # Check the sorry line itself — `sorry` might follow `by` on the same line
    target = lines[sorry_line - 1]
    # Check for `by sorry` or `by\n  sorry` pattern
    stripped = target.strip()
    if stripped == "sorry":
        # sorry is on its own line — look backward for `by`
        for j in range(sorry_line - 2, max(sorry_line - 20, -1), -1):
            if j < 0:
                break
            prev = lines[j].rstrip()
            # Check if this line ends with `by` or contains `by` followed by nothing meaningful
            if re.search(r"\bby\s*$", prev):
                return True
            if re.search(r":=\s*$", prev):
                # Found `:=` without `by` — term mode
                return False
            # If we hit a declaration keyword, stop searching
            if re.match(r"\s*(theorem|lemma|def|instance|example|abbrev)\b", prev):
                return False
        return True  # default to tactic mode (most common)

    # Check if `by` appears before `sorry` on the same line
    sorry_idx = target.find("sorry")
    before_sorry = target[:sorry_idx] if sorry_idx >= 0 else ""
    if re.search(r"\bby\b", before_sorry):
        return True

    # Check for `:= sorry` pattern (term mode)
    if re.search(r":=\s*sorry", target):
        return False

    # Default: tactic mode (by far the most common case)
    return True


# ---------------------------------------------------------------------------
# Declaration finder
# ---------------------------------------------------------------------------

# Matches theorem/lemma/def/instance/example declarations
_DECL_RE = re.compile(
    r"^((?:private|protected|noncomputable|unsafe)\s+)*"
    r"(theorem|lemma|def|instance|example|abbrev)\s+(\S+)?",
    re.MULTILINE,
)


def _find_enclosing_decl(lines: list[str], sorry_line: int) -> tuple[str, int]:
    """Find the declaration that encloses a sorry at the given line (1-indexed).

    Returns (declaration_name, declaration_line_number).
    """
    best_name = "<unknown>"
    best_line = 1

    # Build the full text once for finditer
    full_text = "\n".join(lines)

    for m in _DECL_RE.finditer(full_text):
        # Convert character offset to line number
        line_num = full_text[: m.start()].count("\n") + 1
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
    return bool(m and m.start() < col)


def _is_in_string(line: str, col: int) -> bool:
    """Rough check if position is inside a string literal."""
    in_string = False
    i = 0
    while i < min(col, len(line)):
        if line[i] == '"' and (i == 0 or line[i - 1] != "\\"):
            in_string = not in_string
        i += 1
    return in_string


CONTEXT_WINDOW = 40  # lines of context above/below sorry


def scan_file(
    path: Path,
    context_lines: int = CONTEXT_WINDOW,
    project_root: Path | None = None,
) -> list[SorryTarget]:
    """Scan a single Lean file for sorry targets.

    If project_root is provided, SorryTarget.rel_path is populated
    for collision-safe IDs.
    """
    content = path.read_text(encoding="utf-8")
    lines = content.split("\n")
    targets: list[SorryTarget] = []

    # Compute relative path once
    rel_path = ""
    if project_root:
        try:
            rel_path = str(path.relative_to(project_root))
        except ValueError:
            rel_path = path.name

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

            # Determine if sorry is in tactic mode or term mode
            tactic = _is_tactic_mode(lines, line_num)

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
                    tactic_mode=tactic,
                    rel_path=rel_path,
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
        # Skip nested workspace copies (e.g., workspace/workspace/)
        if "workspace" in parts:
            continue
        targets.extend(scan_file(path, project_root=project_root))
    return targets


# Difficulty hints embedded in file/directory names (lower = easier = first)
_DIFFICULTY_KEYWORDS: dict[str, int] = {
    "trivial": 0,
    "basic": 1,
    "easy": 2,
    "simple": 3,
    "medium": 5,
    "hard": 7,
    "advanced": 8,
    "gromov": 9,     # open-problem territory
    "conjecture": 10,
    "veil": 6,       # distributed systems (medium-hard)
}


def _difficulty_score(t: SorryTarget) -> int:
    """Estimate difficulty from path/name heuristics. Lower = easier."""
    name = (t.rel_path + t.decl_name).lower()
    for keyword, score in _DIFFICULTY_KEYWORDS.items():
        if keyword in name:
            return score
    return 5  # default: medium


def prioritize_targets(targets: list[SorryTarget]) -> list[SorryTarget]:
    """Sort targets by estimated difficulty — easy wins first.

    Priority order:
    1. Difficulty score (from file/name heuristics) — easiest first
    2. File sorry count (fewer sorries = more likely to succeed)
    3. Line number within file (top-to-bottom)

    This ensures the agent gets quick wins on Trivial targets before
    spending cycles on Gromov conjectures.
    """
    file_counts: dict[Path, int] = {}
    for t in targets:
        file_counts[t.file] = file_counts.get(t.file, 0) + 1

    return sorted(targets, key=lambda t: (
        _difficulty_score(t),
        file_counts[t.file],
        t.line,
    ))
