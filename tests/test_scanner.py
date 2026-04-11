"""Tests for autolean.scanner — sorry scanning, tactic mode, prioritization."""

from __future__ import annotations

from pathlib import Path

import pytest

from autolean.scanner import (
    SorryTarget,
    _find_enclosing_decl,
    _is_tactic_mode,
    prioritize_targets,
    scan_file,
)


# ---------------------------------------------------------------------------
# scan_file
# ---------------------------------------------------------------------------


class TestScanFile:
    """Tests for scan_file."""

    def test_no_sorry_returns_empty(self, lean_file_no_sorry: Path) -> None:
        targets = scan_file(lean_file_no_sorry)
        assert targets == []

    def test_one_sorry_tactic_mode(self, lean_file_one_sorry: Path) -> None:
        targets = scan_file(lean_file_one_sorry)
        assert len(targets) == 1
        t = targets[0]
        assert t.line == 2
        assert t.tactic_mode is True
        assert t.decl_name == "foo"

    def test_sorry_in_comment_skipped(self, lean_file_sorry_in_comment: Path) -> None:
        targets = scan_file(lean_file_sorry_in_comment)
        assert targets == []

    def test_sorry_in_string_skipped(self, lean_file_sorry_in_string: Path) -> None:
        targets = scan_file(lean_file_sorry_in_string)
        assert targets == []

    def test_term_mode_detected(self, lean_file_term_sorry: Path) -> None:
        targets = scan_file(lean_file_term_sorry)
        assert len(targets) == 1
        assert targets[0].tactic_mode is False

    def test_multi_sorry_found(self, lean_file_multi_sorry: Path) -> None:
        targets = scan_file(lean_file_multi_sorry)
        assert len(targets) == 3
        names = [t.decl_name for t in targets]
        assert names == ["t1", "t2", "t3"]

    def test_rel_path_populated_with_project_root(
        self, lean_file_one_sorry: Path, tmp_path: Path
    ) -> None:
        targets = scan_file(lean_file_one_sorry, project_root=tmp_path)
        assert len(targets) == 1
        assert targets[0].rel_path == "One.lean"

    def test_rel_path_empty_without_project_root(
        self, lean_file_one_sorry: Path
    ) -> None:
        targets = scan_file(lean_file_one_sorry)
        assert len(targets) == 1
        assert targets[0].rel_path == ""


# ---------------------------------------------------------------------------
# SorryTarget.id
# ---------------------------------------------------------------------------


class TestSorryTargetId:
    """Tests for collision-safe target ID generation."""

    def test_id_uses_rel_path_when_set(self, lean_file_one_sorry: Path, tmp_path: Path) -> None:
        targets = scan_file(lean_file_one_sorry, project_root=tmp_path)
        t = targets[0]
        assert t.id == "One.lean:2:foo"

    def test_id_uses_filename_when_no_rel_path(self, lean_file_one_sorry: Path) -> None:
        targets = scan_file(lean_file_one_sorry)
        t = targets[0]
        assert t.id == "One.lean:2:foo"

    def test_id_collision_safe_with_subdirs(self, tmp_path: Path) -> None:
        """Two files named Foo.lean in different subdirs get different IDs."""
        (tmp_path / "A").mkdir()
        (tmp_path / "B").mkdir()
        f1 = tmp_path / "A" / "Foo.lean"
        f2 = tmp_path / "B" / "Foo.lean"
        content = "theorem x : True := by\n  sorry\n"
        f1.write_text(content, encoding="utf-8")
        f2.write_text(content, encoding="utf-8")

        t1 = scan_file(f1, project_root=tmp_path)
        t2 = scan_file(f2, project_root=tmp_path)
        assert len(t1) == 1
        assert len(t2) == 1
        assert t1[0].id != t2[0].id
        assert "A/Foo.lean" in t1[0].id
        assert "B/Foo.lean" in t2[0].id


# ---------------------------------------------------------------------------
# _is_tactic_mode
# ---------------------------------------------------------------------------


class TestIsTacticMode:
    """Tests for tactic vs term mode detection."""

    def test_by_sorry_is_tactic(self) -> None:
        lines = ["theorem t : True := by", "  sorry"]
        assert _is_tactic_mode(lines, 2) is True

    def test_assign_sorry_is_term(self) -> None:
        lines = ["def x : Nat := sorry"]
        assert _is_tactic_mode(lines, 1) is False

    def test_by_on_same_line(self) -> None:
        lines = ["theorem t : True := by sorry"]
        assert _is_tactic_mode(lines, 1) is True

    def test_sorry_alone_after_by(self) -> None:
        """sorry on its own line after a `by` line is tactic mode."""
        lines = [
            "theorem t : True := by",
            "  intro",
            "  sorry",
        ]
        assert _is_tactic_mode(lines, 3) is True

    def test_sorry_after_assign_is_term(self) -> None:
        """sorry on its own line after `:=` is term mode."""
        lines = [
            "def x : Nat :=",
            "  sorry",
        ]
        assert _is_tactic_mode(lines, 2) is False


# ---------------------------------------------------------------------------
# _find_enclosing_decl
# ---------------------------------------------------------------------------


class TestFindEnclosingDecl:
    """Tests for finding the enclosing declaration."""

    def test_theorem(self) -> None:
        lines = ["theorem foo : True := by", "  sorry"]
        name, line = _find_enclosing_decl(lines, 2)
        assert name == "foo"
        assert line == 1

    def test_lemma(self) -> None:
        lines = ["lemma bar : False := by", "  sorry"]
        name, line = _find_enclosing_decl(lines, 2)
        assert name == "bar"
        assert line == 1

    def test_def(self) -> None:
        lines = ["def baz : Nat := sorry"]
        name, line = _find_enclosing_decl(lines, 1)
        assert name == "baz"
        assert line == 1

    def test_instance(self) -> None:
        lines = [
            "instance myInst : Decidable True := by",
            "  sorry",
        ]
        name, line = _find_enclosing_decl(lines, 2)
        assert name == "myInst"
        assert line == 1

    def test_unknown_when_no_decl(self) -> None:
        lines = ["sorry"]
        name, _line = _find_enclosing_decl(lines, 1)
        assert name == "<unknown>"

    def test_picks_closest_decl(self) -> None:
        lines = [
            "theorem a : True := by",
            "  trivial",
            "",
            "theorem b : True := by",
            "  sorry",
        ]
        name, line = _find_enclosing_decl(lines, 5)
        assert name == "b"
        assert line == 4


# ---------------------------------------------------------------------------
# prioritize_targets
# ---------------------------------------------------------------------------


class TestPrioritizeTargets:
    """Tests for target prioritization."""

    def test_fewer_sorry_files_first(self, tmp_path: Path) -> None:
        """Files with fewer sorries should come first."""
        # File with 1 sorry
        f1 = tmp_path / "One.lean"
        f1.write_text("theorem a : True := by\n  sorry\n", encoding="utf-8")

        # File with 3 sorries
        f3 = tmp_path / "Three.lean"
        f3.write_text(
            "theorem x : True := by\n  sorry\n\n"
            "theorem y : True := by\n  sorry\n\n"
            "theorem z : True := by\n  sorry\n",
            encoding="utf-8",
        )

        t1 = scan_file(f1)
        t3 = scan_file(f3)
        combined = t3 + t1  # put 3-sorry file first deliberately
        result = prioritize_targets(combined)

        # The single-sorry file's target should come first
        assert result[0].file == f1

    def test_within_file_sorted_by_line(self, lean_file_multi_sorry: Path) -> None:
        """Within the same file, targets are sorted by line number."""
        targets = scan_file(lean_file_multi_sorry)
        result = prioritize_targets(targets)
        lines = [t.line for t in result]
        assert lines == sorted(lines)

    def test_empty_list(self) -> None:
        assert prioritize_targets([]) == []
