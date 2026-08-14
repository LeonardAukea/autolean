"""Backend registry — the one place that maps a backend name to a class.

Constructors are imported lazily, keeping each hosted SDK inside its optional
extra.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from autolean.llm.base import (
    CLAUDE_EFFORTS,
    MUSE_GLIMMER_EFFORTS,
    OPENAI_EFFORTS,
    LLMBackend,
    LLMConfig,
    LLMError,
)

#: A backend constructor: takes the config, returns a ready backend.
BackendFactory = Callable[[LLMConfig], LLMBackend]


@dataclass(frozen=True)
class BackendSpec:
    """What a backend needs and where its defaults come from."""

    loader: Callable[[], BackendFactory]
    summary: str
    #: How the user authenticates. Shown by `autolean models`.
    auth: str
    #: Extra to install, if the backend needs one.
    extra: str | None = None
    #: Exact reasoning-effort values accepted by this backend.
    effort_values: frozenset[str] = frozenset()


def _ollama() -> BackendFactory:
    from autolean.llm.ollama import OllamaClient

    return OllamaClient


def _openai_compat() -> BackendFactory:
    from autolean.llm.openai_compat import OpenAICompatibleClient

    return OpenAICompatibleClient


def _muse_glimmer() -> BackendFactory:
    from autolean.llm.muse_glimmer import MuseGlimmerClient

    return MuseGlimmerClient


def _anthropic() -> BackendFactory:
    from autolean.llm.anthropic_api import AnthropicClient

    return AnthropicClient


def _openai() -> BackendFactory:
    from autolean.llm.openai_api import OpenAIClient

    return OpenAIClient


def _claude_cli() -> BackendFactory:
    from autolean.llm.subscription import ClaudeCodeClient

    return ClaudeCodeClient


def _codex_cli() -> BackendFactory:
    from autolean.llm.subscription import CodexClient

    return CodexClient


BACKENDS: dict[str, BackendSpec] = {
    "claude_cli": BackendSpec(
        loader=_claude_cli,
        summary="Claude via the `claude` CLI",
        auth="Claude subscription (`claude` → /login)",
        effort_values=CLAUDE_EFFORTS,
    ),
    "codex_cli": BackendSpec(
        loader=_codex_cli,
        summary="GPT via the `codex` CLI",
        auth="ChatGPT subscription (`codex login`)",
        effort_values=OPENAI_EFFORTS,
    ),
    "anthropic": BackendSpec(
        loader=_anthropic,
        summary="Claude via the hosted Messages API",
        auth="Anthropic API key, auth token, or `ant auth login` profile",
        extra="anthropic",
        effort_values=CLAUDE_EFFORTS,
    ),
    "openai": BackendSpec(
        loader=_openai,
        summary="GPT via the hosted Responses API",
        auth="OPENAI_API_KEY",
        extra="openai",
        effort_values=OPENAI_EFFORTS,
    ),
    "ollama": BackendSpec(
        loader=_ollama,
        summary="Local inference via Ollama",
        auth="local Ollama service",
    ),
    "openai_compat": BackendSpec(
        loader=_openai_compat,
        summary="Self-hosted vLLM / llama.cpp / LM Studio",
        auth="server-specific",
    ),
    "muse_glimmer": BackendSpec(
        loader=_muse_glimmer,
        summary="Muse Glimmer through local llama.cpp or vLLM",
        auth="local model weights",
        effort_values=MUSE_GLIMMER_EFFORTS,
    ),
}

BACKEND_NAMES = tuple(BACKENDS)


def validate_backend_config(config: LLMConfig) -> None:
    """Validate controls whose accepted values depend on the backend."""
    spec = BACKENDS.get(config.backend)
    if spec is None:
        known = ", ".join(sorted(BACKENDS))
        raise ValueError(f"unknown backend '{config.backend}'; choose one of: {known}")
    if config.effort is None:
        return
    if config.effort not in spec.effort_values:
        if not spec.effort_values:
            raise ValueError(f"backend '{config.backend}' does not accept reasoning effort")
        values = ", ".join(sorted(spec.effort_values))
        raise ValueError(f"backend '{config.backend}' reasoning effort must be one of: {values}")


def create_llm_client(config: LLMConfig) -> LLMBackend:
    """Build the backend named by `config.backend`."""
    spec = BACKENDS.get(config.backend)
    if spec is None:
        known = ", ".join(sorted(BACKENDS))
        raise LLMError(f"Unknown backend '{config.backend}'. Known backends: {known}")
    try:
        validate_backend_config(config)
    except ValueError as error:
        raise LLMError(str(error)) from error
    backend: LLMBackend = spec.loader()(config)
    if backend.capabilities.effort_values != spec.effort_values:
        raise LLMError(f"Backend capability metadata drifted for '{config.backend}'")
    return backend
