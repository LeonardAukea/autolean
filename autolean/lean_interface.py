"""Interface to Lean 4 — build, diagnostics, and file manipulation."""

from __future__ import annotations

import re
import subprocess
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from rich.console import Console

console = Console()

# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

Severity = Literal["error", "warning", "info"]


@dataclass
class Diagnostic:
    """A single Lean compiler diagnostic."""

    file: str
    line: int
    col: int
    severity: Severity
    message: str

    def __str__(self) -> str:
        return f"{self.file}:{self.line}:{self.col}: {self.severity}: {self.message}"


@dataclass
class BuildResult:
    """Result of a `lake build` invocation."""

    success: bool
    diagnostics: list[Diagnostic] = field(default_factory=list)
    stdout: str = ""
    stderr: str = ""
    duration_seconds: float = 0.0

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "error"]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "warning"]


# ---------------------------------------------------------------------------
# Diagnostic parser
# ---------------------------------------------------------------------------

# Lean outputs diagnostics like:
# ./AutoLean/Sandbox.lean:10:4: error: unsolved goals ...
_DIAG_RE = re.compile(
    r"^(.+?):(\d+):(\d+):\s*(error|warning|info):\s*(.*)",
    re.MULTILINE,
)


def _parse_diagnostics(output: str) -> list[Diagnostic]:
    """Parse Lean compiler output into structured diagnostics.

    Handles:
    1. Standard Lean diagnostics: file:line:col: severity: message
    2. Lake-level errors: 'error:' lines without file location
    3. Multi-line continuations (indented or non-matching lines)
    """
    diags: list[Diagnostic] = []
    # Lean sometimes emits multi-line diagnostics; collect them
    lines = output.split("\n")
    i = 0
    while i < len(lines):
        m = _DIAG_RE.match(lines[i])
        if m:
            file, line_s, col_s, sev, msg = m.groups()
            # Collect continuation lines (indented or non-matching)
            msg_lines = [msg]
            j = i + 1
            while j < len(lines) and not _DIAG_RE.match(lines[j]):
                msg_lines.append(lines[j])
                j += 1
            diags.append(
                Diagnostic(
                    file=file,
                    line=int(line_s),
                    col=int(col_s),
                    severity=sev,  # type: ignore[arg-type]
                    message="\n".join(msg_lines).strip(),
                )
            )
            i = j
        else:
            # Fallback: capture bare "error:" lines from lake/lean without location
            stripped = lines[i].strip()
            if stripped.lower().startswith("error:"):
                msg_lines = [stripped[6:].strip()]
                j = i + 1
                while j < len(lines) and not _DIAG_RE.match(lines[j]) and not lines[j].strip().lower().startswith("error:"):
                    if lines[j].strip():
                        msg_lines.append(lines[j])
                    j += 1
                diags.append(
                    Diagnostic(
                        file="<lake>",
                        line=0,
                        col=0,
                        severity="error",
                        message="\n".join(msg_lines).strip(),
                    )
                )
                i = j
            else:
                i += 1
    return diags


# ---------------------------------------------------------------------------
# Standard tactics for deterministic pre-search
# ---------------------------------------------------------------------------

# Fast tactics — tried first in pre-search (cheap to check)
FAST_TACTICS: list[str] = [
    "rfl",
    "trivial",
    "decide",
    "norm_num",
    "omega",
    "ring",
    "simp",
    "assumption",
    "contradiction",
]

# Full set — includes slower tactics tried when fast pass fails
STANDARD_TACTICS: list[str] = FAST_TACTICS + [
    "simp_all",
    "tauto",
    "aesop",
]

# Compound tactics for slightly harder goals
COMPOUND_TACTICS: list[str] = [
    "intro h; exact h",
    "intro h; contradiction",
    "constructor <;> assumption",
    "constructor <;> rfl",
    "constructor <;> simp",
    "split <;> intro <;> rfl",
    "split <;> intro <;> simp",
    "ext; simp",
    "funext x; simp",
]


# ---------------------------------------------------------------------------
# Lean Project
# ---------------------------------------------------------------------------


@dataclass
class LeanProject:
    """Interface to a Lean 4 project on disk."""

    root: Path

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()
        if not (self.root / "lakefile.lean").exists():
            # Also check for lakefile.toml
            if not (self.root / "lakefile.toml").exists():
                raise FileNotFoundError(
                    f"No lakefile.lean or lakefile.toml in {self.root}"
                )

    # -- Build --------------------------------------------------------------

    def build(self, target: str | None = None, timeout: int = 300) -> BuildResult:
        """Run `lake build` and return structured results."""
        cmd = ["lake", "build"]
        if target:
            cmd.append(target)

        t0 = time.monotonic()
        try:
            result = subprocess.run(
                cmd,
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return BuildResult(
                success=False,
                stdout="",
                stderr=f"Build timed out after {timeout}s",
                duration_seconds=timeout,
            )

        duration = time.monotonic() - t0
        combined = result.stdout + "\n" + result.stderr
        diags = _parse_diagnostics(combined)

        return BuildResult(
            success=result.returncode == 0,
            diagnostics=diags,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_seconds=duration,
        )

    def check_file(self, lean_file: Path, timeout: int = 120) -> BuildResult:
        """Build a single Lean file by its module name.

        Falls back to `lake env lean FILE` if the module isn't registered
        in the lakefile (e.g., newly-generated paper verification files).
        """
        # Convert file path to module name: AutoLean/Sandbox.lean -> AutoLean.Sandbox
        rel = lean_file.resolve().relative_to(self.root)
        module = str(rel).replace("/", ".").removesuffix(".lean")
        result = self.build(target=module, timeout=timeout)

        # Fallback: if lake build doesn't know the module, use lake env lean
        if not result.success and "unknown target" in (result.stderr or ""):
            return self._check_file_via_env(rel, timeout)

        return result

    def _check_file_via_env(self, rel_path: Path, timeout: int = 120) -> BuildResult:
        """Check a file using `lake env lean FILE` (no module registration needed)."""
        cmd = ["lake", "env", "lean", str(rel_path)]
        t0 = time.monotonic()
        try:
            result = subprocess.run(
                cmd,
                cwd=self.root,
                capture_output=True,
                text=True,
                timeout=timeout,
            )
        except subprocess.TimeoutExpired:
            return BuildResult(
                success=False,
                stdout="",
                stderr=f"Build timed out after {timeout}s",
                duration_seconds=timeout,
            )

        duration = time.monotonic() - t0
        combined = result.stdout + "\n" + result.stderr
        diags = _parse_diagnostics(combined)

        # lake env lean returns 0 even on errors; check diagnostics
        has_errors = any(d.severity == "error" for d in diags)
        return BuildResult(
            success=not has_errors,
            diagnostics=diags,
            stdout=result.stdout,
            stderr=result.stderr,
            duration_seconds=duration,
        )

    # -- File operations ----------------------------------------------------

    def lean_files(self) -> list[Path]:
        """Find all .lean files in the project (excluding .lake/)."""
        files = []
        for p in self.root.rglob("*.lean"):
            # Skip lake build cache, lakefile, and nested workspace copies
            parts = p.relative_to(self.root).parts
            if ".lake" in parts or "lake-packages" in parts or "build" in parts:
                continue
            if "workspace" in parts:
                continue
            if p.name == "lakefile.lean":
                continue
            files.append(p)
        return sorted(files)

    def read_file(self, path: Path) -> str:
        """Read a Lean file."""
        return path.read_text(encoding="utf-8")

    def write_file(self, path: Path, content: str) -> None:
        """Write content to a Lean file."""
        path.write_text(content, encoding="utf-8")

    # -- Goal extraction (hole-punch method) --------------------------------

    def get_goal_via_hole_punch(
        self, lean_file: Path, line: int, col: int, timeout: int = 60
    ) -> str | None:
        """Extract proof goal state by temporarily replacing sorry with a typed hole.

        The "hole-punch" method:
        1. Replace `sorry` with `?_` (a typed hole)
        2. Build — Lean emits "unsolved goals" diagnostic for `?_`
        3. Parse the goal state from the diagnostic
        4. Revert the file to original content (always, via try/finally)

        This works because bare `sorry` only produces "declaration uses 'sorry'"
        warnings (no goal state), while `?_` triggers the full goal display.
        """
        original = self.read_file(lean_file)
        lines = original.split("\n")

        if line < 1 or line > len(lines):
            return None

        target_line = lines[line - 1]
        sorry_match = re.search(r"\bsorry\b", target_line)
        if not sorry_match:
            return None

        # Punch: replace sorry with ?_ (typed hole)
        punched_line = (
            target_line[: sorry_match.start()]
            + "?_"
            + target_line[sorry_match.end() :]
        )
        lines[line - 1] = punched_line
        punched_content = "\n".join(lines)

        try:
            self.write_file(lean_file, punched_content)
            result = self.check_file(lean_file, timeout=timeout)

            # Look for "unsolved goals" diagnostic (Lean's response to ?_)
            for diag in result.diagnostics:
                if "unsolved goals" in diag.message.lower():
                    return diag.message

            # Fallback: any error diagnostic near the hole
            for diag in result.diagnostics:
                if abs(diag.line - line) <= 3 and diag.severity == "error":
                    return diag.message

            return None
        finally:
            # ALWAYS revert — even if build crashes or times out
            self.write_file(lean_file, original)

    # -- Deterministic tactic search ------------------------------------------

    def try_standard_tactics(
        self,
        lean_file: Path,
        line: int,
        col: int,
        *,
        timeout_per_tactic: int = 30,
        include_compound: bool = True,
    ) -> str | None:
        """Try standard closing tactics at a sorry position.

        Returns the first tactic that makes the file build cleanly with no
        sorry remaining at the target line. Returns None if nothing works.

        This is the "deterministic pre-search" — runs before any LLM call.
        For trivial goals like `1 + 1 = 2`, this finds `rfl` instantly.
        """
        original = self.read_file(lean_file)

        tactics_to_try = list(STANDARD_TACTICS)
        if include_compound:
            tactics_to_try.extend(COMPOUND_TACTICS)

        for tactic in tactics_to_try:
            try:
                new_content = self.replace_sorry_at(
                    lean_file, line, tactic, original_content=original
                )
                self.write_file(lean_file, new_content)
                result = self.check_file(lean_file, timeout=timeout_per_tactic)

                if result.success:
                    # Verify sorry is actually gone at target line
                    new_lines = new_content.split("\n")
                    if line <= len(new_lines) and "sorry" not in new_lines[line - 1]:
                        return tactic
            except (ValueError, OSError):
                pass
            finally:
                # Always revert
                self.write_file(lean_file, original)

        return None

    def try_tactics_fast(
        self,
        lean_file: Path,
        line: int,
        col: int,
        tactics: list[str],
        *,
        timeout_per_tactic: int = 30,
    ) -> str | None:
        """Try a specific list of tactics at a sorry position.

        Like try_standard_tactics but with a caller-provided list.
        Returns the first working tactic or None.
        """
        original = self.read_file(lean_file)

        for tactic in tactics:
            try:
                new_content = self.replace_sorry_at(
                    lean_file, line, tactic, original_content=original
                )
                self.write_file(lean_file, new_content)
                result = self.check_file(lean_file, timeout=timeout_per_tactic)

                if result.success:
                    new_lines = new_content.split("\n")
                    if line <= len(new_lines) and "sorry" not in new_lines[line - 1]:
                        return tactic
            except (ValueError, OSError):
                pass
            finally:
                self.write_file(lean_file, original)

        return None

    # -- Sorry replacement --------------------------------------------------

    def replace_sorry_at(
        self, path: Path, line: int, replacement: str, original_content: str | None = None
    ) -> str:
        """
        Replace a `sorry` at the given line with the replacement tactic block.

        Returns the new file content.
        """
        content = original_content or self.read_file(path)
        lines = content.split("\n")

        if line < 1 or line > len(lines):
            raise ValueError(f"Line {line} out of range (1..{len(lines)})")

        target_line = lines[line - 1]

        # Find the sorry token and its indentation
        sorry_match = re.search(r"\bsorry\b", target_line)
        if not sorry_match:
            raise ValueError(f"No 'sorry' found at line {line}: {target_line!r}")

        indent = " " * sorry_match.start()

        # Indent the replacement to match the sorry's position.
        # Strip the LLM's indentation and re-indent consistently.
        replacement_lines = replacement.strip().split("\n")
        indented = []
        for i, rline in enumerate(replacement_lines):
            stripped = rline.strip()
            if not stripped:
                indented.append("")
            elif i == 0:
                # First line: placed exactly where sorry was
                indented.append(stripped)
            else:
                # Subsequent lines: same indentation as sorry's column
                indented.append(indent + stripped)

        replacement_block = "\n".join(indented)

        # Replace sorry with the block
        new_line = target_line[: sorry_match.start()] + replacement_block + target_line[sorry_match.end() :]
        lines[line - 1] = new_line

        return "\n".join(lines)
