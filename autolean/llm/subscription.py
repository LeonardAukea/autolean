"""Subscription-backed backends that drive the vendors' own CLIs.

`claude`, `codex`, and `grok` already hold the credentials for a Claude,
ChatGPT, or Grok subscription. AutoLean invokes each CLI in non-interactive
mode from an empty working directory with its customization and action
surfaces disabled.

Authenticate once, outside AutoLean:

    claude    # then /login
    codex login
    grok login
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path

from autolean.llm.base import (
    BaseBackend,
    Capabilities,
    LLMAuthenticationError,
    LLMConfig,
    LLMError,
    LLMRateLimitError,
    LLMResponse,
    token_count,
)
from autolean.llm.capabilities import (
    CLAUDE_CLI_CAPABILITIES,
    CODEX_CLI_CAPABILITIES,
    GROK_CLI_CAPABILITIES,
)
from autolean.process import ProcessOutputError, run_process
from autolean.ui import console

#: A CLI receives this shutdown margin after its request deadline.
KILL_GRACE_SECONDS = 15.0

# These overrides remove Codex's action and customization surfaces. Strict
# config makes a renamed key fail closed during a future CLI upgrade.
_GROK_DISALLOWED_TOOLS = (
    "Agent,read_file,list_dir,grep,search_replace,run_terminal_cmd,"
    "web_search,web_fetch,todo_write,search_tool,write,edit"
)

_GROK_ISOLATION_CONFIG = """\
[compat.claude]
skills = false
rules = false
agents = false
mcps = false
hooks = false
sessions = false

[compat.cursor]
skills = false
rules = false
agents = false
mcps = false
hooks = false
sessions = false
"""

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


@dataclass(frozen=True)
class SubscriptionStatus:
    """Result of one silent local subscription preflight."""

    ready: bool
    detail: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.ready, bool) or not isinstance(self.detail, str):
            raise ValueError("subscription status must contain a boolean and text")


#: One preflight subprocess per (binary, backend) per process. Model
#: selection, construction, and run startup all preflight the same CLI;
#: its credential state does not change within one invocation.
_PROBE_CACHE: dict[tuple[str, str], SubscriptionStatus] = {}


@dataclass
class CliBackend(BaseBackend):
    """Common process handling for a vendor CLI in non-interactive mode."""

    #: Executable name, overridable so a pinned install can be selected.
    binary: str = ""
    #: Non-generating authentication preflight.
    preflight_args: tuple[str, ...] = ("--version",)
    #: Provider API credentials excluded from subscription subprocesses.
    blocked_env: tuple[str, ...] = ()
    capabilities: Capabilities = CLAUDE_CLI_CAPABILITIES

    def __post_init__(self) -> None:
        super().__post_init__()
        if not isinstance(self.binary, str) or not self.binary:
            raise ValueError("subscription backend binary must not be empty")
        if any(
            not isinstance(values, tuple) or any(not isinstance(value, str) or not value for value in values)
            for values in (self.preflight_args, self.blocked_env)
        ):
            raise ValueError("subscription backend arguments must be text tuples")

    def resolved_binary(self) -> str:
        env_override = os.environ.get(f"AUTOLEAN_{self.binary.upper()}_BIN")
        return env_override or self.binary

    def probe(self) -> SubscriptionStatus:
        """Inspect the CLI and its active credential without writing output."""
        key = (self.resolved_binary(), self.config.backend)
        cached = _PROBE_CACHE.get(key)
        if cached is None:
            cached = _PROBE_CACHE[key] = self._probe_uncached()
        return cached

    def _probe_uncached(self) -> SubscriptionStatus:
        binary = self.resolved_binary()
        if shutil.which(binary) is None:
            return SubscriptionStatus(
                ready=False,
                detail=(
                    f"{binary} not found on PATH. Install it and sign in to use "
                    f"the {self.config.backend} backend."
                ),
            )
        try:
            with tempfile.TemporaryDirectory(prefix="autolean-llm-", ignore_cleanup_errors=True) as scratch:
                result = run_process(
                    [binary, *self.preflight_args],
                    timeout=30,
                    cwd=scratch,
                    env=self._scratch_environment(scratch),
                )
        except (OSError, subprocess.SubprocessError) as error:
            return SubscriptionStatus(ready=False, detail=f"{binary} failed to start: {error}")
        if result.returncode != 0:
            detail = (result.stderr or result.stdout or "").strip()[:300]
            return SubscriptionStatus(
                ready=False,
                detail=f"{binary} authentication preflight exited {result.returncode}: {detail}",
            )
        problem = self._preflight_problem(result)
        if problem:
            return SubscriptionStatus(
                ready=False,
                detail=f"{binary} subscription preflight failed: {problem}",
            )
        return SubscriptionStatus(ready=True)

    def ping(self) -> bool:
        """Check the CLI is installed and its active credential resolves."""
        status = self.probe()
        if not status.ready:
            console.print(f"[red]{status.detail}[/]")
        return status.ready

    def _preflight_problem(self, result: subprocess.CompletedProcess[str]) -> str | None:
        """Return a billing/authentication mismatch, if present."""
        return None

    def _environment(self) -> dict[str, str]:
        env = os.environ.copy()
        for name in self.blocked_env:
            env.pop(name, None)
        return env

    def _scratch_environment(self, scratch: str) -> dict[str, str]:
        """Build the child environment for one isolated CLI invocation."""
        del scratch
        return self._environment()

    def _recover_nonzero(self, result: subprocess.CompletedProcess[str]) -> str | None:
        """Return stdout for a non-zero exit that still produced a completion."""
        del result
        return None

    def _run(
        self,
        args: list[str],
        prompt: str,
        *,
        prompt_file: str | None = None,
    ) -> tuple[str, float]:
        """Run the CLI with `prompt` on stdin or in a scratch file."""
        binary = self.resolved_binary()
        t0 = time.monotonic()
        try:
            with tempfile.TemporaryDirectory(prefix="autolean-llm-", ignore_cleanup_errors=True) as scratch:
                argv = [binary, *args]
                stdin = prompt
                if prompt_file is not None:
                    if Path(prompt_file).name != prompt_file:
                        raise LLMError("prompt file must be a basename")
                    path = Path(scratch) / prompt_file
                    path.write_text(prompt, encoding="utf-8")
                    argv.extend(("--prompt-file", str(path)))
                    stdin = ""
                result = run_process(
                    argv,
                    input=stdin,
                    timeout=self.config.timeout + KILL_GRACE_SECONDS,
                    cwd=scratch,
                    env=self._scratch_environment(scratch),
                    # Piped stdin selects each CLI's non-interactive path.
                )
        except subprocess.TimeoutExpired as e:
            raise LLMError(f"{binary} timed out after {self.config.timeout:.0f}s") from e
        except ProcessOutputError as e:
            raise LLMError(f"{binary} output rejected: {e}") from e
        except OSError as e:
            raise LLMError(f"{binary} could not be executed: {e}") from e

        if result.returncode != 0:
            recovered = self._recover_nonzero(result)
            if recovered is not None:
                return recovered, time.monotonic() - t0
            reason = self._reason(result)
            lowered = reason.lower()
            message = f"{binary} exited {result.returncode}: {reason}"
            if "429" in lowered or "rate limit" in lowered or "weekly limit" in lowered:
                raise LLMRateLimitError(message)
            if any(
                word in lowered
                for word in (
                    "401",
                    "403",
                    "login",
                    "logged out",
                    "authentication",
                    "not authenticated",
                    "unauthenticated",
                )
            ):
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
            model=_generating_model(payload, self.config.model),
            input_tokens=token_count(usage.get("input_tokens")),
            output_tokens=token_count(usage.get("output_tokens")),
            duration_seconds=elapsed,
        )


@dataclass
class CodexClient(CliBackend):
    """GPT through the `codex` CLI in `exec` mode."""

    binary: str = "codex"
    preflight_args: tuple[str, ...] = ("login", "status")
    blocked_env: tuple[str, ...] = ("OPENAI_API_KEY",)
    capabilities: Capabilities = CODEX_CLI_CAPABILITIES

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


@dataclass
class GrokClient(CliBackend):
    """Grok through the `grok` CLI in isolated headless mode."""

    binary: str = "grok"
    preflight_args: tuple[str, ...] = ("models",)
    blocked_env: tuple[str, ...] = ("XAI_API_KEY", "GROK_CODE_XAI_API_KEY")
    capabilities: Capabilities = GROK_CLI_CAPABILITIES

    def _scratch_environment(self, scratch: str) -> dict[str, str]:
        env = self._environment()
        env["GROK_HOME"] = scratch
        env["GROK_MEMORY"] = "0"
        env["GROK_DISABLE_AUTOUPDATER"] = "1"
        env["GROK_CLAUDE_SKILLS_ENABLED"] = "false"
        env["GROK_CURSOR_SKILLS_ENABLED"] = "false"
        Path(scratch, "config.toml").write_text(_GROK_ISOLATION_CONFIG, encoding="utf-8")
        source_home = Path(os.environ.get("GROK_HOME") or Path.home() / ".grok")
        auth = source_home / "auth.json"
        if auth.is_file():
            destination = Path(scratch) / "auth.json"
            try:
                shutil.copy2(auth, destination)
                destination.chmod(0o600)
            except OSError:
                destination.unlink(missing_ok=True)
        return env

    def _recover_nonzero(self, result: subprocess.CompletedProcess[str]) -> str | None:
        try:
            text, _usage, _model, stop = _parse_grok_stream(result.stdout)
        except LLMError:
            return None
        if stop != "end_turn" or not text:
            return None
        return result.stdout

    def _preflight_problem(self, result: subprocess.CompletedProcess[str]) -> str | None:
        status = f"{result.stdout}\n{result.stderr}".lower()
        if "logged in with grok.com" not in status:
            return "expected grok.com subscription login"
        return None

    def _reason(self, result: subprocess.CompletedProcess[str]) -> str:
        """Prefer grok's JSON error object over a mixed stdout/stderr dump."""
        try:
            payload = json.loads(result.stdout)
        except (json.JSONDecodeError, TypeError):
            return super()._reason(result)
        if not isinstance(payload, dict):
            return super()._reason(result)
        message = str(payload.get("message") or payload.get("text") or "").strip()
        if message:
            return message
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
        system = (
            f"{system}\n\n"
            "You cannot use tools, files, or skills. Reply with only the "
            "requested Lean tactics or JSON object."
        )
        args = [
            "--output-format",
            "streaming-json",
            "--model",
            self.config.model,
            "--system-prompt-override",
            system,
            "--disallowed-tools",
            _GROK_DISALLOWED_TOOLS,
            "--no-subagents",
            "--no-plan",
            "--disable-web-search",
            "--max-turns",
            "16",
            "--permission-mode",
            "dontAsk",
            "--verbatim",
        ]
        if self.config.effort:
            args.extend(("--effort", self.config.effort))
        stdout, elapsed = self._run(args, user, prompt_file="prompt.txt")

        text, usage, model, _stop = _parse_grok_stream(stdout)
        if not text:
            raise LLMError(f"grok produced an empty completion: {stdout[-300:]}")
        return LLMResponse(
            text=text,
            model=model or self.config.model,
            input_tokens=usage.get("input_tokens", 0),
            output_tokens=usage.get("output_tokens", 0),
            duration_seconds=elapsed,
        )


def probe_subscription_backend(backend: str) -> SubscriptionStatus:
    """Probe one supported subscription transport without generating text."""
    clients: dict[str, type[CliBackend]] = {
        "claude_cli": ClaudeCodeClient,
        "codex_cli": CodexClient,
        "grok_cli": GrokClient,
    }
    client_type = clients.get(backend)
    if client_type is None:
        raise ValueError(f"backend {backend!r} is not a subscription transport")
    return client_type(LLMConfig(model="preflight", backend=backend)).probe()


def _generating_model(payload: dict[str, object], requested_model: str) -> str:
    """Identify the model whose tokens produced the response."""
    model_usage = payload.get("modelUsage")
    if not isinstance(model_usage, dict) or not model_usage:
        return requested_model
    if len(model_usage) == 1:
        return str(next(iter(model_usage)))

    usage = payload.get("usage")
    if isinstance(usage, dict):
        input_tokens = token_count(usage.get("input_tokens"))
        output_tokens = token_count(usage.get("output_tokens"))
        matches = [
            str(model)
            for model, counts in model_usage.items()
            if isinstance(counts, dict)
            and token_count(counts.get("inputTokens")) == input_tokens
            and token_count(counts.get("outputTokens")) == output_tokens
        ]
        if len(matches) == 1:
            return matches[0]

    requested = requested_model.casefold()
    matches = [str(model) for model in model_usage if requested in str(model).casefold()]
    return matches[0] if len(matches) == 1 else requested_model


def _parse_grok_stream(stdout: str) -> tuple[str, dict[str, int], str | None, str | None]:
    """Extract the final answer, usage, and model from grok streaming-json.

    Grok emits token-level `text` events. Tool calls split those events into
    groups; later groups are the completion after the agent stopped searching.
    Groups are joined with newlines so a planning sentence and a tactic body
    stay separable.
    """
    groups: list[str] = []
    current: list[str] = []
    usage: dict[str, int] = {}
    model: str | None = None
    stop: str | None = None
    completed = False

    def flush() -> None:
        if current:
            groups.append("".join(current))
            current.clear()

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
        if kind == "text":
            data = event.get("data")
            if isinstance(data, str):
                current.append(data)
            continue
        flush()
        if kind == "error":
            detail = str(event.get("message") or event.get("data") or event)[:300]
            raise LLMError(f"grok reported an error: {detail}")
        if kind == "end":
            completed = True
            reported_stop = event.get("stopReason")
            stop = reported_stop if isinstance(reported_stop, str) else None
            reported = event.get("usage")
            if isinstance(reported, dict):
                usage = {key: token_count(reported.get(key)) for key in ("input_tokens", "output_tokens")}
            model = _generating_model(event, "")
    flush()
    if not completed:
        raise LLMError("grok stream ended before end")
    text = "\n".join(part.strip() for part in groups if part.strip())
    return text, usage, model or None, stop


def _parse_codex_events(stdout: str) -> tuple[str | None, dict[str, int]]:
    """Extract the final agent message and token usage from codex JSONL."""
    text: str | None = None
    usage: dict[str, int] = {}
    completed = False
    for line in stdout.splitlines():
        event = _decode_codex_event(line)
        if event is None:
            continue
        match event.get("type"):
            case "item.completed":
                text = _codex_message(event) or text
            case "turn.completed":
                completed = True
                usage = _codex_usage(event)
            case "turn.failed":
                raise LLMError(f"codex turn failed: {_codex_failure(event)}")
    if not completed:
        raise LLMError("codex stream ended before turn.completed")
    return text, usage


def _decode_codex_event(line: str) -> dict[str, object] | None:
    """Decode one JSON object from a mixed Codex output stream."""
    line = line.strip()
    if not line.startswith("{"):
        return None
    try:
        event = json.loads(line)
    except json.JSONDecodeError:
        return None
    return event if isinstance(event, dict) else None


def _codex_message(event: dict[str, object]) -> str | None:
    """Return an agent message carried by an item-completed event."""
    item = event.get("item")
    if not isinstance(item, dict) or item.get("type") != "agent_message":
        return None
    text = item.get("text")
    return text if isinstance(text, str) else None


def _codex_usage(event: dict[str, object]) -> dict[str, int]:
    """Return trustworthy non-negative counters from a completed turn."""
    reported = event.get("usage")
    if not isinstance(reported, dict):
        return {}
    return {key: token_count(reported.get(key)) for key in ("input_tokens", "output_tokens")}


def _codex_failure(event: dict[str, object]) -> object:
    """Return the provider's most specific failure detail."""
    error = event.get("error")
    if isinstance(error, dict):
        return error.get("message") or error
    return error or event
