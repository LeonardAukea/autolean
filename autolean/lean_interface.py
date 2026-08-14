"""Interface to Lean 4 — build, diagnostics, and file manipulation."""

from __future__ import annotations

import os
import platform
import re
import secrets
import shutil
import subprocess
import tempfile
import textwrap
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from autolean.provenance import (
    EnvironmentFingerprint,
    ProofEnvironment,
    ProofEnvironmentError,
    capture_proof_environment,
    environment_fingerprint,
)
from autolean.scanner import _mask_lean_noncode, count_sorries

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
    axioms: tuple[str, ...] | None = None
    #: Lean was still running when its budget ran out, so the result carries
    #: no verdict about the proof — only that elaborating it cost too much.
    timed_out: bool = False

    @property
    def errors(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "error"]

    @property
    def warnings(self) -> list[Diagnostic]:
        return [d for d in self.diagnostics if d.severity == "warning"]


class LeanSandboxError(RuntimeError):
    """The host cannot provide the generated-code execution boundary."""


class LeanSourceChangedError(OSError):
    """Accepted source changed while a candidate was being validated."""


CORE_LOGICAL_AXIOMS = frozenset({"propext", "Quot.sound", "Classical.choice"})

#: Captures allowed before a moving artifact tree is reported as a failure.
_ENVIRONMENT_CAPTURE_ATTEMPTS = 3

#: Elaboration options the sandbox imposes on a candidate.
#:
#: Lake applies a project's `leanOptions` and the sandbox invokes the Lean
#: binary directly, so an option left at its default here would judge a
#: candidate under rules the project does not use. `autoImplicit` is the one
#: that changes what a statement means: left on, an identifier the author
#: never bound becomes a fresh implicit argument, so `(h : n = m)` with `m`
#: undefined elaborates as a theorem quantified over `m` — accepted here and
#: rejected by the project build.
SANDBOX_LEAN_OPTIONS = ("autoImplicit=false",)

_LEAN_OPTION_ARGS = tuple(argument for option in SANDBOX_LEAN_OPTIONS for argument in ("-D", option))


# ---------------------------------------------------------------------------
# Diagnostic parser
# ---------------------------------------------------------------------------

# Lean outputs diagnostics like:
# ./AutoLean/Sandbox.lean:10:4: error: unsolved goals ...
# Lean tags some diagnostics with the rule that raised them, as in
# `error(lean.unknownIdentifier):`. An untagged severity is still the common
# form, so the tag is optional and is not captured.
_DIAG_RE = re.compile(
    r"^(.+?):(\d+):(\d+):\s*(error|warning|info)(?:\([^)\r\n]*\))?:\s*(.*)",
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
            # Lake and Lean can report errors without a source location.
            stripped = lines[i].strip()
            if stripped.lower().startswith("error:"):
                msg_lines = [stripped[6:].strip()]
                j = i + 1
                while (
                    j < len(lines)
                    and not _DIAG_RE.match(lines[j])
                    and not lines[j].strip().lower().startswith("error:")
                ):
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


_NAME_PART = r"(?:[\w']+|«[^»\r\n]+»)"
_DECLARATION_NAME_RE = re.compile(rf"^(?:{_NAME_PART})(?:\.{_NAME_PART})*$")
_MODULE_COMPONENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_']*$")


def _module_name(relative_path: Path) -> str:
    """Return the Lean module name represented by a project-relative path."""
    if relative_path.is_absolute() or relative_path.suffix != ".lean":
        raise LeanSandboxError(f"invalid Lean module path: {relative_path}")
    parts = relative_path.with_suffix("").parts
    if not parts or any(part in {"", ".", ".."} for part in parts):
        raise LeanSandboxError(f"invalid Lean module path: {relative_path}")
    if any(_MODULE_COMPONENT_RE.fullmatch(part) is None for part in parts):
        raise LeanSandboxError(
            f"generated-code audits require identifier-safe Lean module paths: {relative_path}"
        )
    return ".".join(parts)


def _lean_string(value: str) -> str:
    """Encode a trusted Python string as a Lean string literal."""
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    escaped = escaped.replace("\n", "\\n").replace("\r", "\\r")
    return f'"{escaped}"'


def _declaration_audit_source(
    module: str,
    declaration: str,
    target_line: int,
    nonce: str,
) -> str:
    """Build a trusted Lean command that binds an audit to one source range."""
    if not _DECLARATION_NAME_RE.fullmatch(declaration):
        raise LeanSandboxError(f"declaration cannot be audited safely: {declaration!r}")
    if target_line < 1:
        raise LeanSandboxError("declaration audit line must be positive")
    return textwrap.dedent(
        f"""\
        import {module}
        import Lean

        open Lean
        open Lean.Elab Command

        run_cmd do
          let env ← getEnv
          let moduleName := {_lean_string(module)}.toName
          let declarationName := {_lean_string(declaration)}.toName
          let targetLine : Nat := {target_line}
          let some moduleIdx := env.getModuleIdx? moduleName
            | throwError m!"candidate module {{moduleName}} is unavailable"
          unless env.getModuleIdxFor? declarationName == some moduleIdx do
            throwError m!"{{declarationName}} is not declared by {{moduleName}}"
          let ranges? ← findDeclarationRangesCore? declarationName
          let some ranges := ranges?
            | throwError m!"source range unavailable for {{declarationName}}"
          unless ranges.range.pos.line <= targetLine &&
              targetLine <= ranges.range.endPos.line do
            throwError m!"line {{targetLine}} is outside {{declarationName}}"
          logInfo "AUTOLEAN_AUDIT_{nonce}_DECLARATION_OK"
          let axioms ← collectAxioms declarationName
          for axiomName in axioms do
            logInfo m!"AUTOLEAN_AUDIT_{nonce}_AXIOM:{{axiomName}}"
          logInfo "AUTOLEAN_AUDIT_{nonce}_COMPLETE"
        """
    )


def _parse_declaration_audit(output: str, nonce: str) -> tuple[str, ...] | None:
    """Parse the nonce-bound machine report emitted by the trusted audit."""
    prefix = f"AUTOLEAN_AUDIT_{nonce}_"
    if output.count(f"{prefix}DECLARATION_OK") != 1:
        return None
    if output.count(f"{prefix}COMPLETE") != 1:
        return None
    pattern = re.compile(rf"{re.escape(prefix)}AXIOM:([^\r\n]+)")
    return tuple(sorted({match.group(1).strip() for match in pattern.finditer(output)}))


def _apply_axiom_policy(
    result: BuildResult,
    declaration: str,
    allowed_axioms: frozenset[str],
) -> BuildResult:
    """Require a complete axiom report and reject non-foundational axioms."""
    if not result.success:
        return result
    axioms = result.axioms
    if axioms is None:
        result.success = False
        result.diagnostics.append(
            Diagnostic(
                file="<axiom-audit>",
                line=0,
                col=0,
                severity="error",
                message=f"Lean returned no axiom report for {declaration}",
            )
        )
        return result
    unexpected = sorted(set(axioms) - allowed_axioms)
    if unexpected:
        result.success = False
        result.diagnostics.append(
            Diagnostic(
                file="<axiom-audit>",
                line=0,
                col=0,
                severity="error",
                message=(f"{declaration} depends on disallowed axioms: " + ", ".join(unexpected)),
            )
        )
    return result


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
STANDARD_TACTICS: list[str] = [
    *FAST_TACTICS,
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


def _resolve_lean(root: Path) -> Path:
    """Resolve the selected toolchain's Lean binary before sandbox entry."""
    elan = shutil.which("elan")
    if elan is not None:
        try:
            result = subprocess.run(
                [elan, "which", "lean"],
                cwd=root,
                capture_output=True,
                text=True,
                timeout=15,
            )
            candidate = Path(result.stdout.strip())
            if result.returncode == 0 and candidate.is_file():
                return candidate.resolve()
        except (OSError, subprocess.SubprocessError):
            pass
    lean = shutil.which("lean")
    if lean is None:
        raise LeanSandboxError("secure Lean checks require Lean on PATH")
    return Path(lean).resolve()


def _resolve_bubblewrap() -> str | None:
    configured = os.environ.get("AUTOLEAN_BWRAP")
    if configured is None:
        return shutil.which("bwrap")
    path = Path(configured)
    if not path.is_absolute():
        raise LeanSandboxError("AUTOLEAN_BWRAP must name an absolute executable path")
    resolved = path.resolve()
    if not resolved.is_file() or not os.access(resolved, os.X_OK):
        raise LeanSandboxError(f"AUTOLEAN_BWRAP is not executable: {path}")
    return str(resolved)


def _sandbox_quote(path: Path) -> str:
    value = str(path).replace("\\", "\\\\").replace('"', '\\"')
    return f'"{value}"'


def _run_lean_check(
    cmd: list[str],
    *,
    cwd: Path,
    timeout: int,
    env: dict[str, str] | None = None,
) -> BuildResult:
    """Run one Lean check and normalize process and diagnostic failures."""
    t0 = time.monotonic()
    try:
        result = subprocess.run(
            cmd,
            cwd=cwd,
            capture_output=True,
            text=True,
            timeout=timeout,
            env=env,
        )
    except subprocess.TimeoutExpired:
        return BuildResult(
            success=False,
            stderr=f"Build timed out after {timeout}s",
            duration_seconds=timeout,
            timed_out=True,
        )
    except OSError as e:
        return BuildResult(
            success=False,
            stderr=f"Could not start Lean check: {e}",
            duration_seconds=time.monotonic() - t0,
        )

    duration = time.monotonic() - t0
    combined = result.stdout + "\n" + result.stderr
    diagnostics = _parse_diagnostics(combined)
    has_errors = any(d.severity == "error" for d in diagnostics)
    return BuildResult(
        success=result.returncode == 0 and not has_errors,
        diagnostics=diagnostics,
        stdout=result.stdout,
        stderr=result.stderr,
        duration_seconds=duration,
    )


def _atomic_write_text(path: Path, content: str) -> None:
    """Replace one text file atomically within its parent directory."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        temporary.replace(path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def _atomic_create_text(path: Path, content: str) -> None:
    """Create one text file atomically without replacing an existing path."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
            temporary = Path(handle.name)
        os.link(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Lean Project
# ---------------------------------------------------------------------------


@dataclass
class LeanProject:
    """Interface to a Lean 4 project on disk."""

    root: Path
    _proof_environment: ProofEnvironment | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _environment_fingerprint: EnvironmentFingerprint | None = field(
        default=None,
        init=False,
        repr=False,
    )
    _lean_binary: Path | None = field(default=None, init=False, repr=False)
    _module_paths: tuple[Path, ...] | None = field(default=None, init=False, repr=False)

    def __post_init__(self) -> None:
        self.root = Path(self.root).resolve()
        has_lakefile = (self.root / "lakefile.lean").exists() or (self.root / "lakefile.toml").exists()
        if not has_lakefile:
            raise FileNotFoundError(f"No lakefile.lean or lakefile.toml in {self.root}")

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
                timed_out=True,
            )
        except OSError as e:
            return BuildResult(
                success=False,
                stderr=f"Could not start Lean build: {e}",
                duration_seconds=time.monotonic() - t0,
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

    def check_file(
        self,
        lean_file: Path,
        timeout: int = 120,
        *,
        untrusted: bool = False,
    ) -> BuildResult:
        """Check one Lean file through its trusted or untrusted boundary."""
        rel = lean_file.resolve().relative_to(self.root)
        if untrusted:
            content = (self.root / rel).read_text(encoding="utf-8")
            return self._check_untrusted_content(content, timeout)

        # Lake module names use dots and omit the Lean suffix.
        module = str(rel).replace("/", ".").removesuffix(".lean")
        result = self.build(target=module, timeout=timeout)

        # Fallback: if lake build doesn't know the module, use lake env lean
        if not result.success and "unknown target" in (result.stderr or ""):
            return self._check_file_via_env(rel, timeout)

        return result

    def _check_file_via_env(
        self,
        rel_path: Path,
        timeout: int = 120,
    ) -> BuildResult:
        """Check a trusted unregistered file in the project environment."""
        return _run_lean_check(
            ["lake", "env", "lean", str(rel_path)],
            cwd=self.root,
            timeout=timeout,
        )

    def _check_untrusted_content(
        self,
        content: str,
        timeout: int,
    ) -> BuildResult:
        """Compile generated source from scratch inside the host sandbox."""
        with tempfile.TemporaryDirectory(
            prefix="autolean-lean-",
            dir="/private/tmp" if platform.system() == "Darwin" else None,
            ignore_cleanup_errors=True,
        ) as scratch_name:
            scratch = Path(scratch_name).resolve()
            try:
                cmd, env = self._sandboxed_lean_command(
                    scratch,
                    content,
                    relative_path=Path("AutoLeanInternal") / "Candidate.lean",
                )
            except LeanSandboxError as e:
                return BuildResult(success=False, stderr=str(e))
            return _run_lean_check(cmd, cwd=scratch, timeout=timeout, env=env)

    def _check_untrusted_declaration(
        self,
        lean_file: Path,
        content: str,
        timeout: int,
        declaration: str,
        declaration_line: int,
    ) -> BuildResult:
        """Compile exact source, then audit its declaration in a fresh
        module.
        """
        relative_path = lean_file.resolve().relative_to(self.root)
        try:
            module = _module_name(relative_path)
            nonce = secrets.token_hex(16)
            audit_source = _declaration_audit_source(
                module,
                declaration,
                declaration_line,
                nonce,
            )
        except LeanSandboxError as error:
            return BuildResult(success=False, stderr=str(error))

        with tempfile.TemporaryDirectory(
            prefix="autolean-lean-",
            dir="/private/tmp" if platform.system() == "Darwin" else None,
            ignore_cleanup_errors=True,
        ) as scratch_name:
            scratch = Path(scratch_name).resolve()
            try:
                candidate_cmd, candidate_env = self._sandboxed_lean_command(
                    scratch,
                    content,
                    relative_path=relative_path,
                )
            except LeanSandboxError as error:
                return BuildResult(success=False, stderr=str(error))
            candidate = _run_lean_check(
                candidate_cmd,
                cwd=scratch,
                timeout=timeout,
                env=candidate_env,
            )
            if not candidate.success:
                return candidate

            audit_path = Path("AutoLeanInternal") / f"Audit_{nonce}.lean"
            try:
                audit_cmd, audit_env = self._sandboxed_lean_command(
                    scratch,
                    audit_source,
                    relative_path=audit_path,
                )
            except LeanSandboxError as error:
                return BuildResult(success=False, stderr=str(error))
            audit = _run_lean_check(
                audit_cmd,
                cwd=scratch,
                timeout=timeout,
                env=audit_env,
            )
            audit.duration_seconds += candidate.duration_seconds
            audit.stdout = f"{candidate.stdout}\n{audit.stdout}"
            audit.stderr = f"{candidate.stderr}\n{audit.stderr}"
            if not audit.success:
                return audit
            audit.axioms = _parse_declaration_audit(
                f"{audit.stdout}\n{audit.stderr}",
                nonce,
            )
            return audit

    def validate_candidate(
        self,
        lean_file: Path,
        content: str,
        *,
        timeout: int = 120,
        declaration: str | None = None,
        declaration_line: int | None = None,
        expected_environment: str | None = None,
        allowed_axioms: frozenset[str] = CORE_LOGICAL_AXIOMS,
    ) -> BuildResult:
        """Validate generated source without changing the project tree.

        A declaration name and source line bind the axiom audit to the exact
        declaration accepted by Lean. Supplying an environment identity
        compares the proof closure after elaboration with the expected value,
        so a closure change before or during elaboration fails closed.
        """
        lean_file.resolve().relative_to(self.root)
        if (declaration is None) != (declaration_line is None):
            return BuildResult(
                success=False,
                stderr="declaration audits require both a name and source line",
            )
        if declaration is None:
            result = self._check_untrusted_content(content, timeout)
        else:
            assert declaration_line is not None
            result = self._check_untrusted_declaration(
                lean_file,
                content,
                timeout,
                declaration,
                declaration_line,
            )
        if declaration is not None:
            result = _apply_axiom_policy(result, declaration, allowed_axioms)

        if expected_environment is not None:
            mismatch = self._environment_mismatch(expected_environment)
            if mismatch is not None:
                return mismatch
        return result

    def accept_candidate(
        self,
        lean_file: Path,
        content: str,
        *,
        timeout: int = 120,
        declaration: str | None = None,
        declaration_line: int | None = None,
        expected_environment: str | None = None,
        expected_content: str | None = None,
        require_absent: bool = False,
    ) -> BuildResult:
        """Install the exact source accepted by the generated-code sandbox."""
        result = self.validate_candidate(
            lean_file,
            content,
            timeout=timeout,
            declaration=declaration,
            declaration_line=declaration_line,
            expected_environment=expected_environment,
        )
        if result.success:
            self.write_file(
                lean_file,
                content,
                expected_content=expected_content,
                require_absent=require_absent,
            )
        return result

    def _resolved_lean(self) -> Path:
        """The selected toolchain's Lean binary, resolved once per project."""
        if self._lean_binary is None:
            self._lean_binary = _resolve_lean(self.root)
        return self._lean_binary

    def _compiled_module_paths(self) -> list[Path]:
        """Compiled dependency roots, enumerated once per project.

        The set of roots changes only when `lake update` rewrites the
        manifest; a run never does that, and a changed manifest fails the
        environment identity check.
        """
        if self._module_paths is None:
            self._module_paths = tuple(
                sorted(
                    path.resolve()
                    for path in self.root.rglob("lib/lean")
                    if path.is_dir() and ".lake" in path.parts
                )
            )
        return list(self._module_paths)

    def _capture_environment(self, lean: Path) -> tuple[ProofEnvironment, EnvironmentFingerprint]:
        """Capture the closure identity and a fingerprint that bounds it.

        Hashing the closure reads every compiled artifact and takes seconds.
        A write landing inside that window would be hashed on one side and
        stat-recorded on the other, pairing a digest with a fingerprint that
        outlives it: every later refresh would then match the fingerprint,
        skip the re-hash, and keep certifying a closure that no longer
        exists. Bracketing the hash with the fingerprint makes such a write
        visible, and a tree that will not hold still fails closed.
        """
        for _ in range(_ENVIRONMENT_CAPTURE_ATTEMPTS):
            before = environment_fingerprint(self.root, lean)
            environment = capture_proof_environment(self.root, lean)
            after = environment_fingerprint(self.root, lean)
            if before == after:
                return environment, after
        raise ProofEnvironmentError(
            "the proof closure changed while its identity was captured; "
            "stop concurrent builds of this project and retry"
        )

    def proof_environment(self, *, refresh: bool = False) -> ProofEnvironment:
        """Return the content identity of the installed proof closure.

        A refresh revalidates against the artifact tree: an unchanged stat
        fingerprint reuses the captured identity, any change re-hashes the
        content.
        """
        lean = self._resolved_lean()
        if self._proof_environment is None or (
            refresh and environment_fingerprint(self.root, lean) != self._environment_fingerprint
        ):
            self._proof_environment, self._environment_fingerprint = self._capture_environment(lean)
        return self._proof_environment

    def _environment_mismatch(self, expected: str) -> BuildResult | None:
        """Return a failure when the installed proof closure changed."""
        try:
            actual = self.proof_environment(refresh=True).sha256
        except (OSError, ProofEnvironmentError) as e:
            return BuildResult(
                success=False,
                stderr=f"proof environment identification failed: {e}",
            )
        if actual == expected:
            return None
        return BuildResult(
            success=False,
            stderr=(
                "proof environment changed during validation: "
                f"expected sha256:{expected}, found sha256:{actual}"
            ),
        )

    def _sandboxed_lean_command(
        self,
        scratch: Path,
        content: str,
        *,
        relative_path: Path = Path("AutoLeanInternal") / "Candidate.lean",
    ) -> tuple[list[str], dict[str, str]]:
        lean = self._resolved_lean()
        if relative_path.is_absolute() or ".." in relative_path.parts:
            raise LeanSandboxError(f"invalid sandbox source path: {relative_path}")
        candidate = scratch / relative_path
        candidate.parent.mkdir(parents=True, exist_ok=True)
        candidate.write_text(content, encoding="utf-8")
        lean_args = [
            str(lean),
            "-R",
            str(scratch),
            *_LEAN_OPTION_ARGS,
            "-o",
            str(candidate.with_suffix(".olean")),
            "-i",
            str(candidate.with_suffix(".ilean")),
            str(candidate),
        ]
        module_paths = self._compiled_module_paths()
        library_paths = sorted({path.parent for path in module_paths})
        host = platform.system()
        if host == "Linux":
            source_lake = (self.root / ".lake").resolve()
            sandbox_lake = scratch / "project-lake"
            sandbox_lake.mkdir(exist_ok=True)
            module_paths = [sandbox_lake / path.relative_to(source_lake) for path in module_paths]
            library_paths = sorted({path.parent for path in module_paths})
        env = {
            "HOME": str(scratch),
            "LANG": "C.UTF-8",
            "LC_ALL": "C.UTF-8",
            "LEAN_ABORT_ON_PANIC": "1",
            "PATH": os.pathsep.join((str(lean.parent), "/usr/bin", "/bin", "/usr/sbin", "/sbin")),
            "TMPDIR": str(scratch),
        }
        env["LEAN_PATH"] = os.pathsep.join(str(path) for path in (scratch, *module_paths))
        if library_paths:
            env["DYLD_LIBRARY_PATH"] = os.pathsep.join(str(path) for path in library_paths)
            env["LD_LIBRARY_PATH"] = env["DYLD_LIBRARY_PATH"]

        if host == "Darwin":
            sandbox = Path("/usr/bin/sandbox-exec")
            if not sandbox.exists():
                raise LeanSandboxError("secure Lean checks require sandbox-exec on macOS")
            profile = scratch / "lean.sb"
            profile.write_text(self._macos_sandbox_profile(lean, scratch), encoding="utf-8")
            return [str(sandbox), "-f", str(profile), *lean_args], env
        if host == "Linux":
            bwrap = _resolve_bubblewrap()
            if bwrap is None:
                raise LeanSandboxError("secure Lean checks require bubblewrap (`bwrap`) on Linux")
            return self._bubblewrap_command(bwrap, lean, scratch, lean_args), env
        raise LeanSandboxError(f"secure Lean checks are unavailable on {host or 'this platform'}")

    def _macos_sandbox_profile(self, lean: Path, scratch: Path) -> str:
        read_subpaths = [
            lean.parent.parent,
            self.root / ".lake",
            self.root / "lake-packages",
            Path("/System"),
            Path("/Library"),
            Path("/usr"),
            Path("/bin"),
            Path("/sbin"),
            Path("/dev"),
            Path("/private/etc"),
            Path("/nix/store"),
            scratch,
        ]
        subpath_rules = "\n".join(
            f"    (subpath {_sandbox_quote(path.resolve())})" for path in read_subpaths if path.exists()
        )
        return (
            "(version 1)\n"
            "(deny default)\n"
            "(allow process*)\n"
            "(allow signal)\n"
            "(allow sysctl-read)\n"
            "(allow mach-lookup)\n"
            "(allow file-read-metadata)\n"
            "(allow file-read*)\n"
            "(deny file-read*\n"
            '    (subpath "/Users")\n'
            '    (subpath "/home")\n'
            '    (subpath "/root")\n'
            '    (subpath "/Volumes")\n'
            '    (subpath "/tmp")\n'
            '    (subpath "/private/tmp")\n'
            '    (subpath "/private/var/folders"))\n'
            "(allow file-read*\n"
            f"{subpath_rules})\n"
            "(allow file-write*\n"
            f"    (subpath {_sandbox_quote(scratch)}))\n"
        )

    def _bubblewrap_command(
        self,
        bwrap: str,
        lean: Path,
        scratch: Path,
        lean_args: list[str],
    ) -> list[str]:
        args = [
            bwrap,
            "--die-with-parent",
            "--new-session",
            "--unshare-all",
            "--proc",
            "/proc",
            "--dev",
            "/dev",
            "--bind",
            str(scratch),
            str(scratch),
            "--chdir",
            str(scratch),
        ]
        project_lake = (self.root / ".lake").resolve()
        if project_lake.exists():
            args.extend(
                (
                    "--ro-bind",
                    str(project_lake),
                    str(scratch / "project-lake"),
                )
            )
        roots = {
            lean.parent.parent,
            *(Path(path) for path in ("/usr", "/bin", "/lib", "/lib64", "/etc", "/nix/store")),
        }
        for path in sorted((p.resolve() for p in roots if p.exists()), key=str):
            if path == self.root or self.root in path.parents:
                continue
            args.extend(("--ro-bind", str(path), str(path)))
        args.extend(("--remount-ro", "/"))
        return [*args, *lean_args]

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

    def write_file(
        self,
        path: Path,
        content: str,
        *,
        expected_content: str | None = None,
        require_absent: bool = False,
    ) -> None:
        """Atomically write content when the accepted source still matches."""
        if expected_content is not None and require_absent:
            raise ValueError("expected_content and require_absent are mutually exclusive")
        if require_absent:
            try:
                _atomic_create_text(path, content)
            except FileExistsError as e:
                raise LeanSourceChangedError(f"source appeared during validation: {path}") from e
            return
        if expected_content is not None:
            current = path.read_text(encoding="utf-8")
            if current != expected_content:
                raise LeanSourceChangedError(f"source changed during validation: {path}")
        _atomic_write_text(path, content)

    # -- Goal extraction (hole-punch method) --------------------------------

    def get_goal_via_hole_punch(self, lean_file: Path, line: int, col: int, timeout: int = 60) -> str | None:
        """Extract the goal by checking a scratch copy containing `?_`."""
        original = self.read_file(lean_file)
        lines = original.split("\n")
        masked_lines = _mask_lean_noncode(original).split("\n")

        if line < 1 or line > len(lines):
            return None

        target_line = lines[line - 1]
        sorry_match = next(
            (match for match in re.finditer(r"\bsorry\b", masked_lines[line - 1]) if match.start() == col),
            None,
        )
        if not sorry_match:
            return None

        # Punch: replace sorry with ?_ (typed hole)
        punched_line = target_line[: sorry_match.start()] + "?_" + target_line[sorry_match.end() :]
        lines[line - 1] = punched_line
        punched_content = "\n".join(lines)
        result = self.validate_candidate(lean_file, punched_content, timeout=timeout)

        for diagnostic in result.diagnostics:
            if "unsolved goals" in diagnostic.message.lower():
                return diagnostic.message
        for diagnostic in result.diagnostics:
            if abs(diagnostic.line - line) <= 3 and diagnostic.severity == "error":
                return diagnostic.message
        return None

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
        """
        original = self.read_file(lean_file)
        original_sorries = count_sorries(original)

        tactics_to_try = list(STANDARD_TACTICS)
        if include_compound:
            tactics_to_try.extend(COMPOUND_TACTICS)

        for tactic in tactics_to_try:
            try:
                new_content = self.replace_sorry_at(
                    lean_file,
                    line,
                    tactic,
                    original_content=original,
                    col=col,
                )
                result = self.validate_candidate(
                    lean_file,
                    new_content,
                    timeout=timeout_per_tactic,
                )

                if result.success and count_sorries(new_content) == original_sorries - 1:
                    return tactic
            except (ValueError, OSError):
                pass

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
        original_sorries = count_sorries(original)

        for tactic in tactics:
            try:
                new_content = self.replace_sorry_at(
                    lean_file,
                    line,
                    tactic,
                    original_content=original,
                    col=col,
                )
                result = self.validate_candidate(
                    lean_file,
                    new_content,
                    timeout=timeout_per_tactic,
                )

                if result.success and count_sorries(new_content) == original_sorries - 1:
                    return tactic
            except (ValueError, OSError):
                pass

        return None

    # -- Sorry replacement --------------------------------------------------

    def replace_sorry_at(
        self,
        path: Path,
        line: int,
        replacement: str,
        original_content: str | None = None,
        *,
        col: int | None = None,
    ) -> str:
        """
        Replace a `sorry` at the given line with the replacement tactic block.

        Returns the new file content.
        """
        content = self.read_file(path) if original_content is None else original_content
        lines = content.split("\n")
        masked_lines = _mask_lean_noncode(content).split("\n")

        if line < 1 or line > len(lines):
            raise ValueError(f"Line {line} out of range (1..{len(lines)})")

        target_line = lines[line - 1]

        # Find the sorry token and its indentation
        matches = list(re.finditer(r"\bsorry\b", masked_lines[line - 1]))
        sorry_match = next((match for match in matches if match.start() == col), None)
        if sorry_match is None and col is None and len(matches) == 1:
            sorry_match = matches[0]
        if not sorry_match:
            if not matches:
                raise ValueError(f"No 'sorry' found at line {line}: {target_line!r}")
            location = f" at column {col}" if col is not None else ""
            raise ValueError(f"No unambiguous 'sorry' found at line {line}{location}: {target_line!r}")

        indent = " " * sorry_match.start()

        # Preserve nesting inside the completion while moving its common base
        # indentation to the position occupied by `sorry`.
        replacement_lines = textwrap.dedent(replacement).strip("\n").split("\n")
        indented = []
        for i, rline in enumerate(replacement_lines):
            if not rline.strip():
                indented.append("")
            elif i == 0:
                # First line: placed exactly where sorry was
                indented.append(rline.strip())
            else:
                indented.append(indent + rline.rstrip())

        replacement_block = "\n".join(indented)

        # Replace sorry with the block
        new_line = target_line[: sorry_match.start()] + replacement_block + target_line[sorry_match.end() :]
        lines[line - 1] = new_line

        return "\n".join(lines)
