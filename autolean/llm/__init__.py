"""LLM backends.

Seven backends behind one `LLMBackend` protocol:

| backend         | model runs on            | pays with            |
|-----------------|--------------------------|----------------------|
| `claude_cli`    | Anthropic                | Claude subscription  |
| `codex_cli`     | OpenAI                   | ChatGPT subscription |
| `anthropic`     | Anthropic                | API credit           |
| `openai`        | OpenAI                   | API credit           |
| `ollama`        | this machine             | electricity          |
| `openai_compat` | a server you run         | electricity          |
| `muse_glimmer`  | local llama.cpp or vLLM  | electricity          |

Pick one with `LLMConfig(backend=...)` and build it with
`create_llm_client`; the agent loop never learns which it got.
"""

from autolean.llm.base import (
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_TIMEOUT,
    BaseBackend,
    Capabilities,
    DocumentBackend,
    DocumentInput,
    GenerateFn,
    LLMAuthenticationError,
    LLMBackend,
    LLMConfig,
    LLMError,
    LLMRateLimitError,
    LLMRefusalError,
    LLMResponse,
    LLMTransientError,
    validate_endpoint,
)
from autolean.llm.registry import (
    BACKEND_NAMES,
    BACKENDS,
    BackendSpec,
    create_llm_client,
    validate_backend_config,
)

__all__ = [
    "BACKENDS",
    "BACKEND_NAMES",
    "DEFAULT_MAX_OUTPUT_TOKENS",
    "DEFAULT_TIMEOUT",
    "BackendSpec",
    "BaseBackend",
    "Capabilities",
    "DocumentBackend",
    "DocumentInput",
    "GenerateFn",
    "LLMAuthenticationError",
    "LLMBackend",
    "LLMConfig",
    "LLMError",
    "LLMRateLimitError",
    "LLMRefusalError",
    "LLMResponse",
    "LLMTransientError",
    "create_llm_client",
    "validate_backend_config",
    "validate_endpoint",
]
