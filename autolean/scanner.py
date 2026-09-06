"""Scan Lean files for `sorry` targets and extract context."""

from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from autolean.files import read_source, walk_files

# ---------------------------------------------------------------------------
# Sorry Target
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class SorryTarget:
    """One source-bound `sorry` placeholder."""

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
    source_sha256: str = ""  # source identity observed by the scanner

    def __post_init__(self) -> None:
        if not isinstance(self.file, Path):
            raise ValueError("target file must be a path")
        if any(
            isinstance(value, bool) or not isinstance(value, int)
            for value in (self.line, self.col, self.decl_line)
        ):
            raise ValueError("target positions must be integers")
        if self.line < 1 or self.decl_line < 1 or self.decl_line > self.line:
            raise ValueError("target lines must identify an enclosing declaration")
        if self.col < 0:
            raise ValueError("target column must be non-negative")
        text_fields = (
            self.decl_name,
            self.context_before,
            self.context_after,
            self.rel_path,
            self.qualified_decl_name,
            self.source_sha256,
        )
        if any(not isinstance(value, str) for value in text_fields):
            raise ValueError("target source fields must be text")
        if not self.decl_name:
            raise ValueError("target declaration name must not be empty")
        if not isinstance(self.tactic_mode, bool):
            raise ValueError("target tactic mode must be a boolean")
        if self.rel_path and (
            PurePosixPath(self.rel_path).is_absolute() or ".." in PurePosixPath(self.rel_path).parts
        ):
            raise ValueError("target path must be project-relative")
        if self.source_sha256 and re.fullmatch(r"[0-9a-f]{64}", self.source_sha256) is None:
            raise ValueError("target source SHA-256 must be 64 lowercase hexadecimal characters")

    @property
    def id(self) -> str:
        """Unique identifier for this sorry target.

        Path, line, and column distinguish every placeholder in a scanned
        project, including several placeholders in one declaration line.
        """
        path_part = self.rel_path or self.file.name
        return f"{path_part}:{self.line}:{self.col}:{self.decl_name}"

    @property
    def legacy_id(self) -> str:
        """Return the column-free ID written by proof records before v0.5."""
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
    target = lines[sorry_line - 1]
    stripped = target.strip()
    if stripped == "sorry":
        for j in range(sorry_line - 2, max(sorry_line - 20, -1), -1):
            if j < 0:
                break
            prev = lines[j].rstrip()
            # A trailing `by` means the placeholder is already in tactic mode.
            if re.search(r"\bby\s*$", prev):
                return True
            if re.search(r":=\s*$", prev):
                return False
            if re.match(r"\s*(theorem|lemma|def|instance|example|abbrev)\b", prev):
                return False
        return True  # default to tactic mode (most common)

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
        if line_comment:
            i, line_comment = _mask_line_comment(source, result, i)
            continue
        if block_depth:
            i, block_depth = _mask_block_comment(source, result, i, block_depth)
            continue
        if in_string:
            i, in_string = _mask_string(source, result, i)
            continue
        i, block_depth, in_string, line_comment = _mask_code(source, result, i)

    return "".join(result)


def _mask_line_comment(source: str, result: list[str], index: int) -> tuple[int, bool]:
    """Mask one character inside a line comment."""
    if source[index] == "\n":
        return index + 1, False
    result[index] = " "
    return index + 1, True


def _mask_block_comment(
    source: str,
    result: list[str],
    index: int,
    depth: int,
) -> tuple[int, int]:
    """Mask one token inside a nested block comment."""
    pair = source[index : index + 2]
    if pair == "/-":
        result[index] = result[index + 1] = " "
        return index + 2, depth + 1
    if pair == "-/":
        result[index] = result[index + 1] = " "
        return index + 2, depth - 1
    if source[index] != "\n":
        result[index] = " "
    return index + 1, depth


def _mask_string(source: str, result: list[str], index: int) -> tuple[int, bool]:
    """Mask one token inside a Lean string literal."""
    char = source[index]
    if char == "\\" and index + 1 < len(source):
        result[index] = " "
        if source[index + 1] != "\n":
            result[index + 1] = " "
        return index + 2, True
    if char != "\n":
        result[index] = " "
    return index + 1, char != '"'


def _mask_code(
    source: str,
    result: list[str],
    index: int,
) -> tuple[int, int, bool, bool]:
    """Advance through code or enter one non-code state."""
    pair = source[index : index + 2]
    if pair == "--":
        result[index] = result[index + 1] = " "
        return index + 2, 0, False, True
    if pair == "/-":
        result[index] = result[index + 1] = " "
        return index + 2, 1, False, False
    if source[index] == '"':
        result[index] = " "
        return index + 1, 0, True, False
    return index + 1, 0, False, False


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
    content = read_source(path)
    source_sha256 = hashlib.sha256(content.encode()).hexdigest()
    lines = content.split("\n")
    masked_lines = _mask_lean_noncode(content).split("\n")
    targets: list[SorryTarget] = []

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

            tactic = _is_tactic_mode(masked_lines, line_num)

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
                    source_sha256=source_sha256,
                )
            )

    return targets


_EXCLUDED_SOURCE_DIRECTORIES = frozenset(
    {".lake", "lake-packages", "build", "workspace", ".git", ".autolean", ".codedb"}
)


def lean_source_files(project_root: Path) -> list[Path]:
    """Enumerate project source in path order, pruning caches before descent.

    An unreadable source directory fails the scan: a partial enumeration
    cannot establish that a project has no remaining proof targets.
    """

    return sorted(
        path
        for path in walk_files(project_root, excluded=_EXCLUDED_SOURCE_DIRECTORIES)
        if path.suffix == ".lean" and path.name != "lakefile.lean"
    )


def scan_project(project_root: Path) -> list[SorryTarget]:
    """Scan project source for every sorry target."""
    targets: list[SorryTarget] = []
    for path in lean_source_files(project_root):
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
    """Sort targets easiest-first, so quick wins precede hard conjectures."""
    file_counts: dict[Path, int] = {}
    for t in targets:
        file_counts[t.file] = file_counts.get(t.file, 0) + 1

    return sorted(
        targets,
        key=lambda t: (
            difficulty_score(t),
            file_counts[t.file],
            t.line,
            t.col,
        ),
    )
