"""Subscription-backed backends that drive the vendors' own CLIs.

`claude` and `codex` already hold the credentials for a Claude or ChatGPT
subscription. AutoLean invokes each CLI in non-interactive mode from an empty
working directory with its customization and action surfaces disabled.

Authenticate once, outside AutoLean:

    claude    # then /login
    codex login
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass

from rich.console import Console

from autolean.llm.base import (
    CLAUDE_EFFORTS,
    OPENAI_EFFORTS,
    BaseBackend,
    Capabilities,
    LLMAuthenticationError,
    LLMError,
    LLMRateLimitError,
    LLMResponse,
)

console = Console()

#: A CLI receives this shutdown margin after its request deadline.
KILL_GRACE_SECONDS = 15.0

# Both CLIs report token usage and use the models' default sampling
# configuration. Their accepted reasoning levels follow their model families.
_CLAUDE_CAPABILITIES = Capabilities(
    temperature=False,
    effort_values=CLAUDE_EFFORTS,
    stop_sequences=False,
    token_counts=True,
    output_limit=False,
)
_CODEX_CAPABILITIES = Capabilities(
    temperature=False,
    effort_values=OPENAI_EFFORTS,
    stop_sequences=False,
    token_counts=True,
    output_limit=False,
)

# These overrides remove Codex's action and customization surfaces. Strict
# config makes a renamed key fail closed during a future CLI upgrade.
_CODEX_CONFIG_OVERRIDES = (
    'approval_policy="never"',
    "agents.enabled=false",
    "apps._default.enabled=false",
    "features.apps=false",
    "features.browser_use=false",
    "features.browser_use_external=false",
    "features.browser_use_full_cdp_access=false",
    "features.code_mode_host=false",
    "features.computer_use=false",
    "features.goals=false",
    "features.hooks=false",
    "features.image_generation=false",
    "features.in_app_browser=false",
    "features.memories=false",
    "features.plugins=false",
    "features.shell_tool=false",
    "features.skill_mcp_dependency_install=false",
    "features.skill_search=false",
    "features.tool_suggest=false",
    "features.unified_exec=false",
    "features.view_image=false",
    "features.workspace_dependencies=false",
    "project_doc_max_bytes=0",
    'web_search="disabled"',
)


@dataclass
class CliBackend(BaseBackend):
    """Common process handling for a vendor CLI in non-interactive mode."""

    #: Executable name, overridable so a pinned install can be selected.
    binary: str = ""
    #: Non-generating authentication preflight.
    preflight_args: tuple[str, ...] = ("--version",)
    #: Provider API credentials excluded from subscription subprocesses.
    blocked_env: tuple[str, ...] = ()
    capabilities: Capabilities = _CLAUDE_CAPABILITIES

    def resolved_binary(self) -> str:
        env_override = os.environ.get(f"AUTOLEAN_{self.binary.upper()}_BIN")
        return env_override or self.binary

    def ping(self) -> bool:
        """Check the CLI is installed and its active credential resolves."""
        binary = self.resolved_binary()
        if shutil.which(binary) is None:
            console.print(
                f"[red]{binary} not found on PATH.[/] "
                f"Install it and sign in to use the {self.config.backend} backend."
            )
            return False
        try:
            with tempfile.TemporaryDirectory(prefix="autolean-llm-", ignore_cleanup_errors=True) as scratch:
                result = subprocess.run(
                    [binary, *self.preflight_args],
                    capture_output=True,
                    text=True,
                    timeout=30,
                    cwd=scratch,
                    env=self._environment(),
                )
        except (OSError, subprocess.SubprocessError) as e:
            console.print(f"[red]{binary} failed to start:[/] {e}")
            return False
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()[:300]
            console.print(f"[red]{binary} authentication preflight exited {result.returncode}:[/] {detail}")
            return False
        problem = self._preflight_problem(result)
        if problem:
            console.print(f"[red]{binary} subscription preflight failed:[/] {problem}")
            return False
        return True

    def _preflight_problem(self, result: subprocess.CompletedProcess[str]) -> str | None:
        """Return a billing/authentication mismatch, if present."""
        return None

    def _environment(self) -> dict[str, str]:
        env = os.environ.copy()
        for name in self.blocked_env:
            env.pop(name, None)
        return env

    def _run(self, args: list[str], prompt: str) -> tuple[str, float]:
        """Run the CLI with `prompt` on stdin; return (stdout, seconds)."""
        binary = self.resolved_binary()
        t0 = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(prefix="autolean-llm-", ignore_cleanup_errors=True) as scratch:
                result = subprocess.run(
                    [binary, *args],
                    input=prompt,
                    capture_output=True,
                    text=True,
                    timeout=self.config.timeout + KILL_GRACE_SECONDS,
                    cwd=scratch,
                    env=self._environment(),
                    # Piped stdin selects each CLI's non-interactive path.
                )
        except subprocess.TimeoutExpired as e:
            raise LLMError(f"{binary} timed out after {self.config.timeout:.0f}s") from e
        except OSError as e:
            raise LLMError(f"{binary} could not be executed: {e}") from e

        if result.returncode != 0:
            reason = self._reason(result)
            lowered = reason.lower()
            message = f"{binary} exited {result.returncode}: {reason}"
            if "429" in lowered or "rate limit" in lowered or "weekly limit" in lowered:
                raise LLMRateLimitError(message)
            if any(word in lowered for word in ("401", "403", "login", "logged out", "authentication")):
                raise LLMAuthenticationError(message)
            raise LLMError(message)
        return result.stdout, time.monotonic() - t0

    def _reason(self, result: subprocess.CompletedProcess[str]) -> str:
        """The most useful line of failure output the CLI produced."""
        return (result.stderr or result.stdout or "").strip()[:500]


@dataclass
class ClaudeCodeClient(CliBackend):
    """Claude through the `claude` CLI in print mode."""

    binary: str = "claude"
    preflight_args: tuple[str, ...] = ("auth", "status", "--json")
    blocked_env: tuple[str, ...] = ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN")

    def _preflight_problem(self, result: subprocess.CompletedProcess[str]) -> str | None:
        try:
            payload = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            return "auth status was not a JSON object"
        if not isinstance(payload, dict):
            return "auth status was not a JSON object"
        if payload.get("loggedIn") is not True:
            return "Claude is logged out"
        if payload.get("authMethod") != "claude.ai":
            return f"expected Claude subscription auth, found {payload.get('authMethod')!r}"
        subscription = payload.get("subscriptionType")
        if not isinstance(subscription, str) or not subscription:
            return "Claude subscription type is unavailable"
        return None

    def _reason(self, result: subprocess.CompletedProcess[str]) -> str:
        """Prefer the CLI's own explanation over its JSON envelope.

        On quota exhaustion and auth failures `claude` exits non-zero but
        still prints a result envelope whose `result` holds the sentence a
        human needs ("You've hit your weekly limit ...").
        """
        try:
            payload = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            return super()._reason(result)
        if not isinstance(payload, dict):
            return super()._reason(result)
        message = str(payload.get("result") or "").strip()
        status = payload.get("api_error_status")
        if message:
            return f"{message} (HTTP {status})" if status else message
        return super()._reason(result)

    def generate(
        self,
        system: str,
        user: str,
        *,
        temperature: float | None = None,
        stop: list[str] | None = None,
    ) -> LLMResponse:
        del temperature, stop  # not exposed by the CLI
        args = [
            "--print",
            "--output-format",
            "json",
            "--model",
            self.config.model,
            "--system-prompt",
            system,
            "--safe-mode",
            "--strict-mcp-config",
            "--disable-slash-commands",
            "--no-session-persistence",
            "--no-chrome",
            "--tools",
            "",
        ]
        if self.config.effort:
            args.extend(("--effort", self.config.effort))
        stdout, elapsed = self._run(args, user)

        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError as e:
            raise LLMError(f"claude returned non-JSON output: {stdout[:300]}") from e
        if not isinstance(payload, dict):
            raise LLMError("claude returned a JSON value instead of a result envelope")

        if payload.get("is_error"):
            detail = str(payload.get("result") or "")[:300]
            raise LLMError(f"claude reported an error: {detail}")

        usage = payload.get("usage") or {}
        if not isinstance(usage, dict):
            usage = {}
        text = str(payload.get("result") or "").strip()
        if not text:
            raise LLMError("claude produced an empty completion")
        return LLMResponse(
            text=text,
            model=_first_model(payload) or self.config.model,
            input_tokens=_nonnegative_int(usage.get("input_tokens")),
            output_tokens=_nonnegative_int(usage.get("output_tokens")),
            duration_seconds=elapsed,
        )


@dataclass
class CodexClient(CliBackend):
    """GPT through the `codex` CLI in `exec` mode."""

    binary: str = "codex"
    preflight_args: tuple[str, ...] = ("login", "status")
    blocked_env: tuple[str, ...] = ("OPENAI_API_KEY",)
    capabilities: Capabilities = _CODEX_CAPABILITIES

    def _preflight_problem(self, result: subprocess.CompletedProcess[str]) -> str | None:
        status = f"{result.stdout}\n{result.stderr}".lower()
        if "logged in using chatgpt" not in status:
            return "expected ChatGPT subscription login"
        return None

    def generate(
        self,
        system: str,
        user: str,
        *,
        temperature: float | None = None,
        stop: list[str] | None = None,
    ) -> LLMResponse:
        del temperature, stop  # not exposed by the CLI
        args = [
            "exec",
            "--json",
            "--model",
            self.config.model,
            "--strict-config",
            "--ignore-user-config",
            "--ignore-rules",
            "--sandbox",
            "read-only",
            "--skip-git-repo-check",
            "--ephemeral",
            "--color",
            "never",
        ]
        for override in _CODEX_CONFIG_OVERRIDES:
            args.extend(("--config", override))
        if self.config.effort:
            effort = json.dumps(self.config.effort)
            args.extend(("--config", f"model_reasoning_effort={effort}"))
        args.append("-")
        stdout, elapsed = self._run(args, f"{system}\n\n---\n\n{user}")

        text, usage = _parse_codex_events(stdout)
        if not text or not text.strip():
            raise LLMError(f"codex produced no message: {stdout[-300:]}")

        return LLMResponse(
            text=text.strip(),
            model=self.config.model,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            duration_seconds=elapsed,
        )


def _first_model(payload: dict[str, object]) -> str | None:
    """Read the model name out of the claude CLI's `modelUsage` map."""
    model_usage = payload.get("modelUsage")
    if isinstance(model_usage, dict) and model_usage:
        return str(next(iter(model_usage)))
    return None


def _nonnegative_int(value: object) -> int:
    """Return a trustworthy token count from an external JSON envelope."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def _parse_codex_events(stdout: str) -> tuple[str | None, dict[str, int]]:
    """Extract the final agent message and token usage from codex JSONL."""
    text: str | None = None
    usage: dict[str, int] = {}
    completed = False
    for line in stdout.splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        kind = event.get("type")
        if kind == "item.completed":
            item = event.get("item") or {}
            if isinstance(item, dict) and item.get("type") == "agent_message":
                reported_text = item.get("text")
                if isinstance(reported_text, str):
                    text = reported_text
        elif kind == "turn.completed":
            completed = True
            reported = event.get("usage") or {}
            if isinstance(reported, dict):
                usage = {}
                for key, value in reported.items():
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                        usage[str(key)] = value
        elif kind == "turn.failed":
            error = event.get("error") or {}
            detail = error.get("message") if isinstance(error, dict) else error
            raise LLMError(f"codex turn failed: {detail or event}")
    if not completed:
        raise LLMError("codex stream ended before turn.completed")
    return text, usage
