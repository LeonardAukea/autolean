"""Tests for the subscription-backed CLI backends.

The vendor CLIs are replaced with a recording fake, so these tests assert the
argv AutoLean builds and the parsing of each CLI's real output shape.
"""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pytest

from autolean.llm import LLMConfig, LLMError
from autolean.llm import subscription as sub

# One real `claude -p --output-format json` envelope, trimmed to the fields
# AutoLean reads.
CLAUDE_JSON = json.dumps(
    {
        "type": "result",
        "subtype": "success",
        "is_error": False,
        "result": "  norm_num  ",
        "total_cost_usd": 0.089,
        "usage": {"input_tokens": 2, "output_tokens": 8},
        "modelUsage": {"claude-sonnet-5": {"inputTokens": 2}},
    }
)

# One real `codex exec --json` event stream.
CODEX_JSONL = "\n".join(
    [
        '{"type": "thread.started", "thread_id": "t1"}',
        '{"type": "turn.started"}',
        '{"type": "item.completed", "item": {"id": "i0", "type": "reasoning"}}',
        '{"type": "item.completed", "item": {"id": "i1", "type": "agent_message", "text": " by norm_num "}}',
        '{"type": "turn.completed", "usage": {"input_tokens": 20306,'
        ' "output_tokens": 7, "cached_input_tokens": 2816}}',
    ]
)


@dataclass
class FakeRun:
    """Stands in for `subprocess.run`, recording how it was called."""

    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    raises: BaseException | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        self.calls.append({"argv": argv, **kwargs})
        if self.raises is not None:
            raise self.raises
        return subprocess.CompletedProcess(argv, self.returncode, self.stdout, self.stderr)

    @property
    def argv(self) -> list[str]:
        return self.calls[-1]["argv"]

    @property
    def stdin_text(self) -> str:
        return self.calls[-1]["input"]

    def flag(self, name: str) -> str:
        """The value passed after `name` in the recorded argv."""
        return self.argv[self.argv.index(name) + 1]

    def flags(self, name: str) -> list[str]:
        """Every value passed after a repeatable flag."""
        return [self.argv[index + 1] for index, value in enumerate(self.argv[:-1]) if value == name]


@pytest.fixture()
def fake_run(monkeypatch: pytest.MonkeyPatch) -> FakeRun:
    runner = FakeRun()
    monkeypatch.setattr(sub.subprocess, "run", runner)
    return runner


def claude(**overrides: Any) -> sub.ClaudeCodeClient:
    config = LLMConfig(model="opus", backend="claude_cli", **overrides)
    return sub.ClaudeCodeClient(config=config)


def codex(**overrides: Any) -> sub.CodexClient:
    config = LLMConfig(model="gpt-5.6-sol", backend="codex_cli", **overrides)
    return sub.CodexClient(config=config)


# ---------------------------------------------------------------------------
# claude CLI
# ---------------------------------------------------------------------------


class TestClaudeCodeClient:
    def test_parses_result_and_usage(self, fake_run: FakeRun) -> None:
        fake_run.stdout = CLAUDE_JSON
        resp = claude().generate("be terse", "prove 1+1=2")
        assert resp.text == "norm_num"
        assert (resp.input_tokens, resp.output_tokens) == (2, 8)
        assert resp.model == "claude-sonnet-5"
        assert resp.cost_usd is None

    def test_identifies_the_model_that_generated_a_multi_model_response(
        self,
        fake_run: FakeRun,
    ) -> None:
        fake_run.stdout = json.dumps(
            {
                "type": "result",
                "subtype": "success",
                "is_error": False,
                "result": "trivial",
                "usage": {"input_tokens": 179, "output_tokens": 4},
                "modelUsage": {
                    "claude-haiku-4-5-20251001": {
                        "inputTokens": 520,
                        "outputTokens": 11,
                    },
                    "claude-opus-5": {
                        "inputTokens": 179,
                        "outputTokens": 4,
                    },
                },
            }
        )

        response = claude().generate("be terse", "prove True")

        assert response.model == "claude-opus-5"

    def test_runs_in_print_mode_with_json_output(self, fake_run: FakeRun) -> None:
        fake_run.stdout = CLAUDE_JSON
        claude().generate("be terse", "prove 1+1=2")
        assert fake_run.argv[0] == "claude"
        assert "--print" in fake_run.argv
        assert fake_run.flag("--output-format") == "json"
        assert fake_run.flag("--model") == "opus"

    def test_disables_tools_and_customizations(self, fake_run: FakeRun) -> None:
        fake_run.stdout = CLAUDE_JSON
        claude().generate("be terse", "prove 1+1=2")
        assert fake_run.flag("--tools") == ""
        assert "--safe-mode" in fake_run.argv
        assert "--strict-mcp-config" in fake_run.argv
        assert "--disable-slash-commands" in fake_run.argv
        assert "--no-session-persistence" in fake_run.argv
        assert "--no-chrome" in fake_run.argv

    def test_system_prompt_and_user_prompt_are_separated(self, fake_run: FakeRun) -> None:
        fake_run.stdout = CLAUDE_JSON
        claude().generate("be terse", "prove 1+1=2")
        assert fake_run.flag("--system-prompt") == "be terse"
        assert "--append-system-prompt" not in fake_run.argv
        assert fake_run.stdin_text == "prove 1+1=2"

    def test_passes_reasoning_effort(self, fake_run: FakeRun) -> None:
        fake_run.stdout = CLAUDE_JSON
        claude(effort="max").generate("s", "u")
        assert fake_run.flag("--effort") == "max"

    def test_runs_from_an_empty_scratch_directory(self, fake_run: FakeRun) -> None:
        fake_run.stdout = CLAUDE_JSON
        claude().generate("s", "u")
        scratch = Path(fake_run.calls[-1]["cwd"])
        assert scratch.name.startswith("autolean-llm-")
        assert not scratch.exists()

    def test_reported_error_becomes_llm_error(self, fake_run: FakeRun) -> None:
        fake_run.stdout = json.dumps({"is_error": True, "result": "rate limited"})
        with pytest.raises(LLMError, match="rate limited"):
            claude().generate("s", "u")

    def test_non_zero_exit_becomes_llm_error(self, fake_run: FakeRun) -> None:
        fake_run.returncode = 1
        fake_run.stderr = "not logged in"
        with pytest.raises(LLMError, match="exited 1: not logged in"):
            claude().generate("s", "u")

    def test_quota_message_is_surfaced_not_the_json_envelope(self, fake_run: FakeRun) -> None:
        """`claude` exits non-zero but explains itself inside the envelope."""
        fake_run.returncode = 1
        fake_run.stdout = json.dumps(
            {
                "is_error": True,
                "result": "You've hit your weekly limit · resets Aug 11 at 4pm",
                "api_error_status": 429,
                "usage": {},
            }
        )
        with pytest.raises(LLMError, match=r"weekly limit .* \(HTTP 429\)"):
            claude().generate("s", "u")

    def test_non_json_output_becomes_llm_error(self, fake_run: FakeRun) -> None:
        fake_run.stdout = "Welcome to Claude Code!"
        with pytest.raises(LLMError, match="non-JSON output"):
            claude().generate("s", "u")

    def test_non_object_json_becomes_llm_error(self, fake_run: FakeRun) -> None:
        fake_run.stdout = "[]"
        with pytest.raises(LLMError, match="JSON value"):
            claude().generate("s", "u")

    def test_empty_result_becomes_llm_error(self, fake_run: FakeRun) -> None:
        fake_run.stdout = json.dumps({"is_error": False, "result": "", "usage": {}})
        with pytest.raises(LLMError, match="empty completion"):
            claude().generate("s", "u")

    def test_timeout_becomes_llm_error(self, fake_run: FakeRun) -> None:
        fake_run.raises = subprocess.TimeoutExpired(cmd="claude", timeout=1.0)
        with pytest.raises(LLMError, match="timed out"):
            claude(timeout=30.0).generate("s", "u")

    def test_missing_binary_becomes_llm_error(self, fake_run: FakeRun) -> None:
        fake_run.raises = FileNotFoundError("no claude")
        with pytest.raises(LLMError, match="could not be executed"):
            claude().generate("s", "u")

    def test_kill_grace_exceeds_the_request_timeout(self, fake_run: FakeRun) -> None:
        """The CLI gets a moment past its own deadline before being killed."""
        fake_run.stdout = CLAUDE_JSON
        claude(timeout=120.0).generate("s", "u")
        assert fake_run.calls[-1]["timeout"] == 120.0 + sub.KILL_GRACE_SECONDS


# ---------------------------------------------------------------------------
# codex CLI
# ---------------------------------------------------------------------------


class TestCodexClient:
    def test_takes_the_last_agent_message_and_usage(self, fake_run: FakeRun) -> None:
        fake_run.stdout = CODEX_JSONL
        resp = codex().generate("be terse", "prove 1+1=2")
        assert resp.text == "by norm_num"
        assert (resp.input_tokens, resp.output_tokens) == (20306, 7)

    def test_runs_read_only_and_ephemeral(self, fake_run: FakeRun) -> None:
        fake_run.stdout = CODEX_JSONL
        codex().generate("s", "u")
        assert fake_run.argv[:2] == ["codex", "exec"]
        assert fake_run.flag("--sandbox") == "read-only"
        assert "--ephemeral" in fake_run.argv
        assert "--skip-git-repo-check" in fake_run.argv
        assert fake_run.argv[-1] == "-"

    def test_disables_codex_action_surfaces(self, fake_run: FakeRun) -> None:
        fake_run.stdout = CODEX_JSONL
        codex().generate("s", "u")
        assert "--strict-config" in fake_run.argv
        assert "--ignore-user-config" in fake_run.argv
        assert "--ignore-rules" in fake_run.argv
        overrides = set(fake_run.flags("--config"))
        assert {
            'approval_policy="never"',
            "agents.enabled=false",
            "apps._default.enabled=false",
            "features.shell_tool=false",
            "features.unified_exec=false",
            'web_search="disabled"',
            "project_doc_max_bytes=0",
        } <= overrides

    def test_passes_reasoning_effort(self, fake_run: FakeRun) -> None:
        fake_run.stdout = CODEX_JSONL
        codex(effort="xhigh").generate("s", "u")
        assert 'model_reasoning_effort="xhigh"' in fake_run.flags("--config")

    def test_system_prompt_rides_in_the_prompt(self, fake_run: FakeRun) -> None:
        """`codex exec` has no system-prompt flag."""
        fake_run.stdout = CODEX_JSONL
        codex().generate("be terse", "prove 1+1=2")
        assert fake_run.stdin_text == "be terse\n\n---\n\nprove 1+1=2"

    def test_turn_failure_becomes_llm_error(self, fake_run: FakeRun) -> None:
        fake_run.stdout = '{"type": "turn.failed", "error": {"message": "quota"}}'
        with pytest.raises(LLMError, match="quota"):
            codex().generate("s", "u")

    def test_no_agent_message_becomes_llm_error(self, fake_run: FakeRun) -> None:
        fake_run.stdout = '{"type": "turn.completed", "usage": {}}'
        with pytest.raises(LLMError, match="produced no message"):
            codex().generate("s", "u")

    def test_truncated_stream_becomes_llm_error(self, fake_run: FakeRun) -> None:
        fake_run.stdout = '{"type":"item.completed","item":{"type":"agent_message","text":"rfl"}}'
        with pytest.raises(LLMError, match=r"turn\.completed"):
            codex().generate("s", "u")

    def test_non_json_lines_are_skipped(self, fake_run: FakeRun) -> None:
        """The CLI prints a banner before the event stream."""
        fake_run.stdout = "OpenAI Codex v0.147.0\n--------\n" + CODEX_JSONL
        assert codex().generate("s", "u").text == "by norm_num"

    def test_non_object_json_lines_are_skipped(self, fake_run: FakeRun) -> None:
        fake_run.stdout = "[]\n42\n" + CODEX_JSONL
        assert codex().generate("s", "u").text == "by norm_num"

    def test_malformed_nested_events_are_skipped(self, fake_run: FakeRun) -> None:
        fake_run.stdout = (
            '{"type":"item.completed","item":[]}\n{"type":"turn.completed","usage":[]}\n' + CODEX_JSONL
        )
        assert codex().generate("s", "u").text == "by norm_num"


# ---------------------------------------------------------------------------
# Shared CLI behaviour
# ---------------------------------------------------------------------------


class TestCliBackend:
    def test_ping_false_when_binary_is_absent(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sub.shutil, "which", lambda _: None)
        assert claude().ping() is False

    def test_ping_true_when_authentication_preflight_succeeds(
        self, monkeypatch: pytest.MonkeyPatch, fake_run: FakeRun
    ) -> None:
        monkeypatch.setattr(sub.shutil, "which", lambda _: "/usr/bin/claude")
        fake_run.stdout = json.dumps(
            {
                "loggedIn": True,
                "authMethod": "claude.ai",
                "subscriptionType": "max",
            }
        )
        assert claude().ping() is True
        assert fake_run.argv == ["claude", "auth", "status", "--json"]

    def test_codex_ping_checks_login_status(self, monkeypatch: pytest.MonkeyPatch, fake_run: FakeRun) -> None:
        monkeypatch.setattr(sub.shutil, "which", lambda _: "/usr/bin/codex")
        fake_run.stdout = "Logged in using ChatGPT"
        assert codex().ping() is True
        assert fake_run.argv == ["codex", "login", "status"]

    def test_api_billing_modes_are_rejected(self, monkeypatch: pytest.MonkeyPatch, fake_run: FakeRun) -> None:
        monkeypatch.setattr(sub.shutil, "which", lambda name: f"/usr/bin/{name}")
        fake_run.stdout = json.dumps(
            {
                "loggedIn": True,
                "authMethod": "console",
                "subscriptionType": "api",
            }
        )
        assert claude().ping() is False
        fake_run.stdout = "Logged in using an API key"
        assert codex().ping() is False

    def test_provider_api_keys_are_removed_from_child_environment(
        self, monkeypatch: pytest.MonkeyPatch, fake_run: FakeRun
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token")
        monkeypatch.setenv("OPENAI_API_KEY", "secret")
        fake_run.stdout = CLAUDE_JSON
        claude().generate("s", "u")
        assert "ANTHROPIC_API_KEY" not in fake_run.calls[-1]["env"]
        assert "ANTHROPIC_AUTH_TOKEN" not in fake_run.calls[-1]["env"]
        fake_run.stdout = CODEX_JSONL
        codex().generate("s", "u")
        assert "OPENAI_API_KEY" not in fake_run.calls[-1]["env"]

    def test_ping_false_when_version_fails(self, monkeypatch: pytest.MonkeyPatch, fake_run: FakeRun) -> None:
        monkeypatch.setattr(sub.shutil, "which", lambda _: "/usr/bin/codex")
        fake_run.returncode = 127
        assert codex().ping() is False

    def test_binary_is_overridable_by_environment(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("AUTOLEAN_CLAUDE_BIN", "/opt/pinned/claude")
        assert claude().resolved_binary() == "/opt/pinned/claude"

    def test_capabilities_exclude_temperature(self) -> None:
        assert claude().capabilities.temperature is False
        assert codex().capabilities.temperature is False

    def test_capabilities_include_effort_and_exclude_stops(self) -> None:
        assert claude().capabilities.effort is True
        assert codex().capabilities.effort is True
        assert claude().capabilities.stop_sequences is False
        assert codex().capabilities.stop_sequences is False
        assert claude().capabilities.output_limit is False
        assert codex().capabilities.output_limit is False
