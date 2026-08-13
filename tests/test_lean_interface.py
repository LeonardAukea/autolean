"""Tests for Lean diagnostics, source edits, and sandbox commands."""

from __future__ import annotations

from pathlib import Path

import pytest

from autolean import lean_interface
from autolean.lean_interface import (
    BuildResult,
    LeanProject,
    _declaration_audit_source,
    _parse_declaration_audit,
    _parse_diagnostics,
)

# ---------------------------------------------------------------------------
# _parse_diagnostics
# ---------------------------------------------------------------------------


class TestParseDiagnostics:
    """Tests for parsing Lean compiler output."""

    def test_single_error(self) -> None:
        output = "./Foo.lean:10:4: error: unsolved goals\ncase zero\n  ...\n"
        diags = _parse_diagnostics(output)
        assert len(diags) == 1
        d = diags[0]
        assert d.file == "./Foo.lean"
        assert d.line == 10
        assert d.col == 4
        assert d.severity == "error"
        assert "unsolved goals" in d.message

    def test_single_warning(self) -> None:
        output = "./Bar.lean:5:0: warning: declaration uses 'sorry'\n"
        diags = _parse_diagnostics(output)
        assert len(diags) == 1
        assert diags[0].severity == "warning"
        assert "sorry" in diags[0].message

    def test_multiple_diagnostics(self) -> None:
        output = (
            "./A.lean:1:0: error: type mismatch\n"
            "./A.lean:5:2: warning: unused variable\n"
            "./B.lean:10:0: error: unknown identifier\n"
        )
        diags = _parse_diagnostics(output)
        assert len(diags) == 3
        assert diags[0].severity == "error"
        assert diags[1].severity == "warning"
        assert diags[2].severity == "error"

    def test_multiline_diagnostic(self) -> None:
        """Continuation lines (not matching the diag pattern) are collected."""
        output = "./Foo.lean:3:2: error: type mismatch\n  expected: Nat\n  got: Bool\n"
        diags = _parse_diagnostics(output)
        assert len(diags) == 1
        assert "expected: Nat" in diags[0].message
        assert "got: Bool" in diags[0].message

    def test_empty_output(self) -> None:
        assert _parse_diagnostics("") == []

    def test_no_diagnostics_in_output(self) -> None:
        output = "Build completed successfully.\nNo issues found.\n"
        assert _parse_diagnostics(output) == []

    def test_info_severity(self) -> None:
        output = "./X.lean:1:0: info: something informational\n"
        diags = _parse_diagnostics(output)
        assert len(diags) == 1
        assert diags[0].severity == "info"

    def test_mixed_with_non_diag_lines(self) -> None:
        """Preamble lines outside diagnostics are skipped."""
        output = "Building module Foo...\n./Foo.lean:2:0: error: sorry remains\nDone.\n"
        diags = _parse_diagnostics(output)
        assert len(diags) == 1
        # "Done." becomes a continuation line of the diagnostic
        assert diags[0].file == "./Foo.lean"


class TestAxiomAudit:
    def test_parses_nonce_bound_machine_report(self) -> None:
        output = (
            "AUTOLEAN_AUDIT_token_DECLARATION_OK\n"
            "AUTOLEAN_AUDIT_token_AXIOM:Classical.choice\n"
            "AUTOLEAN_AUDIT_token_AXIOM:propext\n"
            "AUTOLEAN_AUDIT_token_COMPLETE\n"
        )

        assert _parse_declaration_audit(output, "token") == (
            "Classical.choice",
            "propext",
        )

    def test_machine_report_requires_matching_nonce_and_completion(self) -> None:
        forged = "AUTOLEAN_AUDIT_other_DECLARATION_OK\nAUTOLEAN_AUDIT_other_COMPLETE\n"

        assert _parse_declaration_audit(forged, "expected") is None

    def test_audit_source_checks_module_and_target_line(self) -> None:
        source = _declaration_audit_source("Example", "Outer.target", 17, "token")

        assert "import Example" in source
        assert 'let declarationName := "Outer.target".toName' in source
        assert "let targetLine : Nat := 17" in source
        assert "env.getModuleIdxFor? declarationName" in source
        assert "targetLine <= ranges.range.endPos.line" in source


# ---------------------------------------------------------------------------
# LeanProject.replace_sorry_at
# ---------------------------------------------------------------------------


class TestReplaceSorryAt:
    """Tests for sorry replacement logic.

    These tests use original_content so we do not need a real LeanProject on disk.
    We instantiate LeanProject with a fake root but pass original_content directly.
    """

    @pytest.fixture()
    def project(self, tmp_path: Path) -> LeanProject:
        """Create a project with the required Lake marker."""
        (tmp_path / "lakefile.lean").write_text("-- lakefile\n", encoding="utf-8")
        return LeanProject(tmp_path)

    def test_basic_replacement(self, project: LeanProject, tmp_path: Path) -> None:
        content = "theorem t : True := by\n  sorry\n"
        result = project.replace_sorry_at(tmp_path / "T.lean", 2, "trivial", original_content=content)
        assert "trivial" in result
        assert "sorry" not in result

    def test_indented_sorry_preserves_indent(self, project: LeanProject, tmp_path: Path) -> None:
        content = "theorem t : True := by\n    sorry\n"
        result = project.replace_sorry_at(tmp_path / "T.lean", 2, "trivial", original_content=content)
        # The replacement should sit at the same indentation the sorry was at
        lines = result.split("\n")
        assert lines[1].startswith("    trivial")

    def test_multiline_replacement(self, project: LeanProject, tmp_path: Path) -> None:
        content = "theorem t : True := by\n  sorry\n"
        result = project.replace_sorry_at(
            tmp_path / "T.lean", 2, "intro\n  exact True.intro", original_content=content
        )
        assert "intro" in result
        assert "exact True.intro" in result
        assert "sorry" not in result

    def test_multiline_replacement_preserves_relative_indentation(
        self, project: LeanProject, tmp_path: Path
    ) -> None:
        content = "theorem t : True := by\n  sorry\n"
        proof = "have h : True := by\n  trivial\nexact h"
        result = project.replace_sorry_at(tmp_path / "T.lean", 2, proof, original_content=content)
        assert result.splitlines()[1:4] == [
            "  have h : True := by",
            "    trivial",
            "  exact h",
        ]

    def test_candidate_validation_restores_source(
        self,
        project: LeanProject,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source = tmp_path / "T.lean"
        source.write_text("original")

        def check(content: str, timeout: int) -> BuildResult:
            assert source.read_text() == "original"
            assert content == "candidate"
            assert timeout == 120
            return BuildResult(success=True)

        monkeypatch.setattr(project, "_check_untrusted_content", check)
        assert project.validate_candidate(source, "candidate").success
        assert source.read_text() == "original"

    def test_candidate_validation_restores_source_after_exception(
        self,
        project: LeanProject,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source = tmp_path / "T.lean"
        source.write_text("original")

        def fail(content: str, timeout: int) -> BuildResult:
            assert content == "candidate"
            assert timeout == 120
            assert source.read_text() == "original"
            raise OSError("process start failed")

        monkeypatch.setattr(project, "_check_untrusted_content", fail)
        with pytest.raises(OSError, match="process start failed"):
            project.validate_candidate(source, "candidate")
        assert source.read_text() == "original"

    def test_candidate_validation_requires_allowed_axioms(
        self,
        project: LeanProject,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source = tmp_path / "T.lean"
        source.write_text("original")

        def check(
            lean_file: Path,
            content: str,
            timeout: int,
            declaration: str,
            declaration_line: int,
        ) -> BuildResult:
            assert lean_file == source
            assert content == "theorem target : True := by trivial"
            assert timeout == 120
            assert declaration == "T.target"
            assert declaration_line == 1
            return BuildResult(success=True, axioms=("sorryAx",))

        monkeypatch.setattr(project, "_check_untrusted_declaration", check)

        result = project.validate_candidate(
            source,
            "theorem target : True := by trivial",
            declaration="T.target",
            declaration_line=1,
        )

        assert not result.success
        assert result.axioms == ("sorryAx",)
        assert "disallowed axioms: sorryAx" in result.errors[0].message

    def test_candidate_validation_accepts_foundational_axioms(
        self,
        project: LeanProject,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source = tmp_path / "T.lean"
        source.write_text("original")
        monkeypatch.setattr(
            project,
            "_check_untrusted_declaration",
            lambda lean_file, content, timeout, declaration, declaration_line: BuildResult(
                success=True,
                axioms=("propext", "Quot.sound", "Classical.choice"),
            ),
        )

        result = project.validate_candidate(
            source,
            "theorem target : True := by trivial",
            declaration="T.target",
            declaration_line=1,
        )

        assert result.success
        assert set(result.axioms or ()) == {
            "propext",
            "Quot.sound",
            "Classical.choice",
        }

    def test_accept_candidate_writes_only_after_success(
        self,
        project: LeanProject,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source = tmp_path / "nested" / "T.lean"
        monkeypatch.setattr(
            project,
            "_check_untrusted_content",
            lambda content, timeout: BuildResult(success=True),
        )

        result = project.accept_candidate(source, "accepted")

        assert result.success
        assert source.read_text() == "accepted"

    def test_accept_candidate_can_require_an_absent_output(
        self,
        project: LeanProject,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source = tmp_path / "T.lean"
        source.write_text("user source", encoding="utf-8")
        monkeypatch.setattr(
            project,
            "_check_untrusted_content",
            lambda content, timeout: BuildResult(success=True),
        )

        with pytest.raises(OSError, match="appeared during validation"):
            project.accept_candidate(
                source,
                "generated",
                require_absent=True,
            )

        assert source.read_text(encoding="utf-8") == "user source"

    def test_accept_candidate_preserves_file_after_rejection(
        self,
        project: LeanProject,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        source = tmp_path / "T.lean"
        source.write_text("original")
        monkeypatch.setattr(
            project,
            "_check_untrusted_content",
            lambda content, timeout: BuildResult(
                success=False,
                stderr="rejected",
            ),
        )

        result = project.accept_candidate(source, "candidate")

        assert not result.success
        assert source.read_text() == "original"

    def test_write_file_rejects_a_concurrent_source_change(
        self,
        project: LeanProject,
        tmp_path: Path,
    ) -> None:
        source = tmp_path / "T.lean"
        source.write_text("editor save", encoding="utf-8")

        with pytest.raises(OSError, match="source changed during validation"):
            project.write_file(
                source,
                "candidate",
                expected_content="validation snapshot",
            )

        assert source.read_text(encoding="utf-8") == "editor save"

    def test_linux_sandbox_needs_no_project_lake_directory(
        self,
        project: LeanProject,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        lean = tmp_path / "toolchain" / "bin" / "lean"
        lean.parent.mkdir(parents=True)
        lean.write_text("")
        monkeypatch.setattr(lean_interface.platform, "system", lambda: "Linux")
        monkeypatch.setattr(lean_interface, "_resolve_lean", lambda root: lean)
        monkeypatch.setattr(
            lean_interface.shutil,
            "which",
            lambda name: "/usr/bin/bwrap" if name == "bwrap" else None,
        )

        cmd, env = project._sandboxed_lean_command(
            scratch,
            "example : True := by exact True.intro\n",
        )

        assert cmd[0] == "/usr/bin/bwrap"
        assert "--unshare-all" in cmd
        assert cmd[cmd.index("--remount-ro") + 1] == "/"
        assert str(project.root / ".lake") not in cmd
        assert (scratch / "AutoLeanInternal" / "Candidate.lean").is_file()
        assert set(env) == {
            "HOME",
            "LANG",
            "LC_ALL",
            "LEAN_ABORT_ON_PANIC",
            "LEAN_PATH",
            "PATH",
            "TMPDIR",
        }

    def test_linux_sandbox_uses_configured_bubblewrap(
        self,
        project: LeanProject,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        lean = tmp_path / "toolchain" / "bin" / "lean"
        lean.parent.mkdir(parents=True)
        lean.write_text("", encoding="utf-8")
        bubblewrap = tmp_path / "bin" / "bwrap"
        bubblewrap.parent.mkdir()
        bubblewrap.write_text("#!/bin/sh\n", encoding="utf-8")
        bubblewrap.chmod(0o700)
        monkeypatch.setenv("AUTOLEAN_BWRAP", str(bubblewrap))
        monkeypatch.setattr(lean_interface.platform, "system", lambda: "Linux")
        monkeypatch.setattr(lean_interface, "_resolve_lean", lambda root: lean)
        monkeypatch.setattr(
            lean_interface.shutil,
            "which",
            lambda name: pytest.fail(f"PATH lookup used for {name}"),
        )

        command, _ = project._sandboxed_lean_command(
            scratch,
            "example : True := by exact True.intro\n",
        )

        assert command[0] == str(bubblewrap.resolve())

    def test_linux_sandbox_rejects_relative_bubblewrap_override(
        self,
        project: LeanProject,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        scratch = tmp_path / "scratch"
        scratch.mkdir()
        lean = tmp_path / "toolchain" / "bin" / "lean"
        lean.parent.mkdir(parents=True)
        lean.write_text("", encoding="utf-8")
        monkeypatch.setenv("AUTOLEAN_BWRAP", "bin/bwrap")
        monkeypatch.setattr(lean_interface.platform, "system", lambda: "Linux")
        monkeypatch.setattr(lean_interface, "_resolve_lean", lambda root: lean)

        with pytest.raises(lean_interface.LeanSandboxError, match="absolute executable"):
            project._sandboxed_lean_command(
                scratch,
                "example : True := by exact True.intro\n",
            )

    def test_line_out_of_range_raises(self, project: LeanProject, tmp_path: Path) -> None:
        content = "theorem t : True := by\n  sorry\n"
        with pytest.raises(ValueError, match="out of range"):
            project.replace_sorry_at(tmp_path / "T.lean", 99, "trivial", original_content=content)

    def test_line_zero_raises(self, project: LeanProject, tmp_path: Path) -> None:
        content = "sorry\n"
        with pytest.raises(ValueError, match="out of range"):
            project.replace_sorry_at(tmp_path / "T.lean", 0, "trivial", original_content=content)

    def test_no_sorry_on_line_raises(self, project: LeanProject, tmp_path: Path) -> None:
        content = "theorem t : True := by\n  trivial\n"
        with pytest.raises(ValueError, match="No 'sorry' found"):
            project.replace_sorry_at(tmp_path / "T.lean", 2, "omega", original_content=content)

    def test_other_lines_unchanged(self, project: LeanProject, tmp_path: Path) -> None:
        content = "import Mathlib\n\ntheorem t : True := by\n  sorry\n\n-- end\n"
        result = project.replace_sorry_at(tmp_path / "T.lean", 4, "trivial", original_content=content)
        lines = result.split("\n")
        assert lines[0] == "import Mathlib"
        assert lines[1] == ""
        assert lines[4] == ""
        assert lines[5] == "-- end"

    def test_exact_column_selects_one_of_two_placeholders(
        self,
        project: LeanProject,
        tmp_path: Path,
    ) -> None:
        content = "theorem pair : True ∧ True := And.intro (by sorry) (by sorry)\n"
        second = content.rindex("sorry")

        result = project.replace_sorry_at(
            tmp_path / "T.lean",
            1,
            "trivial",
            original_content=content,
            col=second,
        )

        assert result == "theorem pair : True ∧ True := And.intro (by sorry) (by trivial)\n"


class TestProofEnvironmentCaching:
    def test_unchanged_tree_reuses_the_captured_identity(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        from autolean import lean_interface, provenance
        from tests.test_provenance import _environment

        project_dir, lean = _environment(tmp_path)
        monkeypatch.setattr(provenance, "_read_lean_version", lambda _: "Lean (version 4.33.0)")
        monkeypatch.setattr(lean_interface, "_resolve_lean", lambda _: lean)
        captures = 0
        real_capture = lean_interface.capture_proof_environment

        def counting_capture(root: Path, lean_path: Path) -> object:
            nonlocal captures
            captures += 1
            return real_capture(root, lean_path)

        monkeypatch.setattr(lean_interface, "capture_proof_environment", counting_capture)
        project = lean_interface.LeanProject(project_dir)

        first = project.proof_environment()
        unchanged = project.proof_environment(refresh=True)

        assert unchanged.sha256 == first.sha256
        assert captures == 1

        artifact = (
            project_dir
            / ".lake"
            / "packages"
            / "mathlib"
            / ".lake"
            / "build"
            / "lib"
            / "lean"
            / "Mathlib.olean"
        )
        artifact.write_bytes(b"different-olean-content")
        changed = project.proof_environment(refresh=True)

        assert captures == 2
        assert changed.sha256 != first.sha256
