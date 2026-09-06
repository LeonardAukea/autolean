"""Backend registry — the one place that maps a backend name to a class.

Constructors are imported lazily, keeping each hosted SDK inside its optional
extra.
"""

from __future__ import annotations

import ipaddress
import re
from collections.abc import Callable
from dataclasses import dataclass
from urllib.parse import urlsplit

from autolean.llm.base import (
    Capabilities,
    InferenceLocation,
    LLMBackend,
    LLMConfig,
    LLMError,
    validate_endpoint,
)
from autolean.llm.capabilities import (
    ANTHROPIC_CAPABILITIES,
    CLAUDE_CLI_CAPABILITIES,
    CODEX_CLI_CAPABILITIES,
    GROK_CLI_CAPABILITIES,
    MUSE_GLIMMER_CAPABILITIES,
    OLLAMA_CAPABILITIES,
    OPENAI_CAPABILITIES,
    OPENAI_COMPAT_CAPABILITIES,
)

#: A backend constructor: takes the config, returns a ready backend.
BackendFactory = Callable[[LLMConfig], LLMBackend]


@dataclass(frozen=True)
class BackendSpec:
    """What a backend needs and where its defaults come from."""

    loader: Callable[[], BackendFactory]
    #: Short user-facing name accepted by `--provider`.
    provider: str
    summary: str
    #: How the user authenticates. Shown by `autolean models`.
    auth: str
    #: Placement used when the configuration has no explicit endpoint.
    location: InferenceLocation
    #: Request controls and inputs implemented by this provider adapter.
    capabilities: Capabilities
    #: Whether `base_url` changes the destination used by the adapter.
    custom_endpoint: bool = True
    #: Additional user-facing names accepted by `--provider`.
    provider_aliases: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if not isinstance(self.provider_aliases, tuple):
            raise ValueError("provider aliases must be a tuple")
        names = (self.provider, *self.provider_aliases)
        if not callable(self.loader):
            raise ValueError("backend loader must be callable")
        if any(
            not isinstance(name, str) or re.fullmatch(r"[a-z0-9][a-z0-9-]*", name) is None for name in names
        ) or len(set(names)) != len(names):
            raise ValueError("provider names must be unique slugs")
        if any(not isinstance(value, str) or not value.strip() for value in (self.summary, self.auth)):
            raise ValueError("backend summary and authentication help are required")
        if not isinstance(self.location, InferenceLocation):
            raise ValueError("backend location must be local or remote")
        if not isinstance(self.capabilities, Capabilities):
            raise ValueError("backend capabilities must use the shared vocabulary")
        if not isinstance(self.custom_endpoint, bool):
            raise ValueError("custom endpoint support must be a boolean")


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


def _grok_cli() -> BackendFactory:
    from autolean.llm.subscription import GrokClient

    return GrokClient


BACKENDS: dict[str, BackendSpec] = {
    "claude_cli": BackendSpec(
        loader=_claude_cli,
        provider="claude",
        summary="Claude via the `claude` CLI",
        auth="Claude subscription (`claude` → /login)",
        location=InferenceLocation.REMOTE,
        capabilities=CLAUDE_CLI_CAPABILITIES,
        custom_endpoint=False,
    ),
    "codex_cli": BackendSpec(
        loader=_codex_cli,
        provider="codex",
        summary="GPT via the `codex` CLI",
        auth="ChatGPT subscription (`codex login`)",
        location=InferenceLocation.REMOTE,
        capabilities=CODEX_CLI_CAPABILITIES,
        custom_endpoint=False,
    ),
    "grok_cli": BackendSpec(
        loader=_grok_cli,
        provider="grok",
        summary="Grok via the `grok` CLI",
        auth="Grok subscription (`grok login`)",
        location=InferenceLocation.REMOTE,
        capabilities=GROK_CLI_CAPABILITIES,
        custom_endpoint=False,
    ),
    "anthropic": BackendSpec(
        loader=_anthropic,
        provider="anthropic",
        summary="Claude via the hosted Messages API",
        auth="Anthropic API key, auth token, or `ant auth login` profile",
        location=InferenceLocation.REMOTE,
        capabilities=ANTHROPIC_CAPABILITIES,
    ),
    "openai": BackendSpec(
        loader=_openai,
        provider="openai",
        summary="GPT via the hosted Responses API",
        auth="OPENAI_API_KEY",
        location=InferenceLocation.REMOTE,
        capabilities=OPENAI_CAPABILITIES,
    ),
    "ollama": BackendSpec(
        loader=_ollama,
        provider="ollama",
        summary="Local inference via Ollama",
        auth="local Ollama service",
        location=InferenceLocation.LOCAL,
        capabilities=OLLAMA_CAPABILITIES,
    ),
    "openai_compat": BackendSpec(
        loader=_openai_compat,
        provider="compatible",
        summary="Self-hosted vLLM / llama.cpp / LM Studio",
        auth="server-specific",
        location=InferenceLocation.LOCAL,
        capabilities=OPENAI_COMPAT_CAPABILITIES,
        provider_aliases=("openai-compatible", "self-hosted"),
    ),
    "muse_glimmer": BackendSpec(
        loader=_muse_glimmer,
        provider="muse",
        summary="Muse Glimmer through local llama.cpp or vLLM",
        auth="local model weights",
        location=InferenceLocation.LOCAL,
        capabilities=MUSE_GLIMMER_CAPABILITIES,
        provider_aliases=("muse-glimmer",),
    ),
}

BACKEND_NAMES = tuple(BACKENDS)
PROVIDER_NAMES = tuple(spec.provider for spec in BACKENDS.values())


def _provider_backends() -> dict[str, str]:
    """Build every accepted provider spelling and its canonical backend."""
    aliases: dict[str, str] = {}
    for backend, spec in BACKENDS.items():
        for name in dict.fromkeys((spec.provider, *spec.provider_aliases, backend)):
            owner = aliases.setdefault(name, backend)
            if owner != backend:
                raise RuntimeError(f"provider name {name!r} belongs to two backends")
    return aliases


_PROVIDER_BACKENDS = _provider_backends()


def resolve_backend_name(name: str) -> str | None:
    """Resolve a provider name or canonical backend ID."""
    return _PROVIDER_BACKENDS.get(name)


def provider_name(backend: str) -> str:
    """Return the user-facing provider name for a canonical backend ID."""
    spec = BACKENDS.get(backend)
    if spec is None:
        raise ValueError(f"unknown backend {backend!r}")
    return spec.provider


def backend_capabilities(backend: str) -> Capabilities:
    """Return the canonical request surface for one backend ID."""
    spec = BACKENDS.get(backend)
    if spec is None:
        raise ValueError(f"unknown backend {backend!r}")
    return spec.capabilities


def inference_location(config: LLMConfig) -> InferenceLocation:
    """Resolve model placement from its backend and explicit endpoint."""
    spec = BACKENDS.get(config.backend)
    if spec is None:
        raise ValueError(f"unknown backend {config.backend!r}")
    if config.base_url is None:
        return spec.location

    return endpoint_location(config.base_url)


def endpoint_location(endpoint: str) -> InferenceLocation:
    """Return whether an HTTP endpoint stays on the operator's machine."""
    validate_endpoint(endpoint)

    hostname = (urlsplit(endpoint).hostname or "").rstrip(".").casefold()
    if hostname == "localhost":
        return InferenceLocation.LOCAL
    try:
        address = ipaddress.ip_address(hostname)
    except ValueError:
        return InferenceLocation.REMOTE
    return (
        InferenceLocation.LOCAL if address.is_loopback or address.is_unspecified else InferenceLocation.REMOTE
    )


def validate_backend_config(config: LLMConfig) -> None:
    """Validate controls whose accepted values depend on the backend."""
    spec = BACKENDS.get(config.backend)
    if spec is None:
        known = ", ".join(sorted(BACKENDS))
        raise ValueError(f"unknown backend '{config.backend}'; choose one of: {known}")
    if config.base_url is not None and not spec.custom_endpoint:
        raise ValueError(f"provider '{spec.provider}' does not accept a custom endpoint")
    if config.temperature is not None and not spec.capabilities.temperature:
        raise ValueError(f"provider '{spec.provider}' does not accept temperature")
    if config.effort is not None and config.effort not in spec.capabilities.effort_values:
        if not spec.capabilities.effort_values:
            raise ValueError(f"backend '{config.backend}' does not accept reasoning effort")
        values = ", ".join(sorted(spec.capabilities.effort_values))
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
    if backend.capabilities != spec.capabilities:
        raise LLMError(f"Backend capability metadata drifted for '{config.backend}'")
    return backend
