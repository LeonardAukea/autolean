"""Bounded local-project search through the CodeDB command-line client."""

from __future__ import annotations

import hashlib
import os
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path

_MAX_CONTEXT_CHARS = 6000
_MAX_QUERY_CHARS = 160
_IDENTIFIER = re.compile(r"[A-Za-z_][A-Za-z0-9_'.]{2,}")
_COMMON_IDENTIFIERS = frozenset(
    {
        "case",
        "false",
        "forall",
        "goal",
        "have",
        "lean",
        "sorry",
        "theorem",
        "true",
        "type",
    }
)


@dataclass(frozen=True)
class IndexedCodeContext:
    """One advisory CodeDB result supplied to a proof request."""

    tool: str
    queries: tuple[str, ...]
    text: str = ""
    unavailable_reason: str = ""

    @property
    def sha256(self) -> str:
        """Return the identity of the exact rendered context."""
        return hashlib.sha256(self.render().encode()).hexdigest()

    def render(self) -> str:
        """Render bounded local-code evidence for a model prompt."""
        lines = ["## Indexed local project context (advisory)", f"tool: {self.tool}"]
        if self.queries:
            lines.append("queries: " + ", ".join(self.queries))
        if self.unavailable_reason:
            lines.append(f"unavailable: {self.unavailable_reason}")
        if self.text:
            lines.extend(("results:", self.text))
        lines.append("Lean elaboration determines whether any result applies.")
        return "\n".join(lines)[:_MAX_CONTEXT_CHARS]


class CodeDBSearchProvider:
    """Use CodeDB's indexed text search without granting edit capability."""

    def __init__(self, command: str | None = None) -> None:
        configured = command or os.environ.get("AUTOLEAN_CODEDB")
        self._command = _resolve_command(configured, "codedb")
        self._identity: str | None = None

    @property
    def available(self) -> bool:
        """Return whether a CodeDB executable is available."""
        return self._command is not None

    def identity(self) -> str:
        """Return a bounded version string for diagnostics and provenance."""
        if self._identity is not None:
            return self._identity
        if self._command is None:
            self._identity = "codedb/unavailable"
            return self._identity
        try:
            result = subprocess.run(
                [self._command, "--version"],
                check=False,
                capture_output=True,
                text=True,
                timeout=3,
                env=_tool_environment(),
            )
        except (OSError, subprocess.SubprocessError):
            self._identity = "codedb/unavailable"
            return self._identity
        version = " ".join((result.stdout or result.stderr).split())[:120]
        self._identity = version or "codedb/unknown"
        return self._identity

    def search(self, project_root: Path, goal: str, declaration: str) -> IndexedCodeContext:
        """Search local source for a few goal-bearing identifiers."""
        queries = _queries(goal, declaration)
        identity = self.identity()
        if self._command is None:
            return IndexedCodeContext(identity, queries, unavailable_reason="executable not found")
        if not queries:
            return IndexedCodeContext(identity, queries, unavailable_reason="no stable query terms")

        chunks: list[str] = []
        for query in queries:
            try:
                result = subprocess.run(
                    [self._command, "search", "--max-results", "4", query],
                    cwd=project_root,
                    check=False,
                    capture_output=True,
                    text=True,
                    timeout=8,
                    env=_tool_environment(),
                )
            except (OSError, subprocess.SubprocessError) as error:
                return IndexedCodeContext(
                    identity,
                    queries,
                    unavailable_reason=f"search failed: {type(error).__name__}",
                )
            output = result.stdout.strip()
            if result.returncode == 0 and output:
                chunks.append(f"[{query}]\n{output}")
        if not chunks:
            return IndexedCodeContext(identity, queries, unavailable_reason="no local matches")
        return IndexedCodeContext(identity, queries, text="\n\n".join(chunks))


def _queries(goal: str, declaration: str) -> tuple[str, ...]:
    candidates = [declaration, *_IDENTIFIER.findall(goal)]
    queries: list[str] = []
    seen: set[str] = set()
    for candidate in candidates:
        candidate = candidate.strip(".'")[:_MAX_QUERY_CHARS]
        folded = candidate.casefold()
        if len(candidate) < 3 or folded in _COMMON_IDENTIFIERS or folded in seen:
            continue
        seen.add(folded)
        queries.append(candidate)
        if len(queries) == 3:
            break
    return tuple(queries)


def _resolve_command(configured: str | None, fallback: str) -> str | None:
    if configured is None:
        return shutil.which(fallback)
    path = Path(configured)
    if not path.is_absolute() or not path.is_file() or not os.access(path, os.X_OK):
        return None
    return str(path)


def _tool_environment() -> dict[str, str]:
    environment = {
        "CODEDB_NO_TELEMETRY": "1",
        "PATH": os.environ.get("PATH", ""),
    }
    for name in ("LANG", "LC_ALL", "TMPDIR"):
        if value := os.environ.get(name):
            environment[name] = value
    return environment
