"""LLM client abstraction — Ollama, OpenAI-compatible, or any backend.

Provides a Protocol-based interface so the agent loop doesn't care
which LLM server is running behind the scenes.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from typing import Protocol, runtime_checkable

import httpx
from rich.console import Console

console = Console()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_OPENAI_URL = "http://localhost:8080"  # vLLM / llama.cpp default
DEFAULT_MODEL = "gemma4:26b"
DEFAULT_TEMPERATURE = 0.4
DEFAULT_NUM_PREDICT = 4096  # must be large enough for thinking models (Gemma 4 uses extended thinking)


@dataclass
class LLMConfig:
    """Configuration for any LLM backend."""

    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_OLLAMA_URL
    temperature: float = DEFAULT_TEMPERATURE
    num_predict: int = DEFAULT_NUM_PREDICT
    timeout: float = 300.0  # 5 min max per request
    backend: str = "ollama"  # "ollama" | "openai_compat"


# ---------------------------------------------------------------------------
# Response (shared across all backends)
# ---------------------------------------------------------------------------


@dataclass
class LLMResponse:
    """Structured response from any LLM backend."""

    text: str
    model: str
    eval_count: int = 0
    eval_duration_ns: int = 0
    prompt_eval_count: int = 0
    total_duration_ns: int = 0

    @property
    def tokens_per_second(self) -> float:
        if self.eval_duration_ns > 0:
            return self.eval_count / (self.eval_duration_ns / 1e9)
        return 0.0

    @property
    def total_duration_seconds(self) -> float:
        return self.total_duration_ns / 1e9


# ---------------------------------------------------------------------------
# LLM Backend Protocol (P3.4)
# ---------------------------------------------------------------------------


@runtime_checkable
class LLMBackend(Protocol):
    """Protocol for LLM backends — any object with these methods works.

    Supports Ollama, vLLM, llama.cpp server, LM Studio, or any
    OpenAI-compatible API.
    """

    config: LLMConfig

    def ping(self) -> bool:
        """Check that the server is reachable and the model is available."""
        ...

    def generate(
        self,
        system: str,
        user: str,
        *,
        temperature: float | None = None,
        stop: list[str] | None = None,
    ) -> LLMResponse:
        """Generate a chat completion."""
        ...

    def close(self) -> None:
        """Release resources."""
        ...


# ---------------------------------------------------------------------------
# Ollama Client
# ---------------------------------------------------------------------------


@dataclass
class OllamaClient:
    """Synchronous Ollama client using httpx (/api/chat endpoint)."""

    config: LLMConfig = field(default_factory=LLMConfig)
    _client: httpx.Client | None = field(default=None, repr=False)

    def _ensure_client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                base_url=self.config.base_url,
                timeout=httpx.Timeout(self.config.timeout, connect=10.0),
            )
        return self._client

    def ping(self) -> bool:
        """Check that Ollama is reachable and the model is available."""
        try:
            client = self._ensure_client()
            resp = client.get("/api/tags")
            resp.raise_for_status()
            models = [m["name"] for m in resp.json().get("models", [])]
            target = self.config.model
            found = any(
                m == target or m.startswith(target + ":") or target.startswith(m)
                for m in models
            )
            if not found:
                console.print(
                    f"[yellow]Warning:[/] Model '{target}' not found. "
                    f"Available: {', '.join(models)}"
                )
            return found
        except httpx.HTTPError as e:
            console.print(f"[red]Ollama unreachable:[/] {e}")
            return False

    def generate(
        self,
        system: str,
        user: str,
        *,
        temperature: float | None = None,
        stop: list[str] | None = None,
    ) -> LLMResponse:
        """Generate a completion using Ollama /api/chat."""
        client = self._ensure_client()

        payload: dict = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "stream": False,
            "options": {
                "temperature": temperature or self.config.temperature,
                "num_predict": self.config.num_predict,
            },
        }
        if stop:
            payload["options"]["stop"] = stop

        t0 = time.monotonic()
        resp = client.post("/api/chat", json=payload)
        resp.raise_for_status()
        data = resp.json()
        wall_time = time.monotonic() - t0

        message = data.get("message", {})
        text = message.get("content", "").strip()

        # Gemma 4 and other thinking models put reasoning in "thinking" field
        # and the answer in "content". If content is empty, extract from thinking.
        if not text:
            thinking = message.get("thinking", "")
            if thinking:
                text = thinking.strip()

        return LLMResponse(
            text=text,
            model=data.get("model", self.config.model),
            eval_count=data.get("eval_count", 0),
            eval_duration_ns=data.get("eval_duration", 0),
            prompt_eval_count=data.get("prompt_eval_count", 0),
            total_duration_ns=int(wall_time * 1e9),
        )

    def close(self) -> None:
        if self._client and not self._client.is_closed:
            self._client.close()

    def __del__(self) -> None:
        self.close()


# ---------------------------------------------------------------------------
# OpenAI-Compatible Client (vLLM, llama.cpp, LM Studio, etc.)
# ---------------------------------------------------------------------------


@dataclass
class OpenAICompatibleClient:
    """Client for any server exposing /v1/chat/completions.

    Works with: vLLM, llama.cpp server, LM Studio, text-generation-inference,
    Ollama (also exposes OpenAI-compat endpoint), and any OpenAI-compatible API.
    """

    config: LLMConfig = field(default_factory=lambda: LLMConfig(
        base_url=DEFAULT_OPENAI_URL,
        backend="openai_compat",
    ))
    _client: httpx.Client | None = field(default=None, repr=False)

    def _ensure_client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                base_url=self.config.base_url,
                timeout=httpx.Timeout(self.config.timeout, connect=10.0),
            )
        return self._client

    def ping(self) -> bool:
        """Check server is reachable via /v1/models."""
        try:
            client = self._ensure_client()
            resp = client.get("/v1/models")
            resp.raise_for_status()
            data = resp.json()
            models = [m.get("id", "") for m in data.get("data", [])]
            target = self.config.model
            found = any(target in m for m in models) if models else True
            if not found:
                console.print(
                    f"[yellow]Warning:[/] Model '{target}' not found. "
                    f"Available: {', '.join(models)}"
                )
            return True  # Server is reachable even if model list is empty
        except httpx.HTTPError as e:
            console.print(f"[red]OpenAI-compatible server unreachable:[/] {e}")
            return False

    def generate(
        self,
        system: str,
        user: str,
        *,
        temperature: float | None = None,
        stop: list[str] | None = None,
    ) -> LLMResponse:
        """Generate via /v1/chat/completions (OpenAI format)."""
        client = self._ensure_client()

        payload: dict = {
            "model": self.config.model,
            "messages": [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            "temperature": temperature or self.config.temperature,
            "max_tokens": self.config.num_predict,
            "stream": False,
        }
        if stop:
            payload["stop"] = stop

        t0 = time.monotonic()
        resp = client.post("/v1/chat/completions", json=payload)
        resp.raise_for_status()
        data = resp.json()
        wall_time = time.monotonic() - t0

        choices = data.get("choices", [{}])
        text = choices[0].get("message", {}).get("content", "").strip() if choices else ""
        usage = data.get("usage", {})

        return LLMResponse(
            text=text,
            model=data.get("model", self.config.model),
            eval_count=usage.get("completion_tokens", 0),
            eval_duration_ns=int(wall_time * 1e9),  # approximate
            prompt_eval_count=usage.get("prompt_tokens", 0),
            total_duration_ns=int(wall_time * 1e9),
        )

    def close(self) -> None:
        if self._client and not self._client.is_closed:
            self._client.close()

    def __del__(self) -> None:
        self.close()


# ---------------------------------------------------------------------------
# Factory
# ---------------------------------------------------------------------------


def create_llm_client(config: LLMConfig) -> LLMBackend:
    """Create the appropriate LLM client based on config.backend.

    Args:
        config: LLM configuration with backend field.

    Returns:
        An LLMBackend instance (OllamaClient or OpenAICompatibleClient).
    """
    if config.backend == "openai_compat":
        return OpenAICompatibleClient(config=config)
    return OllamaClient(config=config)
