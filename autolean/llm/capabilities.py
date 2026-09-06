"""Canonical request capabilities for every model provider adapter."""

from autolean.llm.base import (
    CLAUDE_EFFORTS,
    GROK_EFFORTS,
    MUSE_GLIMMER_EFFORTS,
    OPENAI_EFFORTS,
    Capabilities,
)

CLAUDE_CLI_CAPABILITIES = Capabilities(
    temperature=False,
    effort_values=CLAUDE_EFFORTS,
    stop_sequences=False,
    output_limit=False,
)
CODEX_CLI_CAPABILITIES = Capabilities(
    temperature=False,
    effort_values=OPENAI_EFFORTS,
    stop_sequences=False,
    output_limit=False,
)
GROK_CLI_CAPABILITIES = Capabilities(
    temperature=False,
    effort_values=GROK_EFFORTS,
    stop_sequences=False,
    output_limit=False,
)
ANTHROPIC_CAPABILITIES = Capabilities(
    temperature=False,
    effort_values=CLAUDE_EFFORTS,
    document_inputs=True,
)
OPENAI_CAPABILITIES = Capabilities(
    temperature=False,
    effort_values=OPENAI_EFFORTS,
    stop_sequences=False,
    document_inputs=True,
)
OLLAMA_CAPABILITIES = Capabilities(temperature=True)
OPENAI_COMPAT_CAPABILITIES = Capabilities(temperature=True)
MUSE_GLIMMER_CAPABILITIES = Capabilities(
    temperature=True,
    effort_values=MUSE_GLIMMER_EFFORTS,
    retry_temperature=False,
)
