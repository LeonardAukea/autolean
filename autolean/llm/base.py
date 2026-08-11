"""Provider-neutral vocabulary shared by every LLM backend.

The agent loop depends on this module and nothing else in `autolean.llm`:
it holds a `LLMBackend`, asks for text, and reads token counts back. Which
process or service produced that text is the backend's business.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol, Self, runtime_checkable
from urllib.parse import urlsplit

DEFAULT_TIMEOUT = 600.0
"""Seconds per request; hard reasoning requests can take several minutes."""

DEFAULT_MAX_OUTPUT_TOKENS = 32768
"""Requested ceiling for backends that expose an output-limit control."""

CLAUDE_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})
OPENAI_EFFORTS = frozenset({"none", "low", "medium", "high", "xhigh", "max"})
MUSE_GLIMMER_EFFORTS = frozenset({"low", "medium", "high", "xhigh"})


def validate_endpoint(endpoint: str | None) -> None:
    """Require an absolute HTTP endpoint without embedded credentials."""
    if endpoint is None:
        return
    parsed = urlsplit(endpoint)
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("endpoint must be an absolute HTTP or HTTPS URL")
    if parsed.username is not None or parsed.password is not None:
        raise ValueError("endpoint credentials belong in provider environment variables")


class LLMError(RuntimeError):
    """A backend could not produce a completion.

    Transport errors, non-zero CLI exits, schema failures, and model refusals
    cross the provider boundary through this type hierarchy.
    """


class LLMAuthenticationError(LLMError):
    """Provider credentials are absent or use the wrong billing mode."""


class LLMRateLimitError(LLMError):
    """Provider capacity or account quota cannot serve this run."""


class LLMTransientError(LLMError):
    """A retry can recover from a transport or provider outage."""


class LLMRefusalError(LLMError):
    """The provider declined the requested content."""


@dataclass(frozen=True)
class Capabilities:
    """Which request knobs a backend actually honours.

    Reasoning models reject `temperature` outright, so the agent asks before
    it escalates rather than sending a parameter that returns a 400.
    """

    temperature: bool = True
    effort_values: frozenset[str] = frozenset()
    stop_sequences: bool = True
    token_counts: bool = True
    output_limit: bool = True
    document_inputs: bool = False
    #: Retry policy may vary temperature when the model benefits from sampling.
    retry_temperature: bool = True

    @property
    def effort(self) -> bool:
        """Return whether the backend accepts a reasoning-effort control."""
        return bool(self.effort_values)


@dataclass(frozen=True)
class DocumentInput:
    """One bounded document delivered natively to a capable backend."""

    filename: str
    media_type: str
    data: bytes

    def __post_init__(self) -> None:
        if not self.filename or Path(self.filename).name != self.filename:
            raise ValueError("document filename must be one basename")
        if self.media_type != "application/pdf":
            raise ValueError("document media type must be application/pdf")
        if not self.data:
            raise ValueError("document data must not be empty")
        if len(self.data) > 32 * 1024 * 1024:
            raise ValueError("document data exceeds the 32 MiB request limit")

    @classmethod
    def from_path(cls, path: Path) -> DocumentInput:
        """Read one PDF after validating its regular-file identity."""
        path = path.resolve()
        if not path.is_file():
            raise ValueError(f"document is not a regular file: {path}")
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("document data exceeds the 32 MiB request limit")
        return cls(path.name, "application/pdf", path.read_bytes())


@dataclass(frozen=True)
class LLMConfig:
    """Everything needed to construct and drive one backend.

    `backend` selects the implementation (see `autolean.llm.registry`). Each
    backend applies the request fields declared by its `Capabilities`.
    """

    model: str
    backend: str = "ollama"
    base_url: str | None = None
    temperature: float | None = 0.4
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    timeout: float = DEFAULT_TIMEOUT
    effort: str | None = None
    #: Deterministic sampling seed for servers that expose one.
    seed: int | None = None
    #: Immutable weight or hosted-model revision recorded with experiments.
    model_revision: str | None = None
    #: SHA-256 of an exact local weight artifact when one file defines it.
    model_artifact_sha256: str | None = None
    # Anthropic's hosted API can retry refusals on a server-selected model.
    fallbacks: bool = True

    def __post_init__(self) -> None:
        if not self.model.strip():
            raise ValueError("model must not be empty")
        validate_endpoint(self.base_url)
        if self.max_output_tokens <= 0:
            raise ValueError("max_output_tokens must be positive")
        if not math.isfinite(self.timeout) or self.timeout <= 0:
            raise ValueError("timeout must be finite and positive")
        if self.temperature is not None and (
            not math.isfinite(self.temperature) or not 0 <= self.temperature <= 2
        ):
            raise ValueError("temperature must be finite and between 0 and 2")
        if self.seed is not None and self.seed < 0:
            raise ValueError("seed must be non-negative")
        if self.model_revision is not None and not self.model_revision.strip():
            raise ValueError("model_revision must not be empty")
        if self.model_artifact_sha256 is not None and not re.fullmatch(
            r"[0-9a-f]{64}", self.model_artifact_sha256
        ):
            raise ValueError("model_artifact_sha256 must be 64 lowercase hexadecimal characters")

    def resolved_temperature(self, override: float | None) -> float | None:
        """Pick the temperature for one request, preferring the override."""
        return override if override is not None else self.temperature


@dataclass
class LLMResponse:
    """One completion plus the accounting the tracker records."""

    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    duration_seconds: float = 0.0
    # Hosted providers can report per-call spend. Subscription providers use
    # plan-level accounting.
    cost_usd: float | None = None

    @property
    def tokens_per_second(self) -> float:
        if self.duration_seconds > 0:
            return self.output_tokens / self.duration_seconds
        return 0.0


class GenerateFn(Protocol):
    """A bound `LLMBackend.generate`, passed to helpers that only need text.

    Formalization and library generation take one of these instead of a whole
    backend, so they stay testable with a plain function.
    """

    def __call__(
        self,
        system: str,
        user: str,
        *,
        temperature: float | None = None,
        stop: list[str] | None = None,
    ) -> LLMResponse: ...


@runtime_checkable
class LLMBackend(Protocol):
    """The whole surface the agent loop needs from a model."""

    config: LLMConfig
    capabilities: Capabilities

    def ping(self) -> bool:
        """Run a non-generating local credential and reachability preflight."""
        ...

    def generate(
        self,
        system: str,
        user: str,
        *,
        temperature: float | None = None,
        stop: list[str] | None = None,
    ) -> LLMResponse:
        """Return one completion, or raise `LLMError`."""
        ...

    def close(self) -> None:
        """Release sockets, subprocesses, and other held resources."""
        ...

    def __enter__(self) -> Self: ...

    def __exit__(self, *exc: object) -> None: ...


@runtime_checkable
class DocumentBackend(Protocol):
    """Optional extension for providers with native document inputs."""

    capabilities: Capabilities

    def generate_with_documents(
        self,
        system: str,
        user: str,
        documents: tuple[DocumentInput, ...],
        *,
        temperature: float | None = None,
        stop: list[str] | None = None,
    ) -> LLMResponse: ...


@dataclass
class BaseBackend:
    """Shared lifecycle for concrete backends.

    Subclasses implement `ping` and `generate`; this supplies the context
    manager protocol so callers can write `with create_llm_client(cfg) as llm`.
    """

    config: LLMConfig
    capabilities: Capabilities = field(default_factory=Capabilities, repr=False)

    def close(self) -> None:
        """The default lifecycle holds no resources."""

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
