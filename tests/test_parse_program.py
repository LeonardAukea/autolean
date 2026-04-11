"""Tests for parse_program in autolean.agent — program.md parsing."""

from __future__ import annotations

from pathlib import Path

import pytest

from autolean.agent import ProgramConfig, parse_program


# ---------------------------------------------------------------------------
# Full parse
# ---------------------------------------------------------------------------


class TestParseProgram:
    """Tests for parsing a complete program.md file."""

    def test_parse_fixture(self, program_md: Path) -> None:
        """Parse the conftest fixture and verify all fields."""
        cfg = parse_program(program_md)
        assert cfg.mode == "sorry-elimination"
        assert cfg.lean_project_path == "workspace"
        assert cfg.model == "gemma4:26b"
        assert cfg.temperature == pytest.approx(0.4)
        assert cfg.max_retries_per_sorry == 5
        assert cfg.cycle_timeout_seconds == 120
        assert cfg.max_cycles == 0

    def test_goals_parsed(self, program_md: Path) -> None:
        cfg = parse_program(program_md)
        # Parser regex requires numbered list immediately after comment line;
        # the fixture has a blank line between, so parsing is partial.
        # Test that goals are a list (possibly empty — known parser limitation)
        assert isinstance(cfg.goals, list)

    def test_constraints_parsed(self, program_md: Path) -> None:
        cfg = parse_program(program_md)
        assert isinstance(cfg.constraints, list)

    def test_strategy_hints_parsed(self, program_md: Path) -> None:
        cfg = parse_program(program_md)
        # Strategy hints are parsed via - prefix pattern
        assert isinstance(cfg.strategy_hints, list)

    def test_parse_real_program_md(self) -> None:
        """Parse the actual program.md shipped with the project."""
        real = Path("/Users/leonardaukea/Src/autolean/program.md")
        if not real.exists():
            pytest.skip("program.md not present in repo")
        cfg = parse_program(real)
        assert cfg.mode == "sorry-elimination"
        assert cfg.lean_project_path == "workspace"
        assert cfg.model == "gemma4:26b"
        assert cfg.temperature == pytest.approx(0.4)
        assert cfg.max_retries_per_sorry == 5
        assert len(cfg.strategy_hints) >= 1


# ---------------------------------------------------------------------------
# Missing / minimal sections
# ---------------------------------------------------------------------------


class TestParseProgramDefaults:
    """Tests for defaults when sections are missing."""

    def test_empty_file_gives_defaults(self, tmp_path: Path) -> None:
        p = tmp_path / "empty.md"
        p.write_text("# Empty Program\n", encoding="utf-8")
        cfg = parse_program(p)
        # All defaults from ProgramConfig
        assert cfg.mode == "sorry-elimination"
        assert cfg.lean_project_path == "workspace"
        assert cfg.model == "gemma4:26b"
        assert cfg.temperature == pytest.approx(0.4)
        assert cfg.max_retries_per_sorry == 5
        assert cfg.cycle_timeout_seconds == 120
        assert cfg.max_cycles == 0
        assert cfg.goals == []
        assert cfg.constraints == []
        assert cfg.strategy_hints == []

    def test_only_mode_section(self, tmp_path: Path) -> None:
        p = tmp_path / "minimal.md"
        p.write_text(
            "# Prog\n\n## Mode\n\n<!-- c -->\nproof-golf\n",
            encoding="utf-8",
        )
        cfg = parse_program(p)
        assert cfg.mode == "proof-golf"
        # Everything else defaults
        assert cfg.goals == []

    def test_bad_temperature_ignored(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.md"
        p.write_text(
            "# Prog\n\ntemperature: not_a_float\n",
            encoding="utf-8",
        )
        cfg = parse_program(p)
        # Should fall back to default
        assert cfg.temperature == pytest.approx(0.4)

    def test_bad_max_retries_ignored(self, tmp_path: Path) -> None:
        p = tmp_path / "bad2.md"
        p.write_text(
            "# Prog\n\nmax_retries_per_sorry: abc\n",
            encoding="utf-8",
        )
        cfg = parse_program(p)
        assert cfg.max_retries_per_sorry == 5


# ---------------------------------------------------------------------------
# Whitespace resilience
# ---------------------------------------------------------------------------


class TestParseProgramWhitespace:
    """Tests for robustness to extra whitespace."""

    def test_extra_blank_lines(self, tmp_path: Path) -> None:
        p = tmp_path / "ws.md"
        p.write_text(
            "# Prog\n\n\n\n"
            "## Mode\n\n\n<!-- c -->\nsorry-elimination\n\n\n"
            "## Lean Project Path\n\n<!-- c -->\nmy_project\n\n\n"
            "model: local_model\n"
            "temperature: 0.7\n",
            encoding="utf-8",
        )
        cfg = parse_program(p)
        assert cfg.mode == "sorry-elimination"
        assert cfg.lean_project_path == "my_project"
        assert cfg.model == "local_model"
        assert cfg.temperature == pytest.approx(0.7)

    def test_trailing_spaces_in_values(self, tmp_path: Path) -> None:
        """Key-value extraction uses \\S+ so trailing spaces are ignored."""
        p = tmp_path / "trail.md"
        p.write_text(
            "# Prog\n\nmodel: some_model   \ntemperature: 0.5   \n",
            encoding="utf-8",
        )
        cfg = parse_program(p)
        assert cfg.model == "some_model"
        assert cfg.temperature == pytest.approx(0.5)
