"""Tests for autolean.lean_interface — diagnostics parsing and sorry replacement."""

from __future__ import annotations

from pathlib import Path

import pytest

from autolean.lean_interface import LeanProject, _parse_diagnostics


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
        output = (
            "./Foo.lean:3:2: error: type mismatch\n"
            "  expected: Nat\n"
            "  got: Bool\n"
        )
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
        """Lines not matching the diagnostic pattern are skipped (before first match)."""
        output = (
            "Building module Foo...\n"
            "./Foo.lean:2:0: error: sorry remains\n"
            "Done.\n"
        )
        diags = _parse_diagnostics(output)
        assert len(diags) == 1
        # "Done." becomes a continuation line of the diagnostic
        assert diags[0].file == "./Foo.lean"


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
        """Create a LeanProject with a minimal lakefile so __post_init__ passes."""
        (tmp_path / "lakefile.lean").write_text("-- lakefile\n", encoding="utf-8")
        return LeanProject(tmp_path)

    def test_basic_replacement(self, project: LeanProject, tmp_path: Path) -> None:
        content = "theorem t : True := by\n  sorry\n"
        result = project.replace_sorry_at(
            tmp_path / "T.lean", 2, "trivial", original_content=content
        )
        assert "trivial" in result
        assert "sorry" not in result

    def test_indented_sorry_preserves_indent(
        self, project: LeanProject, tmp_path: Path
    ) -> None:
        content = "theorem t : True := by\n    sorry\n"
        result = project.replace_sorry_at(
            tmp_path / "T.lean", 2, "trivial", original_content=content
        )
        # The replacement should sit at the same indentation the sorry was at
        lines = result.split("\n")
        assert lines[1].startswith("    trivial")

    def test_multiline_replacement(
        self, project: LeanProject, tmp_path: Path
    ) -> None:
        content = "theorem t : True := by\n  sorry\n"
        result = project.replace_sorry_at(
            tmp_path / "T.lean", 2, "intro\n  exact True.intro", original_content=content
        )
        assert "intro" in result
        assert "exact True.intro" in result
        assert "sorry" not in result

    def test_line_out_of_range_raises(
        self, project: LeanProject, tmp_path: Path
    ) -> None:
        content = "theorem t : True := by\n  sorry\n"
        with pytest.raises(ValueError, match="out of range"):
            project.replace_sorry_at(
                tmp_path / "T.lean", 99, "trivial", original_content=content
            )

    def test_line_zero_raises(
        self, project: LeanProject, tmp_path: Path
    ) -> None:
        content = "sorry\n"
        with pytest.raises(ValueError, match="out of range"):
            project.replace_sorry_at(
                tmp_path / "T.lean", 0, "trivial", original_content=content
            )

    def test_no_sorry_on_line_raises(
        self, project: LeanProject, tmp_path: Path
    ) -> None:
        content = "theorem t : True := by\n  trivial\n"
        with pytest.raises(ValueError, match="No 'sorry' found"):
            project.replace_sorry_at(
                tmp_path / "T.lean", 2, "omega", original_content=content
            )

    def test_other_lines_unchanged(
        self, project: LeanProject, tmp_path: Path
    ) -> None:
        content = "import Mathlib\n\ntheorem t : True := by\n  sorry\n\n-- end\n"
        result = project.replace_sorry_at(
            tmp_path / "T.lean", 4, "trivial", original_content=content
        )
        lines = result.split("\n")
        assert lines[0] == "import Mathlib"
        assert lines[1] == ""
        assert lines[4] == ""
        assert lines[5] == "-- end"
