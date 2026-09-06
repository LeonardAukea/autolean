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

from autolean.llm import LLMAuthenticationError, LLMConfig, LLMError
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

# One real `grok --output-format streaming-json` stream. Planning text is
# followed by a tool call, then the tactic that AutoLean should keep.
GROK_JSONL = "\n".join(
    [
        '{"type": "thought", "data": "fill the proof"}',
        '{"type": "text", "data": "I\'ll locate the theorem."}',
        '{"type": "tool_call", "toolName": "read_file"}',
        '{"type": "text", "data": "  trivial  "}',
        '{"type": "end", "stopReason": "end_turn", "usage": {"input_tokens": 12,'
        ' "output_tokens": 4}, "modelUsage": {"grok-4.6-build": {"inputTokens": 12,'
        ' "outputTokens": 4}}}',
    ]
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
    """Records calls to the shared process runner."""

    stdout: str = ""
    stderr: str = ""
    returncode: int = 0
    raises: BaseException | None = None
    calls: list[dict[str, Any]] = field(default_factory=list)
    cwd_names: list[str] = field(default_factory=list)
    prompt_file_text: str | None = None

    def __call__(self, argv: list[str], **kwargs: Any) -> subprocess.CompletedProcess[str]:
        cwd = kwargs.get("cwd")
        if cwd:
            self.cwd_names = [path.name for path in Path(cwd).iterdir()]
        if "--prompt-file" in argv:
            self.prompt_file_text = Path(argv[argv.index("--prompt-file") + 1]).read_text(encoding="utf-8")
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


@pytest.mark.parametrize("backend", ["claude_cli", "codex_cli", "grok_cli"])
@pytest.mark.parametrize("value", [-1, True, "4", 1.5])
def test_subscription_rejects_malformed_token_measurements(
    fake_run: FakeRun,
    backend: str,
    value: object,
) -> None:
    usage = {"input_tokens": value, "output_tokens": 1}
    if backend == "claude_cli":
        fake_run.stdout = json.dumps({"result": "trivial", "usage": usage})
        client = sub.ClaudeCodeClient(config=LLMConfig(model="sonnet", backend=backend))
    elif backend == "codex_cli":
        fake_run.stdout = "\n".join(
            [
                json.dumps({"type": "item.completed", "item": {"type": "agent_message", "text": "trivial"}}),
                json.dumps({"type": "turn.completed", "usage": usage}),
            ]
        )
        client = sub.CodexClient(config=LLMConfig(model="gpt-6-astra", backend=backend))
    else:
        fake_run.stdout = "\n".join(
            [
                json.dumps({"type": "text", "data": "trivial"}),
                json.dumps({"type": "end", "stopReason": "end_turn", "usage": usage}),
            ]
        )
        client = sub.GrokClient(config=LLMConfig(model="grok", backend=backend))
    with pytest.raises(LLMError, match="token count"):
        client.generate("system", "user")


@pytest.fixture(autouse=True)
def _clear_subscription_probe_cache() -> None:
    sub._PROBE_CACHE.clear()


@pytest.fixture()
def fake_run(monkeypatch: pytest.MonkeyPatch) -> FakeRun:
    runner = FakeRun()
    monkeypatch.setattr(sub, "run_process", runner)
    return runner


def claude(**overrides: Any) -> sub.ClaudeCodeClient:
    config = LLMConfig(model="opus", backend="claude_cli", **overrides)
    return sub.ClaudeCodeClient(config=config)


def codex(**overrides: Any) -> sub.CodexClient:
    config = LLMConfig(model="gpt-5.6-sol", backend="codex_cli", **overrides)
    return sub.CodexClient(config=config)


def grok(**overrides: Any) -> sub.GrokClient:
    config = LLMConfig(model="grok-4.6", backend="grok_cli", **overrides)
    return sub.GrokClient(config=config)


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
# grok CLI
# ---------------------------------------------------------------------------


class TestGrokClient:
    def test_keeps_text_after_tool_calls_and_usage(self, fake_run: FakeRun) -> None:
        fake_run.stdout = GROK_JSONL
        resp = grok().generate("be terse", "prove 1+1=2")
        assert resp.text == "I'll locate the theorem.\ntrivial"
        assert (resp.input_tokens, resp.output_tokens) == (12, 4)
        assert resp.model == "grok-4.6-build"

    def test_runs_streaming_json_without_tools(self, fake_run: FakeRun) -> None:
        fake_run.stdout = GROK_JSONL
        grok().generate("be terse", "prove 1+1=2")
        assert fake_run.argv[0] == "grok"
        assert fake_run.flag("--output-format") == "streaming-json"
        assert fake_run.flag("--model") == "grok-4.6"
        assert "search_tool" in fake_run.flag("--disallowed-tools")
        assert fake_run.flag("--max-turns") == "16"
        assert fake_run.flag("--permission-mode") == "dontAsk"
        assert "--no-subagents" in fake_run.argv
        assert "--no-plan" in fake_run.argv
        assert "--disable-web-search" in fake_run.argv
        assert "--verbatim" in fake_run.argv

    def test_system_prompt_and_user_prompt_are_separated(self, fake_run: FakeRun) -> None:
        fake_run.stdout = GROK_JSONL
        grok().generate("be terse", "prove 1+1=2")
        system = fake_run.flag("--system-prompt-override")
        assert system.startswith("be terse")
        assert "cannot use tools" in system
        assert fake_run.prompt_file_text == "prove 1+1=2"
        assert fake_run.stdin_text == ""

    def test_passes_reasoning_effort(self, fake_run: FakeRun) -> None:
        fake_run.stdout = GROK_JSONL
        grok(effort="xhigh").generate("s", "u")
        assert fake_run.flag("--effort") == "xhigh"

    def test_isolates_grok_home_and_copies_subscription_auth(
        self,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        fake_run: FakeRun,
    ) -> None:
        source = tmp_path / "grok-home"
        source.mkdir()
        (source / "auth.json").write_text('{"auth_mode": "oidc"}', encoding="utf-8")
        monkeypatch.setenv("GROK_HOME", str(source))
        fake_run.stdout = GROK_JSONL

        grok().generate("s", "u")

        env = fake_run.calls[-1]["env"]
        assert env["GROK_HOME"] == fake_run.calls[-1]["cwd"]
        assert env["GROK_HOME"] != str(source)
        assert env["GROK_MEMORY"] == "0"
        assert env["GROK_DISABLE_AUTOUPDATER"] == "1"
        assert env["GROK_CLAUDE_SKILLS_ENABLED"] == "false"
        assert env["GROK_CURSOR_SKILLS_ENABLED"] == "false"
        assert "auth.json" in fake_run.cwd_names
        assert "config.toml" in fake_run.cwd_names

    def test_reported_error_becomes_llm_error(self, fake_run: FakeRun) -> None:
        fake_run.stdout = '{"type": "error", "message": "rate limited"}'
        with pytest.raises(LLMError, match="rate limited"):
            grok().generate("s", "u")

    def test_truncated_stream_becomes_llm_error(self, fake_run: FakeRun) -> None:
        fake_run.stdout = '{"type": "text", "data": "trivial"}'
        with pytest.raises(LLMError, match=r"\bend\b"):
            grok().generate("s", "u")

    def test_empty_result_becomes_llm_error(self, fake_run: FakeRun) -> None:
        fake_run.stdout = '{"type": "end", "stopReason": "end_turn", "usage": {}}'
        with pytest.raises(LLMError, match="empty completion"):
            grok().generate("s", "u")

    def test_unauthenticated_exit_is_an_authentication_error(self, fake_run: FakeRun) -> None:
        fake_run.returncode = 1
        fake_run.stdout = json.dumps({"type": "error", "message": "You are not authenticated."})
        with pytest.raises(LLMAuthenticationError, match="not authenticated"):
            grok().generate("s", "u")

    def test_max_turns_exit_is_recovered_when_the_turn_completed(self, fake_run: FakeRun) -> None:
        fake_run.returncode = 1
        fake_run.stderr = "Error: max turns reached"
        fake_run.stdout = GROK_JSONL
        resp = grok().generate("s", "u")
        assert resp.text.endswith("trivial")

    def test_cancelled_stream_is_not_recovered(self, fake_run: FakeRun) -> None:
        fake_run.returncode = 1
        fake_run.stderr = "Error: max turns reached"
        fake_run.stdout = "\n".join(
            [
                '{"type": "text", "data": "I will search the workspace."}',
                '{"type": "end", "stopReason": "cancelled", "usage": {}}',
            ]
        )
        with pytest.raises(LLMError, match="max turns reached"):
            grok().generate("s", "u")


# ---------------------------------------------------------------------------
# Shared CLI behaviour
# ---------------------------------------------------------------------------


class TestCliBackend:
    def test_probe_is_silent_and_reports_missing_binary(
        self,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ) -> None:
        monkeypatch.setattr(sub.shutil, "which", lambda _: None)

        status = claude().probe()

        assert status.ready is False
        assert "claude not found" in status.detail
        assert capsys.readouterr().out == ""

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

    def test_grok_ping_checks_grok_com_login(
        self,
        monkeypatch: pytest.MonkeyPatch,
        fake_run: FakeRun,
    ) -> None:
        monkeypatch.setattr(sub.shutil, "which", lambda _: "/usr/bin/grok")
        fake_run.stdout = "You are logged in with grok.com.\n\nDefault model: grok-4.6\n"
        assert grok().ping() is True
        assert fake_run.argv == ["grok", "models"]

    @pytest.mark.parametrize(
        ("backend", "expected_binary"),
        [("claude_cli", "claude"), ("codex_cli", "codex"), ("grok_cli", "grok")],
    )
    def test_shared_subscription_probe_uses_the_backend_client(
        self,
        backend: str,
        expected_binary: str,
        monkeypatch: pytest.MonkeyPatch,
        fake_run: FakeRun,
    ) -> None:
        monkeypatch.setattr(sub.shutil, "which", lambda _: f"/usr/bin/{expected_binary}")
        fake_run.stdout = {
            "claude_cli": json.dumps(
                {
                    "loggedIn": True,
                    "authMethod": "claude.ai",
                    "subscriptionType": "max",
                }
            ),
            "codex_cli": "Logged in using ChatGPT",
            "grok_cli": "You are logged in with grok.com.",
        }[backend]

        assert sub.probe_subscription_backend(backend).ready is True

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
        fake_run.stdout = "You are not authenticated."
        assert grok().ping() is False

    def test_provider_api_keys_are_removed_from_child_environment(
        self, monkeypatch: pytest.MonkeyPatch, fake_run: FakeRun
    ) -> None:
        monkeypatch.setenv("ANTHROPIC_API_KEY", "secret")
        monkeypatch.setenv("ANTHROPIC_AUTH_TOKEN", "token")
        monkeypatch.setenv("OPENAI_API_KEY", "secret")
        monkeypatch.setenv("XAI_API_KEY", "secret")
        monkeypatch.setenv("GROK_CODE_XAI_API_KEY", "secret")
        fake_run.stdout = CLAUDE_JSON
        claude().generate("s", "u")
        assert "ANTHROPIC_API_KEY" not in fake_run.calls[-1]["env"]
        assert "ANTHROPIC_AUTH_TOKEN" not in fake_run.calls[-1]["env"]
        fake_run.stdout = CODEX_JSONL
        codex().generate("s", "u")
        assert "OPENAI_API_KEY" not in fake_run.calls[-1]["env"]
        fake_run.stdout = GROK_JSONL
        grok().generate("s", "u")
        assert "XAI_API_KEY" not in fake_run.calls[-1]["env"]
        assert "GROK_CODE_XAI_API_KEY" not in fake_run.calls[-1]["env"]

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
        assert grok().capabilities.temperature is False

    def test_capabilities_include_effort_and_exclude_stops(self) -> None:
        assert claude().capabilities.effort is True
        assert codex().capabilities.effort is True
        assert grok().capabilities.effort is True
        assert grok().capabilities.effort_values == {"low", "medium", "high", "xhigh"}
        assert claude().capabilities.stop_sequences is False
        assert codex().capabilities.stop_sequences is False
        assert grok().capabilities.stop_sequences is False
        assert claude().capabilities.output_limit is False
        assert codex().capabilities.output_limit is False
        assert grok().capabilities.output_limit is False


class TestProbeCache:
    def test_probe_runs_one_preflight_per_process(
        self,
        monkeypatch: pytest.MonkeyPatch,
    ) -> None:
        lookups = 0

        def counting_which(_: str) -> None:
            nonlocal lookups
            lookups += 1
            return None

        monkeypatch.setattr(sub.shutil, "which", counting_which)

        first = claude().probe()
        second = claude().probe()

        assert first == second
        assert lookups == 1

    def test_probe_is_cached_per_backend(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setattr(sub.shutil, "which", lambda _: None)

        assert "claude" in claude().probe().detail
        assert "codex" in codex().probe().detail
        assert "grok" in grok().probe().detail
