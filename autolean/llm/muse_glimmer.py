"""Native Muse Glimmer client for llama.cpp and vLLM endpoints."""

from __future__ import annotations

import os
from dataclasses import dataclass

from autolean.llm.base import MUSE_GLIMMER_EFFORTS, Capabilities, LLMError, LLMResponse
from autolean.llm.openai_compat import OpenAICompatibleClient

DEFAULT_MUSE_GLIMMER_URL = "http://127.0.0.1:8080"
MUSE_GLIMMER_MODEL = "muse-glimmer"
MUSE_GLIMMER_HF_MODEL = "meta-models/Muse-Glimmer-30B"
MUSE_GLIMMER_BF16_REVISION = "f84ecc3a0ea984a4c04542a84269e3d065350a6e"
MUSE_GLIMMER_GGUF_REVISION = "93769bc7ab5ad1e9cd22d857e3138cf5d977ae81"
MUSE_GLIMMER_GGUF_SHA256 = "7e9b74b7c8875e9e265695df9613bf6290f2392e479ce740495a129019c488d8"
_CAPABILITIES = Capabilities(
    temperature=True,
    effort_values=MUSE_GLIMMER_EFFORTS,
    retry_temperature=False,
)


@dataclass
class MuseGlimmerClient(OpenAICompatibleClient):
    """Muse Glimmer text client with its reasoning-template contract."""

    capabilities: Capabilities = _CAPABILITIES
    default_base_url: str = DEFAULT_MUSE_GLIMMER_URL

    def request_headers(self) -> dict[str, str]:
        key = os.environ.get("MUSE_GLIMMER_API_KEY")
        return {"Authorization": f"Bearer {key}"} if key else {}

    def generate(
        self,
        system: str,
        user: str,
        *,
        temperature: float | None = None,
        stop: list[str] | None = None,
    ) -> LLMResponse:
        effort = self.config.effort or "high"
        if effort not in MUSE_GLIMMER_EFFORTS:
            levels = ", ".join(sorted(MUSE_GLIMMER_EFFORTS))
            raise LLMError(f"Muse Glimmer reasoning effort must be one of: {levels}")
        if stop and "<|eom|>" in stop:
            raise LLMError("Muse Glimmer uses <|eom|> inside an active turn; it cannot be a stop token")

        return self._generate_chat(
            system,
            user,
            temperature=temperature,
            stop=stop,
            extra_payload={
                "chat_template_kwargs": {"reasoning_strength": effort},
            },
        )
