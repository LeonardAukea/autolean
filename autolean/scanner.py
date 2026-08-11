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
    tactic_mode: bool = True  # True if sorry is inside a `by` block
    rel_path: str = ""  # relative path from project root (set by scan_project)
    qualified_decl_name: str = ""  # source-qualified name used for axiom audits

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
            # A trailing `by` means the placeholder is already in tactic mode.
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

    # `:= sorry` is term mode; everything else defaults to tactic mode,
    # which is by far the most common shape.
    return not re.search(r":=\s*sorry", target)


# ---------------------------------------------------------------------------
# Declaration finder
# ---------------------------------------------------------------------------

# This bounded scanner discovers common source targets. Lean's parser validates
# the declaration name and source range before any generated proof is accepted.
_DECL_RE = re.compile(
    r"^\s*(?:@\[[^\]\r\n]*\]\s*)*"
    r"(?:(?:private|protected|noncomputable|unsafe|partial|local)\s+)*"
    r"(theorem|lemma|def|instance|example|abbrev|opaque)\b"
    r"(?:\s+(«[^»]+»|[^\s:({\[]+))?"
)
_NAMESPACE_RE = re.compile(r"^\s*namespace(?:\s+([^\s]+))?\s*$")
_SECTION_RE = re.compile(r"^\s*section(?:\s+[^\s]+)?\s*$")
_END_RE = re.compile(r"^\s*end(?:\s+[^\s]+)?\s*$")


def _find_enclosing_decl(lines: list[str], sorry_line: int) -> tuple[str, int]:
    """Find the declaration that encloses a sorry at the given line (1-indexed).

    Returns (declaration_name, declaration_line_number).
    """
    masked = _mask_lean_noncode("\n".join(lines)).split("\n")
    name, _qualified, line = _find_enclosing_decl_details(masked, sorry_line)
    return name, line


def _find_enclosing_decl_details(
    masked_lines: list[str],
    sorry_line: int,
) -> tuple[str, str, int]:
    """Return local name, fully qualified name, and declaration line."""
    scopes: list[tuple[str, str]] = []
    best_name = "<unknown>"
    best_qualified = ""
    best_line = 1

    for line_num, line in enumerate(masked_lines[:sorry_line], start=1):
        namespace = _NAMESPACE_RE.match(line)
        if namespace:
            scopes.append(("namespace", namespace.group(1) or ""))
            continue
        if _SECTION_RE.match(line):
            scopes.append(("section", ""))
            continue
        if _END_RE.match(line):
            if scopes:
                scopes.pop()
            continue

        declaration = _DECL_RE.match(line)
        if declaration is None:
            continue
        kind, parsed_name = declaration.groups()
        if kind == "example" or not parsed_name:
            best_name = f"<{kind}@{line_num}>"
            best_qualified = ""
        else:
            best_name = parsed_name
            namespaces = [name for scope, name in scopes if scope == "namespace" and name]
            if parsed_name.startswith("_root_."):
                best_qualified = parsed_name.removeprefix("_root_.")
            else:
                best_qualified = ".".join([*namespaces, parsed_name])
        best_line = line_num

    return best_name, best_qualified, best_line


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

# Match a sorry token in masked Lean source.
_SORRY_RE = re.compile(r"\bsorry\b")


def _mask_lean_noncode(source: str) -> str:
    """Blank comments and strings while preserving offsets and newlines.

    Lean block comments nest. Keeping every source offset stable lets the
    scanner use positions from the masked text to edit the original bytes.
    """
    result = list(source)
    i = 0
    block_depth = 0
    in_string = False
    line_comment = False

    while i < len(source):
        char = source[i]
        pair = source[i : i + 2]

        if line_comment:
            if char == "\n":
                line_comment = False
            else:
                result[i] = " "
            i += 1
            continue

        if block_depth:
            if pair == "/-":
                result[i] = result[i + 1] = " "
                block_depth += 1
                i += 2
                continue
            if pair == "-/":
                result[i] = result[i + 1] = " "
                block_depth -= 1
                i += 2
                continue
            if char != "\n":
                result[i] = " "
            i += 1
            continue

        if in_string:
            if char == "\\" and i + 1 < len(source):
                result[i] = " "
                if source[i + 1] != "\n":
                    result[i + 1] = " "
                i += 2
                continue
            if char == '"':
                in_string = False
            if char != "\n":
                result[i] = " "
            i += 1
            continue

        if pair == "--":
            result[i] = result[i + 1] = " "
            line_comment = True
            i += 2
            continue
        if pair == "/-":
            result[i] = result[i + 1] = " "
            block_depth = 1
            i += 2
            continue
        if char == '"':
            result[i] = " "
            in_string = True
        i += 1

    return "".join(result)


def count_sorries(source: str) -> int:
    """Count actual placeholders outside Lean comments and strings."""
    return len(_SORRY_RE.findall(_mask_lean_noncode(source)))


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
    masked_lines = _mask_lean_noncode(content).split("\n")
    targets: list[SorryTarget] = []

    # Compute relative path once
    rel_path = ""
    if project_root:
        try:
            rel_path = str(path.relative_to(project_root))
        except ValueError:
            rel_path = path.name

    for i, masked_line in enumerate(masked_lines):
        for m in _SORRY_RE.finditer(masked_line):
            col = m.start()

            line_num = i + 1  # 1-indexed
            decl_name, qualified_name, decl_line = _find_enclosing_decl_details(
                masked_lines,
                line_num,
            )

            # Determine if sorry is in tactic mode or term mode
            tactic = _is_tactic_mode(masked_lines, line_num)

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
                    qualified_decl_name=qualified_name,
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
    "gromov": 9,  # open-problem territory
    "conjecture": 10,
    "veil": 6,  # distributed systems (medium-hard)
}


def difficulty_score(t: SorryTarget) -> int:
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

    return sorted(
        targets,
        key=lambda t: (
            difficulty_score(t),
            file_counts[t.file],
            t.line,
        ),
    )
