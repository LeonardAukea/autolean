"""Comprehensive CLI interface tests — prove every public command works."""

from __future__ import annotations

import json
from pathlib import Path
from unittest.mock import patch

import pytest
from click.testing import CliRunner

from autolean.__main__ import main


@pytest.fixture
def runner() -> CliRunner:
    return CliRunner()


@pytest.fixture
def project_dir(tmp_path: Path) -> Path:
    """Create a minimal AutoLean project for testing."""
    ws = tmp_path / "workspace"
    ws.mkdir()
    al = ws / "AutoLean"
    al.mkdir()

    # lakefile.lean
    (ws / "lakefile.lean").write_text(
        "import Lake\nopen Lake DSL\n"
        "package test_ws\n"
        "@[default_target]\nlean_lib AutoLean where srcDir := \".\"\n"
    )
    (ws / "lean-toolchain").write_text("leanprover/lean4:v4.29.0\n")

    # A lean file with sorrys
    (al / "Test.lean").write_text(
        "theorem test_rfl : 1 + 1 = 2 := by\n  sorry\n\n"
        "theorem test_impl (P Q : Prop) (h : P) (f : P -> Q) : Q := by\n  sorry\n"
    )
    (ws / "AutoLean.lean").write_text("import AutoLean.Test\n")

    # program.md
    (tmp_path / "program.md").write_text(
        "# Test\n\n## Mode\nsorry-elimination\n\n"
        "## Lean Project Path\nworkspace\n\n"
        "## LLM Configuration\nmodel: gemma4:26b\ntemperature: 0.4\n"
        "max_retries_per_sorry: 3\ncycle_timeout_seconds: 60\nmax_cycles: 0\n"
    )

    return tmp_path


# ---------------------------------------------------------------------------
# Version and help
# ---------------------------------------------------------------------------


class TestCLIBasics:
    def test_version(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["--version"])
        assert result.exit_code == 0
        assert "0.5.0" in result.output or "autolean" in result.output

    def test_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["--help"])
        assert result.exit_code == 0
        assert "prove" in result.output
        assert "verify" in result.output
        assert "scan" in result.output
        assert "models" in result.output
        assert "improve" in result.output

    def test_run_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["run", "--help"])
        assert result.exit_code == 0
        assert "--max-cycles" in result.output
        assert "--model" in result.output
        assert "--resume" in result.output

    def test_prove_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["prove", "--help"])
        assert result.exit_code == 0
        assert "STATEMENT" in result.output
        assert "--max-attempts" in result.output

    def test_verify_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["verify", "--help"])
        assert result.exit_code == 0
        assert "SOURCE" in result.output
        assert "--pages" in result.output

    def test_improve_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["improve", "--help"])
        assert result.exit_code == 0
        assert "FILE_PATH" in result.output
        assert "THEOREM_NAME" in result.output
        assert "--goal" in result.output
        assert "shorter" in result.output
        assert "elegant" in result.output

    def test_models_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["models", "--help"])
        assert result.exit_code == 0

    def test_init_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["init", "--help"])
        assert result.exit_code == 0
        assert "--mathlib" in result.output

    def test_finetune_config_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["finetune-config", "--help"])
        assert result.exit_code == 0
        assert "--framework" in result.output
        assert "axolotl" in result.output

    def test_export_training_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["export-training", "--help"])
        assert result.exit_code == 0

    def test_diff_help(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["diff", "--help"])
        assert result.exit_code == 0


# ---------------------------------------------------------------------------
# Scan command (works without LLM)
# ---------------------------------------------------------------------------


class TestScanCommand:
    def test_scan_finds_targets(self, runner: CliRunner, project_dir: Path) -> None:
        result = runner.invoke(main, ["scan", "-d", str(project_dir / "workspace")])
        assert result.exit_code == 0
        assert "sorry target" in result.output
        assert "test_rfl" in result.output
        assert "test_impl" in result.output

    def test_scan_json_output(self, runner: CliRunner, project_dir: Path) -> None:
        result = runner.invoke(main, ["scan", "-d", str(project_dir / "workspace"), "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert len(data) == 2
        names = {d["decl_name"] for d in data}
        assert "test_rfl" in names
        assert "test_impl" in names

    def test_scan_json_has_tactic_mode(self, runner: CliRunner, project_dir: Path) -> None:
        result = runner.invoke(main, ["scan", "-d", str(project_dir / "workspace"), "--json"])
        data = json.loads(result.output)
        for item in data:
            assert "tactic_mode" in item
            assert "difficulty" in item

    def test_scan_empty_project(self, runner: CliRunner, tmp_path: Path) -> None:
        ws = tmp_path / "empty"
        ws.mkdir()
        (ws / "lakefile.lean").write_text("import Lake\nopen Lake DSL\npackage empty\n")
        (ws / "lean-toolchain").write_text("leanprover/lean4:v4.29.0\n")
        result = runner.invoke(main, ["scan", "-d", str(ws)])
        assert result.exit_code == 0
        assert "0 sorry" in result.output


# ---------------------------------------------------------------------------
# Init command
# ---------------------------------------------------------------------------


class TestInitCommand:
    def test_init_creates_project(self, runner: CliRunner, tmp_path: Path) -> None:
        target = tmp_path / "my_project"
        result = runner.invoke(main, ["init", str(target)])
        assert result.exit_code == 0
        assert (target / "lakefile.lean").exists()
        assert (target / "lean-toolchain").exists()
        assert (target / "my_project.lean").exists()
        # Check lakefile content
        lakefile = (target / "lakefile.lean").read_text()
        assert "my_project" in lakefile

    def test_init_with_mathlib(self, runner: CliRunner, tmp_path: Path) -> None:
        target = tmp_path / "math_proj"
        result = runner.invoke(main, ["init", str(target), "--mathlib"])
        assert result.exit_code == 0
        lakefile = (target / "lakefile.lean").read_text()
        assert "mathlib" in lakefile


# ---------------------------------------------------------------------------
# Models command (works without LLM if Ollama is down)
# ---------------------------------------------------------------------------


class TestModelsCommand:
    def test_models_lists_profiles(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["models"])
        # Should complete even if Ollama is down (shows "not installed")
        assert result.exit_code == 0
        assert "gemma4" in result.output
        assert "deepseek-prover" in result.output

    def test_models_shows_install_commands(self, runner: CliRunner) -> None:
        result = runner.invoke(main, ["models"])
        assert result.exit_code == 0
        assert "ollama pull" in result.output


# ---------------------------------------------------------------------------
# Finetune config
# ---------------------------------------------------------------------------


class TestFinetuneConfigCommand:
    def test_generates_axolotl_config(self, runner: CliRunner, project_dir: Path) -> None:
        result = runner.invoke(main, [
            "finetune-config",
            "-d", str(project_dir / "workspace"),
            "--framework", "axolotl",
        ])
        assert result.exit_code == 0
        assert "axolotl" in result.output.lower()
        config_file = project_dir / "workspace" / "training_data" / "axolotl_config.yaml"
        assert config_file.exists()

    def test_generates_trl_config(self, runner: CliRunner, project_dir: Path) -> None:
        result = runner.invoke(main, [
            "finetune-config",
            "-d", str(project_dir / "workspace"),
            "--framework", "trl",
        ])
        assert result.exit_code == 0
        assert "dpo" in result.output.lower()


# ---------------------------------------------------------------------------
# Results command
# ---------------------------------------------------------------------------


class TestResultsCommand:
    def test_results_no_file(self, runner: CliRunner, tmp_path: Path) -> None:
        result = runner.invoke(main, ["results", "-f", str(tmp_path / "noexist.tsv")])
        assert result.exit_code == 0
        assert "No results" in result.output

    def test_results_with_data(self, runner: CliRunner, tmp_path: Path) -> None:
        tsv = tmp_path / "results.tsv"
        tsv.write_text(
            "cycle\ttimestamp\ttarget_id\tdecl_name\tfile\tline\t"
            "outcome\tattempt\tduration_s\tllm_tokens\tllm_tok_s\t"
            "proof_lines\terror_category\tbuild_s\terror\n"
            "1\t2026-01-01\tt1\tfoo\tF.lean\t10\tsuccess\t1\t5.0\t100\t50.0\t1\t\t0.5\t\n"
        )
        result = runner.invoke(main, ["results", "-f", str(tsv)])
        assert result.exit_code == 0
        assert "foo" in result.output
        assert "success" in result.output
