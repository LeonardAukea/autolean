"""OpenAI-compatible backend — vLLM, llama.cpp, LM Studio, TGI, and friends.

Speaks `/v1/chat/completions` against a server you run yourself. For the
hosted OpenAI API, use the `openai` backend instead: it targets the Responses
API and understands reasoning effort.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
from rich.console import Console

from autolean.llm._http import HttpBackend, as_int, as_list, as_object, as_text
from autolean.llm.base import Capabilities, LLMError, LLMResponse

console = Console()

DEFAULT_OPENAI_COMPAT_URL = "http://localhost:8000"

_CAPABILITIES = Capabilities(temperature=True)


@dataclass
class OpenAICompatibleClient(HttpBackend):
    """Client for any server exposing the OpenAI chat-completions shape."""

    capabilities: Capabilities = _CAPABILITIES
    default_base_url: str = DEFAULT_OPENAI_COMPAT_URL

    def ping(self) -> bool:
        """Check the server answers `/v1/models`.

        A reachable server counts as usable even when it lists no models —
        several implementations serve a single unnamed model.
        """
        try:
            resp = self.client().get("/v1/models")
            resp.raise_for_status()
            body = as_object(resp.json(), "OpenAI-compatible model-list response")
            models = as_list(body.get("data"), "OpenAI-compatible models")
            served = [
                as_text(
                    as_object(model, "OpenAI-compatible model").get("id"),
                    "OpenAI-compatible model id",
                )
                for model in models
            ]
        except (httpx.HTTPError, ValueError, LLMError) as e:
            console.print(f"[red]OpenAI-compatible server unreachable:[/] {e}")
            return False

        if served and not any(self.config.model in m for m in served):
            console.print(
                f"[yellow]Warning:[/] model '{self.config.model}' not served. Available: {', '.join(served)}"
            )
            return False
        return True

    def generate(
        self,
        system: str,
        user: str,
        *,
        temperature: float | None = None,
        stop: list[str] | None = None,
    ) -> LLMResponse:
        return self._generate_chat(system, user, temperature=temperature, stop=stop)

    def _generate_chat(
        self,
        system: str,
        user: str,
        *,
        temperature: float | None = None,
        stop: list[str] | None = None,
        extra_payload: dict[str, object] | None = None,
    ) -> LLMResponse:
        """Execute one chat-completions request with validated extensions."""
        payload: dict[str, object] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "max_tokens": self.config.max_output_tokens,
            "stream": False,
        }
        temp = self.config.resolved_temperature(temperature)
        if temp is not None:
            payload["temperature"] = temp
        if stop:
            payload["stop"] = stop
        if self.config.seed is not None:
            payload["seed"] = self.config.seed
        if extra_payload:
            collisions = payload.keys() & extra_payload.keys()
            if collisions:
                fields = ", ".join(sorted(collisions))
                raise LLMError(f"OpenAI-compatible request extension replaces core fields: {fields}")
            payload.update(extra_payload)

        t0 = time.monotonic()
        data = self.post_json("/v1/chat/completions", payload)
        elapsed = time.monotonic() - t0

        choices = as_list(data.get("choices"), "OpenAI-compatible choices")
        if not choices:
            raise LLMError("OpenAI-compatible response contains no choices")
        choice = as_object(choices[0], "OpenAI-compatible choice")
        message = as_object(choice.get("message"), "OpenAI-compatible message")
        text = as_text(message.get("content"), "OpenAI-compatible message content")
        usage_value = data.get("usage")
        usage = {} if usage_value is None else as_object(usage_value, "OpenAI-compatible usage")
        model = data.get("model", self.config.model)
        if not isinstance(model, str):
            raise LLMError("OpenAI-compatible model name must be text")

        return LLMResponse(
            text=text,
            model=model,
            input_tokens=as_int(usage.get("prompt_tokens")),
            output_tokens=as_int(usage.get("completion_tokens")),
            duration_seconds=elapsed,
        )
