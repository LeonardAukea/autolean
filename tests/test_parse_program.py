"""Tests for the typed `program.md` parser."""

from __future__ import annotations

from pathlib import Path

import pytest

from autolean.models import DEFAULT_PROFILE
from autolean.program import parse_program
from autolean.routing import EscalationPolicy

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
        assert cfg.goals

    def test_constraints_parsed(self, program_md: Path) -> None:
        cfg = parse_program(program_md)
        assert cfg.constraints

    def test_strategy_hints_parsed(self, program_md: Path) -> None:
        cfg = parse_program(program_md)
        assert cfg.strategy_hints

    def test_parse_real_program_md(self) -> None:
        """Parse the actual program.md shipped with the project."""
        real = Path(__file__).resolve().parents[1] / "program.md"
        if not real.exists():
            pytest.skip("program.md not present in repo")
        cfg = parse_program(real)
        assert cfg.mode == "sorry-elimination"
        assert cfg.lean_project_path == "workspace"
        assert cfg.model == DEFAULT_PROFILE
        assert cfg.temperature == pytest.approx(0.0)
        assert cfg.max_retries_per_sorry == 5
        assert cfg.max_proof_lines == 30
        assert cfg.escalation_policy is EscalationPolicy.ASK
        assert cfg.escalation_after_failures == 2
        assert len(cfg.strategy_hints) >= 1
        # The shipped program.md documents each key in an HTML comment; those
        # must not be mistaken for settings.
        assert cfg.llm_config().backend == "claude_cli"

    def test_html_comments_are_not_parsed_as_settings(self, tmp_path: Path) -> None:
        p = tmp_path / "commented.md"
        p.write_text(
            "# Prog\n\n<!-- model: gemma4:26b\ntemperature: ignored -->\nmodel: sonnet\ntemperature: 0.7\n",
            encoding="utf-8",
        )
        cfg = parse_program(p)
        assert cfg.model == "sonnet"
        assert cfg.temperature == pytest.approx(0.7)


# ---------------------------------------------------------------------------
# Backend selection
# ---------------------------------------------------------------------------


class TestBackendSelection:
    """program.md decides which backend the agent talks to."""

    def _write(self, tmp_path: Path, body: str) -> Path:
        p = tmp_path / "program.md"
        p.write_text(f"# Prog\n\n## LLM Configuration\n\n{body}", encoding="utf-8")
        return p

    def test_profile_name_resolves_to_its_backend(self, tmp_path: Path) -> None:
        cfg = parse_program(self._write(tmp_path, "model: opus\n"))
        assert cfg.llm_config().backend == "claude_cli"

    def test_explicit_backend_overrides_the_profile(self, tmp_path: Path) -> None:
        cfg = parse_program(self._write(tmp_path, "model: opus\nbackend: ollama\n"))
        assert cfg.llm_config().backend == "ollama"
        assert cfg.llm_config().effort is None

    def test_explicit_incompatible_backend_control_is_rejected(self, tmp_path: Path) -> None:
        cfg = parse_program(self._write(tmp_path, "model: opus\nbackend: ollama\neffort: high\n"))
        with pytest.raises(ValueError, match="reasoning effort"):
            cfg.llm_config()

    def test_effort_is_passed_through(self, tmp_path: Path) -> None:
        cfg = parse_program(self._write(tmp_path, "model: opus-api\neffort: max\n"))
        assert cfg.llm_config().effort == "max"

    def test_openai_none_effort_is_supported(self, tmp_path: Path) -> None:
        cfg = parse_program(self._write(tmp_path, "model: gpt-api\neffort: none\n"))
        assert cfg.llm_config().effort == "none"

    def test_backend_specific_effort_is_rejected(self, tmp_path: Path) -> None:
        cfg = parse_program(self._write(tmp_path, "model: opus-api\neffort: none\n"))
        with pytest.raises(ValueError, match="reasoning effort"):
            cfg.llm_config()

    def test_endpoint_is_passed_through(self, tmp_path: Path) -> None:
        cfg = parse_program(
            self._write(
                tmp_path,
                "model: local\nbackend: openai_compat\nendpoint: http://127.0.0.1:8000\n",
            )
        )
        assert cfg.llm_config().base_url == "http://127.0.0.1:8000"

    def test_raw_ollama_tag_keeps_the_local_backend(self, tmp_path: Path) -> None:
        cfg = parse_program(self._write(tmp_path, "model: gemma4:26b\n"))
        resolved = cfg.llm_config()
        assert (resolved.model, resolved.backend) == ("gemma4:26b", "ollama")

    def test_max_output_tokens_is_read(self, tmp_path: Path) -> None:
        cfg = parse_program(self._write(tmp_path, "max_output_tokens: 4096\n"))
        assert cfg.llm_config().max_output_tokens == 4096

    def test_num_predict_is_accepted_as_the_ollama_spelling(self, tmp_path: Path) -> None:
        cfg = parse_program(self._write(tmp_path, "num_predict: 512\n"))
        assert cfg.llm_config().max_output_tokens == 512

    def test_llm_timeout_is_read(self, tmp_path: Path) -> None:
        cfg = parse_program(self._write(tmp_path, "llm_timeout_seconds: 90\n"))
        assert cfg.llm_config().timeout == pytest.approx(90.0)

    def test_model_escalation_policy_is_read(self, tmp_path: Path) -> None:
        cfg = parse_program(
            self._write(
                tmp_path,
                "escalation_policy: auto\nescalation_model: fable\nescalation_after_failures: 3\n",
            )
        )
        assert cfg.escalation_policy is EscalationPolicy.AUTO
        assert cfg.escalation_model == "fable"
        assert cfg.escalation_after_failures == 3

    def test_backend_is_unset_when_absent(self, tmp_path: Path) -> None:
        cfg = parse_program(self._write(tmp_path, "model: opus\n"))
        assert cfg.backend is None

    @pytest.mark.parametrize(
        "body",
        [
            "max_retries_per_sorry: 0\n",
            "cycle_timeout_seconds: 0\n",
            "max_cycles: -1\n",
            "max_proof_lines: 0\n",
            "temperature: -0.1\n",
            "temperature: nan\n",
            "temperature: 2.1\n",
            "max_output_tokens: 0\n",
            "llm_timeout_seconds: -1\n",
            "llm_timeout_seconds: inf\n",
            "effort: impossible\n",
            "endpoint: file:///tmp/model\n",
            "escalation_policy: eager\n",
            "escalation_after_failures: 0\n",
        ],
    )
    def test_invalid_program_policy_is_rejected(self, tmp_path: Path, body: str) -> None:
        with pytest.raises(ValueError):
            parse_program(self._write(tmp_path, body))


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
        assert cfg.model == DEFAULT_PROFILE
        assert cfg.temperature == pytest.approx(0.4)
        assert cfg.max_retries_per_sorry == 5
        assert cfg.cycle_timeout_seconds == 120
        assert cfg.max_cycles == 5
        assert cfg.escalation_policy is EscalationPolicy.ASK
        assert cfg.escalation_model is None
        assert cfg.escalation_after_failures == 2
        assert cfg.goals == []
        assert cfg.constraints == []
        assert cfg.strategy_hints == []

    def test_unsupported_mode_is_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "minimal.md"
        p.write_text(
            "# Prog\n\n## Mode\n\n<!-- c -->\nproof-golf\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="unsupported agent mode"):
            parse_program(p)

    def test_bad_temperature_is_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "bad.md"
        p.write_text(
            "# Prog\n\ntemperature: not_a_float\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="temperature must be numeric"):
            parse_program(p)

    def test_bad_max_retries_is_rejected(self, tmp_path: Path) -> None:
        p = tmp_path / "bad2.md"
        p.write_text(
            "# Prog\n\nmax_retries_per_sorry: abc\n",
            encoding="utf-8",
        )
        with pytest.raises(ValueError, match="max_retries_per_sorry must be an integer"):
            parse_program(p)


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
