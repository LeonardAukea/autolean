"""Anthropic hosted API backend: Claude via the official SDK.

Requires an API key (`ANTHROPIC_API_KEY`, `ANTHROPIC_AUTH_TOKEN`, or an
`ant auth login` profile). Hosted calls use API credit. The `claude_cli`
backend uses Claude subscription access.
"""

from __future__ import annotations

import importlib
import time
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console

from autolean.llm.base import (
    CLAUDE_EFFORTS,
    BaseBackend,
    Capabilities,
    LLMAuthenticationError,
    LLMError,
    LLMRateLimitError,
    LLMRefusalError,
    LLMResponse,
    LLMTransientError,
)

console = Console()

#: Server-side refusal fallback: on a policy decline the API re-runs the
#: request on Anthropic's recommended substitute inside the same call.
FALLBACK_BETA = "server-side-fallback-2026-07-01"

# Current Claude models take reasoning depth from `effort` and accept their
# default sampling configuration.
_CAPABILITIES = Capabilities(temperature=False, effort_values=CLAUDE_EFFORTS)


def _require_sdk() -> Any:
    try:
        return importlib.import_module("anthropic")
    except ImportError as e:  # pragma: no cover - exercised via the error path
        raise LLMError(
            "The anthropic SDK is not installed. Install it with: uv sync --extra anthropic"
        ) from e


@dataclass
class AnthropicClient(BaseBackend):
    """Claude over the Messages API.

    Streams every request: `max_output_tokens` is large enough that a
    non-streaming call would risk an HTTP timeout, and the SDK refuses
    non-streaming requests it estimates will run past ten minutes.
    """

    capabilities: Capabilities = _CAPABILITIES
    _sdk_client: Any = field(default=None, repr=False)
    _fallbacks_enabled: bool = field(default=True, repr=False)

    def __post_init__(self) -> None:
        self._fallbacks_enabled = self.config.fallbacks

    def _client(self) -> Any:
        if self._sdk_client is None:
            anthropic = _require_sdk()
            kwargs: dict[str, Any] = {"timeout": self.config.timeout}
            if self.config.base_url:
                kwargs["base_url"] = self.config.base_url
            try:
                self._sdk_client = anthropic.Anthropic(**kwargs)
            except (anthropic.AnthropicError, TypeError) as e:
                raise LLMError(f"Anthropic client configuration failed: {e}") from e
        return self._sdk_client

    def ping(self) -> bool:
        """Confirm credentials resolve and the model is available to them."""
        anthropic = _require_sdk()
        try:
            self._client().models.retrieve(self.config.model)
            return True
        except anthropic.APIStatusError as e:
            console.print(f"[red]Anthropic rejected the request:[/] {e.status_code} {e.message}")
            return False
        except anthropic.APIConnectionError as e:
            console.print(f"[red]Anthropic API unreachable:[/] {e}")
            return False

    def _request_kwargs(self, system: str, user: str, stop: list[str] | None) -> dict[str, Any]:
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "max_tokens": self.config.max_output_tokens,
            "system": system,
            "messages": [{"role": "user", "content": user}],
            "thinking": {"type": "adaptive"},
        }
        if self.config.effort:
            kwargs["output_config"] = {"effort": self.config.effort}
        if stop:
            kwargs["stop_sequences"] = stop
        if self._fallbacks_enabled:
            kwargs["betas"] = [FALLBACK_BETA]
            kwargs["fallbacks"] = "default"
        return kwargs

    def generate(
        self,
        system: str,
        user: str,
        *,
        temperature: float | None = None,
        stop: list[str] | None = None,
    ) -> LLMResponse:
        del temperature  # rejected by current Claude models; see `capabilities`
        anthropic = _require_sdk()
        t0 = time.monotonic()
        client = self._client()
        while True:
            try:
                message = self._stream(client, self._request_kwargs(system, user, stop))
                break
            except anthropic.BadRequestError as e:
                # A workspace can lack access to the fallback beta. One retry
                # establishes the supported request shape for this client.
                if self._fallbacks_enabled and _mentions_fallback(str(e)):
                    self._fallbacks_enabled = False
                    continue
                raise LLMError(f"Anthropic rejected the request: {e}") from e
            except anthropic.APIStatusError as e:
                raise _status_error(e.status_code, f"Anthropic API error: {e.message}") from e
            except anthropic.APIConnectionError as e:
                raise LLMTransientError(f"Anthropic API unreachable: {e}") from e
        elapsed = time.monotonic() - t0

        if message.stop_reason == "refusal":
            category = getattr(getattr(message, "stop_details", None), "category", None)
            raise LLMRefusalError(f"Claude declined the request (category: {category})")
        if message.stop_reason == "max_tokens":
            raise LLMError(f"Claude exhausted the {self.config.max_output_tokens}-token output limit")
        if message.stop_reason not in ("end_turn", "stop_sequence"):
            raise LLMError(f"Claude stopped with reason: {message.stop_reason}")

        text = "".join(b.text for b in message.content if b.type == "text").strip()
        if not text:
            raise LLMError("Claude produced an empty completion")
        usage = message.usage
        return LLMResponse(
            text=text,
            model=message.model,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            duration_seconds=elapsed,
        )

    @staticmethod
    def _stream(client: Any, kwargs: dict[str, Any]) -> Any:
        endpoint = client.beta.messages if "betas" in kwargs else client.messages
        with endpoint.stream(**kwargs) as stream:
            return stream.get_final_message()

    def close(self) -> None:
        if self._sdk_client is not None:
            self._sdk_client.close()
            self._sdk_client = None


def _mentions_fallback(message: str) -> bool:
    lowered = message.lower()
    return "fallback" in lowered or FALLBACK_BETA in lowered


def _status_error(status: int, message: str) -> LLMError:
    if status in (401, 403):
        return LLMAuthenticationError(message)
    if status == 429:
        return LLMRateLimitError(message)
    if status >= 500:
        return LLMTransientError(message)
    return LLMError(message)
