"""Ollama backend — local inference over `/api/chat`."""

from __future__ import annotations

import time
from dataclasses import dataclass

import httpx
from rich.console import Console

from autolean.llm._http import (
    CONNECT_TIMEOUT,
    HttpBackend,
    as_int,
    as_list,
    as_object,
    as_text,
)
from autolean.llm.base import Capabilities, LLMError, LLMResponse

console = Console()

DEFAULT_OLLAMA_URL = "http://localhost:11434"

_CAPABILITIES = Capabilities(temperature=True)


@dataclass
class OllamaClient(HttpBackend):
    """Synchronous Ollama client."""

    capabilities: Capabilities = _CAPABILITIES
    default_base_url: str = DEFAULT_OLLAMA_URL

    def ping(self) -> bool:
        """Check that Ollama is up and the configured model is pulled."""
        try:
            resp = self.client().get("/api/tags")
            resp.raise_for_status()
            body = as_object(resp.json(), "Ollama model-list response")
            models = as_list(body.get("models"), "Ollama models")
            installed = [
                as_text(as_object(model, "Ollama model").get("name"), "Ollama model name") for model in models
            ]
        except (httpx.HTTPError, ValueError, LLMError) as e:
            console.print(f"[red]Ollama unreachable:[/] {e}")
            return False

        target = self.config.model
        if any(m == target or m.startswith(f"{target}:") or target.startswith(m) for m in installed):
            return True
        console.print(
            f"[yellow]Warning:[/] model '{target}' not pulled. Available: {', '.join(installed) or '(none)'}"
        )
        return False

    def generate(
        self,
        system: str,
        user: str,
        *,
        temperature: float | None = None,
        stop: list[str] | None = None,
    ) -> LLMResponse:
        options: dict[str, object] = {"num_predict": self.config.max_output_tokens}
        temp = self.config.resolved_temperature(temperature)
        if temp is not None:
            options["temperature"] = temp
        if stop:
            options["stop"] = stop

        payload: dict[str, object] = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": options,
        }

        t0 = time.monotonic()
        data = self.post_json("/api/chat", payload)
        elapsed = time.monotonic() - t0

        message = as_object(data.get("message"), "Ollama message")
        text = as_text(message.get("content"), "Ollama message content")
        model = data.get("model", self.config.model)
        if not isinstance(model, str):
            raise LLMError("Ollama model name must be text")
        return LLMResponse(
            text=text,
            model=model,
            input_tokens=as_int(data.get("prompt_eval_count")),
            output_tokens=as_int(data.get("eval_count")),
            duration_seconds=elapsed,
        )


def probe_installed_models(base_url: str = DEFAULT_OLLAMA_URL) -> set[str]:
    """Names of models pulled locally, or an empty set if Ollama is down."""
    try:
        resp = httpx.get(f"{base_url}/api/tags", timeout=CONNECT_TIMEOUT / 2)
        resp.raise_for_status()
        body = as_object(resp.json(), "Ollama model-list response")
        models = as_list(body.get("models"), "Ollama models")
        return {
            as_text(as_object(model, "Ollama model").get("name"), "Ollama model name") for model in models
        }
    except (httpx.HTTPError, ValueError, LLMError):
        return set()
