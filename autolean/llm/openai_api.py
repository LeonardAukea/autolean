"""OpenAI hosted API backend: GPT via the official SDK's Responses API.

Requires `OPENAI_API_KEY` and uses API credit. The `codex_cli` backend uses
ChatGPT subscription access.
"""

from __future__ import annotations

import base64
import importlib
import time
from dataclasses import dataclass, field
from typing import Any

from rich.console import Console

from autolean.llm.base import (
    OPENAI_EFFORTS,
    BaseBackend,
    Capabilities,
    DocumentInput,
    LLMAuthenticationError,
    LLMError,
    LLMRateLimitError,
    LLMRefusalError,
    LLMResponse,
    LLMTransientError,
)

console = Console()

# GPT-5-class reasoning models take depth from `reasoning.effort` and accept
# their default sampling configuration. Responses has no stop-sequence field.
_CAPABILITIES = Capabilities(
    temperature=False,
    effort_values=OPENAI_EFFORTS,
    stop_sequences=False,
    document_inputs=True,
)


def _require_sdk() -> Any:
    try:
        return importlib.import_module("openai")
    except ImportError as e:  # pragma: no cover - exercised via the error path
        raise LLMError("The openai SDK is not installed. Install it with: uv sync --extra openai") from e


def _responses_input(user: str, documents: tuple[DocumentInput, ...]) -> Any:
    if not documents:
        return user
    content = [
        {
            "type": "input_file",
            "filename": document.filename,
            "file_data": (
                f"data:{document.media_type};base64,{base64.b64encode(document.data).decode('ascii')}"
            ),
        }
        for document in documents
    ]
    content.append({"type": "input_text", "text": user})
    return [{"role": "user", "content": content}]


def _response_text(response: Any, max_output_tokens: int) -> str:
    status = response.status
    if status != "completed":
        reason = getattr(getattr(response, "incomplete_details", None), "reason", None)
        if reason == "content_filter":
            raise LLMRefusalError("OpenAI declined the request (content filter)")
        if reason == "max_output_tokens":
            raise LLMError(f"OpenAI exhausted the {max_output_tokens}-token output limit")
        error = getattr(response, "error", None)
        detail = getattr(error, "message", None) or reason or "no detail"
        raise LLMError(f"OpenAI response {status or 'has no status'}: {detail}")
    try:
        text = (response.output_text or "").strip()
    except (AttributeError, TypeError) as error:
        raise LLMError(f"OpenAI returned malformed text output: {error}") from error
    if not text:
        raise LLMError("OpenAI produced an empty completion")
    return text


@dataclass
class OpenAIClient(BaseBackend):
    """GPT over the Responses API."""

    capabilities: Capabilities = _CAPABILITIES
    _sdk_client: Any = field(default=None, repr=False)

    def _client(self) -> Any:
        if self._sdk_client is None:
            openai = _require_sdk()
            kwargs: dict[str, Any] = {"timeout": self.config.timeout}
            if self.config.base_url:
                kwargs["base_url"] = self.config.base_url
            try:
                self._sdk_client = openai.OpenAI(**kwargs)
            except openai.OpenAIError as e:
                raise LLMError(f"OpenAI client configuration failed: {e}") from e
        return self._sdk_client

    def ping(self) -> bool:
        """Confirm the key resolves and the model is available to it."""
        openai = _require_sdk()
        try:
            self._client().models.retrieve(self.config.model)
            return True
        except openai.APIStatusError as e:
            console.print(f"[red]OpenAI rejected the request:[/] {e.status_code} {e.message}")
            return False
        except openai.APIConnectionError as e:
            console.print(f"[red]OpenAI API unreachable:[/] {e}")
            return False

    def generate(
        self,
        system: str,
        user: str,
        *,
        temperature: float | None = None,
        stop: list[str] | None = None,
    ) -> LLMResponse:
        return self._generate(system, user, (), temperature=temperature, stop=stop)

    def generate_with_documents(
        self,
        system: str,
        user: str,
        documents: tuple[DocumentInput, ...],
        *,
        temperature: float | None = None,
        stop: list[str] | None = None,
    ) -> LLMResponse:
        """Generate with native Responses API PDF inputs."""
        if not documents:
            raise ValueError("documents must not be empty")
        return self._generate(system, user, documents, temperature=temperature, stop=stop)

    def _generate(
        self,
        system: str,
        user: str,
        documents: tuple[DocumentInput, ...],
        *,
        temperature: float | None,
        stop: list[str] | None,
    ) -> LLMResponse:
        del temperature, stop
        openai = _require_sdk()
        kwargs: dict[str, Any] = {
            "model": self.config.model,
            "instructions": system,
            "input": _responses_input(user, documents),
            "max_output_tokens": self.config.max_output_tokens,
            # AutoLean requests are independent, so server-side conversation
            # storage has no continuation value.
            "store": False,
        }
        if self.config.effort:
            kwargs["reasoning"] = {"effort": self.config.effort}

        t0 = time.monotonic()
        try:
            response = self._client().responses.create(**kwargs)
        except openai.APIStatusError as e:
            raise _status_error(e.status_code, f"OpenAI API error: {e.message}") from e
        except openai.APIConnectionError as e:
            raise LLMTransientError(f"OpenAI API unreachable: {e}") from e
        elapsed = time.monotonic() - t0
        text = _response_text(response, self.config.max_output_tokens)
        usage = response.usage
        return LLMResponse(
            text=text,
            model=response.model,
            input_tokens=getattr(usage, "input_tokens", 0) or 0,
            output_tokens=getattr(usage, "output_tokens", 0) or 0,
            duration_seconds=elapsed,
        )

    def close(self) -> None:
        if self._sdk_client is not None:
            self._sdk_client.close()
            self._sdk_client = None


def _status_error(status: int, message: str) -> LLMError:
    if status in (401, 403):
        return LLMAuthenticationError(message)
    if status == 429:
        return LLMRateLimitError(message)
    if status >= 500:
        return LLMTransientError(message)
    return LLMError(message)
