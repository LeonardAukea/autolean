"""Scan Lean files for `sorry` targets and extract context."""

from __future__ import annotations

import hashlib
import re
from bisect import bisect_right
from collections.abc import Iterator
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


def _is_tactic_mode(lines: list[str], sorry_line: int, sorry_col: int | None = None) -> bool:
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

    sorry_idx = (
        next((match.start() for match in _sorry_matches(target)), -1) if sorry_col is None else sorry_col
    )
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
# Character classes follow Lean's Init.Meta.Defs identifier predicates.
_IDENT_FIRST = (
    r"A-Za-z_\u00c0-\u00d6\u00d8-\u00f6\u00f8-\u017f"
    r"\u0391-\u039f\u03a1\u03a4-\u03a9\u03b1-\u03ba\u03bc-\u03fb"
    r"\u1f00-\u1ffe\u2100-\u214f\U0001d49c-\U0001d59f"
)
_IDENT_REST = _IDENT_FIRST + r"0-9'!?\u2080-\u2089\u2090-\u209c\u1d62-\u1d6a\u2c7c"
_IDENT_PART = rf"(?:«[^»]*»|[{_IDENT_FIRST}][{_IDENT_REST}]*)"
_IDENT = rf"{_IDENT_PART}(?:\.{_IDENT_PART})*"
_DECL_RE = re.compile(
    r"^\s*(?:@\[[^\]\r\n]*\]\s*)*"
    r"(?:(?:private|protected|noncomputable|unsafe|partial|local)\s+)*"
    r"(theorem|lemma|def|instance|example|abbrev|opaque)\b"
    rf"(?:\s+({_IDENT}))?"
)
_NAMESPACE_RE = re.compile(rf"^\s*namespace(?:\s+({_IDENT}))?\s*$")
_SECTION_RE = re.compile(rf"^\s*section(?:\s+{_IDENT})?\s*$")
_END_RE = re.compile(rf"^\s*end(?:\s+{_IDENT})?\s*$")


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

_SORRY_RE = re.compile(rf"«[^»]*(?:»|$)|(?<![{_IDENT_REST}.`])sorry(?![{_IDENT_REST}.])")
_CHAR_LITERAL = r"'(?:[^'\\]|\\(?:x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}|[\\\"'rnt]))'"
_NONCODE_START_RE = re.compile(rf'«|--|/-|(?<![{_IDENT_REST}.`])(?:r#*"|{_CHAR_LITERAL})|"')
_BLOCK_DELIMITER_RE = re.compile(r"/-|-/")
_STRING_DELIMITER_RE = re.compile(r'\\[\s\S]|"')
_NON_NEWLINE_RE = re.compile(r"[^\n]+")


def _mask_lean_noncode(source: str) -> str:
    """Blank comments and value literals; preserve identifiers and positions.

    Lean block comments nest. Keeping every source offset stable lets the
    scanner use positions from the masked text to edit the original bytes.
    """
    spans: list[str] = []
    cursor = 0
    while match := _NONCODE_START_RE.search(source, cursor):
        start = match.start()
        end = len(source)
        token = match.group()
        if token == "«":
            closing = source.find("»", match.end())
            if closing >= 0:
                end = closing + 1
        elif token.startswith("r"):
            closing_marker = '"' + "#" * (len(token) - 2)
            closing = source.find(closing_marker, match.end())
            if closing >= 0:
                end = closing + len(closing_marker)
        elif token.startswith("'"):
            end = match.end()
        elif token == "--":
            newline = source.find("\n", match.end())
            if newline >= 0:
                end = newline
        elif token == "/-":
            depth = 1
            for delimiter in _BLOCK_DELIMITER_RE.finditer(source, match.end()):
                depth += 1 if delimiter.group() == "/-" else -1
                if depth == 0:
                    end = delimiter.end()
                    break
        else:
            for delimiter in _STRING_DELIMITER_RE.finditer(source, match.end()):
                if delimiter.group() == '"':
                    end = delimiter.end()
                    break
        spans.append(source[cursor:start])
        span = source[start:end]
        spans.append(
            span if token == "«" else _NON_NEWLINE_RE.sub(lambda part: " " * len(part.group()), span)
        )
        cursor = end
    spans.append(source[cursor:])
    return "".join(spans)


def _sorry_matches(masked: str) -> Iterator[re.Match[str]]:
    return (match for match in _SORRY_RE.finditer(masked) if match.group() == "sorry")


def count_sorries(source: str) -> int:
    """Count bare placeholders outside Lean comments and value literals."""
    return sum(1 for _ in _sorry_matches(_mask_lean_noncode(source)))


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
    masked = _mask_lean_noncode(content)
    masked_lines = masked.split("\n")
    line_starts = [0, *(match.end() for match in re.finditer("\n", masked))]
    targets: list[SorryTarget] = []

    rel_path = ""
    if project_root:
        try:
            rel_path = str(path.relative_to(project_root))
        except ValueError:
            rel_path = path.name

    for match in _sorry_matches(masked):
        line_num = bisect_right(line_starts, match.start())
        col = match.start() - line_starts[line_num - 1]
        decl_name, qualified_name, decl_line = _find_enclosing_decl_details(masked_lines, line_num)
        tactic = _is_tactic_mode(masked_lines, line_num, col)
        ctx_start = max(0, decl_line - 1)
        ctx_end = min(len(lines), line_num + context_lines)

        targets.append(
            SorryTarget(
                file=path,
                line=line_num,
                col=col,
                decl_name=decl_name,
                decl_line=decl_line,
                context_before="\n".join(lines[ctx_start : line_num - 1]),
                context_after="\n".join(lines[line_num:ctx_end]),
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
