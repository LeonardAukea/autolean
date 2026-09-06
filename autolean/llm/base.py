"""Provider-neutral vocabulary shared by every LLM backend.

The agent loop depends on this module and nothing else in `autolean.llm`:
it holds a `LLMBackend`, asks for text, and reads token counts back. Which
process or service produced that text is the backend's business.
"""

from __future__ import annotations

import hashlib
import math
import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Protocol, Self, runtime_checkable
from urllib.parse import urlsplit

from autolean.validation import (
    require_bool,
    require_int,
    require_number,
    require_optional_int,
    require_optional_number,
    require_sha256,
    require_text,
)

DEFAULT_TIMEOUT = 600.0
"""Seconds per request; hard reasoning requests can take several minutes."""

DEFAULT_MAX_OUTPUT_TOKENS = 32768
"""Requested ceiling for backends that expose an output-limit control."""

DEFAULT_TEMPERATURE = 0.4
"""Sampling temperature for raw models whose provider supports it."""

CLAUDE_EFFORTS = frozenset({"low", "medium", "high", "xhigh", "max"})
OPENAI_EFFORTS = frozenset({"none", "low", "medium", "high", "xhigh", "max"})
GROK_EFFORTS = frozenset({"low", "medium", "high", "xhigh"})
MUSE_GLIMMER_EFFORTS = frozenset({"low", "medium", "high", "xhigh"})


class InferenceLocation(StrEnum):
    """Whether model inputs leave the operator's machine."""

    LOCAL = "local"
    REMOTE = "remote"


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


def token_count(value: object) -> int:
    """Decode an optional non-negative provider counter; absence is zero."""
    if value is None:
        return 0
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise LLMError("provider token count must be a non-negative integer")
    return value


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

    def __post_init__(self) -> None:
        flags = (
            self.temperature,
            self.stop_sequences,
            self.token_counts,
            self.output_limit,
            self.document_inputs,
            self.retry_temperature,
        )
        if not all(isinstance(flag, bool) for flag in flags):
            raise ValueError("capability flags must be booleans")
        if not isinstance(self.effort_values, frozenset) or any(
            not isinstance(value, str) or not value for value in self.effort_values
        ):
            raise ValueError("capability effort values must be a string set")

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
        if (
            not isinstance(self.filename, str)
            or not self.filename
            or self.filename in {".", ".."}
            or Path(self.filename).name != self.filename
        ):
            raise ValueError("document filename must be one basename")
        if self.media_type != "application/pdf":
            raise ValueError("document media type must be application/pdf")
        if not isinstance(self.data, bytes) or not self.data:
            raise ValueError("document data must not be empty")
        if len(self.data) > 32 * 1024 * 1024:
            raise ValueError("document data exceeds the 32 MiB request limit")

    @property
    def sha256(self) -> str:
        """Return the content identity of the transferred bytes."""
        return hashlib.sha256(self.data).hexdigest()

    @property
    def size_bytes(self) -> int:
        """Return the exact transfer size."""
        return len(self.data)

    @classmethod
    def from_path(cls, path: Path) -> DocumentInput:
        """Read one PDF after validating its regular-file identity."""
        path = path.resolve()
        if not path.is_file():
            raise ValueError(f"document is not a regular file: {path}")
        if path.stat().st_size > 32 * 1024 * 1024:
            raise ValueError("document data exceeds the 32 MiB request limit")
        with path.open("rb") as handle:
            data = handle.read(32 * 1024 * 1024 + 1)
        return cls(path.name, "application/pdf", data)


@dataclass(frozen=True)
class LLMConfig:
    """Everything needed to construct and drive one backend.

    `backend` selects the implementation (see `autolean.llm.registry`). Each
    backend applies the request fields declared by its `Capabilities`.
    """

    model: str
    backend: str = "ollama"
    base_url: str | None = None
    temperature: float | None = None
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
        require_text(self.model, "model must not be empty")
        require_text(self.backend, "backend must not be empty")
        if self.base_url is not None:
            require_text(self.base_url, "endpoint must be text", allow_empty=True)
        validate_endpoint(self.base_url)
        require_int(
            self.max_output_tokens,
            "max_output_tokens must be positive",
            minimum=1,
        )
        require_number(
            self.timeout,
            "timeout must be finite and positive",
            minimum=0.0,
            minimum_inclusive=False,
        )
        require_optional_number(
            self.temperature,
            "temperature must be finite and between 0 and 2",
            minimum=0.0,
            maximum=2.0,
        )
        require_optional_int(self.seed, "seed must be non-negative", minimum=0)
        if self.effort is not None and (not isinstance(self.effort, str) or not self.effort.strip()):
            raise ValueError("effort must not be empty")
        if self.model_revision is not None and (
            not isinstance(self.model_revision, str) or not self.model_revision.strip()
        ):
            raise ValueError("model_revision must not be empty")
        if self.model_artifact_sha256 is not None:
            require_sha256(
                self.model_artifact_sha256,
                "model_artifact_sha256 must be 64 lowercase hexadecimal characters",
            )
        require_bool(self.fallbacks, "fallbacks must be a boolean")

    def resolved_temperature(self, override: float | None) -> float | None:
        """Pick the temperature for one request, preferring the override."""
        return override if override is not None else self.temperature


@dataclass(frozen=True)
class LLMResponse:
    """One completion plus the accounting the tracker records."""

    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    duration_seconds: float = 0.0

    def __post_init__(self) -> None:
        if not isinstance(self.text, str) or not self.text.strip():
            raise ValueError("response text must not be empty")
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError("response model must not be empty")
        if any(
            isinstance(count, bool) or not isinstance(count, int) or count < 0
            for count in (self.input_tokens, self.output_tokens)
        ):
            raise ValueError("response token counts must be non-negative integers")
        if (
            isinstance(self.duration_seconds, bool)
            or not isinstance(self.duration_seconds, (int, float))
            or not math.isfinite(self.duration_seconds)
            or self.duration_seconds < 0
        ):
            raise ValueError("response duration must be finite and non-negative")

    @property
    def tokens_per_second(self) -> float:
        if self.duration_seconds > 0:
            return self.output_tokens / self.duration_seconds
        return 0.0


@dataclass(frozen=True)
class ModelCallReceipt:
    """Content, accounting, and placement for one returned model response."""

    model: str
    backend: str
    location: InferenceLocation
    request_sha256: str
    response_sha256: str
    input_tokens: int
    output_tokens: int
    duration_seconds: float

    def __post_init__(self) -> None:
        if any(not isinstance(value, str) or not value.strip() for value in (self.model, self.backend)):
            raise ValueError("model call identity must be complete")
        if not isinstance(self.location, InferenceLocation):
            raise ValueError("model call location must be local or remote")
        for name, digest in (
            ("request", self.request_sha256),
            ("response", self.response_sha256),
        ):
            if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest) is None:
                raise ValueError(f"model call {name} digest is invalid")
        if any(
            isinstance(value, bool) or not isinstance(value, int) or value < 0
            for value in (self.input_tokens, self.output_tokens)
        ):
            raise ValueError("model call token counts must be non-negative integers")
        if (
            isinstance(self.duration_seconds, bool)
            or not isinstance(self.duration_seconds, (int, float))
            or not math.isfinite(self.duration_seconds)
            or self.duration_seconds < 0
        ):
            raise ValueError("model call duration must be finite and non-negative")

    @classmethod
    def from_response(
        cls,
        config: LLMConfig,
        response: LLMResponse,
        *,
        location: InferenceLocation,
        system: str,
        user: str,
    ) -> ModelCallReceipt:
        """Bind one response to the exact request and effective placement."""
        if not isinstance(config, LLMConfig) or not isinstance(response, LLMResponse):
            raise ValueError("model call receipt requires typed config and response")
        if not isinstance(system, str) or not isinstance(user, str):
            raise ValueError("model call request must be text")
        return cls(
            model=response.model,
            backend=config.backend,
            location=location,
            request_sha256=hashlib.sha256(f"{system}\0{user}".encode()).hexdigest(),
            response_sha256=hashlib.sha256(response.text.encode()).hexdigest(),
            input_tokens=response.input_tokens,
            output_tokens=response.output_tokens,
            duration_seconds=response.duration_seconds,
        )

    def as_dict(self) -> dict[str, object]:
        """Return the canonical JSON-compatible receipt."""
        return {
            "backend": self.backend,
            "duration_seconds": self.duration_seconds,
            "input_tokens": self.input_tokens,
            "location": self.location.value,
            "model": self.model,
            "output_tokens": self.output_tokens,
            "request_sha256": self.request_sha256,
            "response_sha256": self.response_sha256,
        }


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

    def __post_init__(self) -> None:
        if not isinstance(self.config, LLMConfig):
            raise ValueError("backend config must use LLMConfig")
        if not isinstance(self.capabilities, Capabilities):
            raise ValueError("backend capabilities must use Capabilities")

    def close(self) -> None:
        """The default lifecycle holds no resources."""

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()
