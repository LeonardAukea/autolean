"""Ollama client for local LLM inference (Gemma 4, etc.)."""

from __future__ import annotations

import time
from dataclasses import dataclass, field

import httpx
from rich.console import Console

console = Console()

# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------

DEFAULT_OLLAMA_URL = "http://localhost:11434"
DEFAULT_MODEL = "gemma4:26b"
DEFAULT_TEMPERATURE = 0.4
DEFAULT_NUM_PREDICT = 2048


@dataclass
class LLMConfig:
    """Configuration for the Ollama LLM client."""

    model: str = DEFAULT_MODEL
    base_url: str = DEFAULT_OLLAMA_URL
    temperature: float = DEFAULT_TEMPERATURE
    num_predict: int = DEFAULT_NUM_PREDICT
    timeout: float = 300.0  # 5 min max per request


# ---------------------------------------------------------------------------
# Response
# ---------------------------------------------------------------------------


@dataclass
class LLMResponse:
    """Structured response from the LLM."""

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
# Client
# ---------------------------------------------------------------------------


@dataclass
class OllamaClient:
    """Synchronous Ollama client using httpx."""

    config: LLMConfig = field(default_factory=LLMConfig)
    _client: httpx.Client | None = field(default=None, repr=False)

    def _ensure_client(self) -> httpx.Client:
        if self._client is None or self._client.is_closed:
            self._client = httpx.Client(
                base_url=self.config.base_url,
                timeout=httpx.Timeout(self.config.timeout, connect=10.0),
            )
        return self._client

    # -- health check -------------------------------------------------------

    def ping(self) -> bool:
        """Check that Ollama is reachable and the model is available."""
        try:
            client = self._ensure_client()
            resp = client.get("/api/tags")
            resp.raise_for_status()
            models = [m["name"] for m in resp.json().get("models", [])]
            # Match with or without :latest tag
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

    # -- generation ---------------------------------------------------------

    def generate(
        self,
        system: str,
        user: str,
        *,
        temperature: float | None = None,
        stop: list[str] | None = None,
    ) -> LLMResponse:
        """Generate a completion using /api/chat (chat mode)."""
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

        return LLMResponse(
            text=text,
            model=data.get("model", self.config.model),
            eval_count=data.get("eval_count", 0),
            eval_duration_ns=data.get("eval_duration", 0),
            prompt_eval_count=data.get("prompt_eval_count", 0),
            total_duration_ns=int(wall_time * 1e9),
        )

    # -- cleanup ------------------------------------------------------------

    def close(self) -> None:
        if self._client and not self._client.is_closed:
            self._client.close()

    def __del__(self) -> None:
        self.close()
