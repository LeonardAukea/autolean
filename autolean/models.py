"""Model profiles — a short name for a tuned backend configuration.

A profile is the answer to "which model, on which backend, with what
settings". `autolean solve --model opus` resolves to one of these; anything
that does not resolve is passed through as a raw model string.
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass, replace

from rich.console import Console
from rich.table import Table

from autolean.llm import BACKENDS, DEFAULT_MAX_OUTPUT_TOKENS, LLMConfig
from autolean.llm.ollama import DEFAULT_OLLAMA_URL, probe_installed_models

console = Console()


@dataclass(frozen=True)
class ModelProfile:
    """A named, tuned configuration for one model."""

    name: str
    model: str
    backend: str = "ollama"
    base_url: str | None = None
    max_output_tokens: int = DEFAULT_MAX_OUTPUT_TOKENS
    #: None where the backend uses the model's default sampling configuration.
    temperature: float | None = 0.4
    #: Reasoning depth: low | medium | high | xhigh | max.
    effort: str | None = None
    seed: int | None = None
    revision: str | None = None
    artifact_sha256: str | None = None
    description: str = ""
    #: How to make this profile usable — install, pull, or sign in.
    setup_command: str = ""
    aliases: tuple[str, ...] = ()
    escalates_to: str | None = None

    def to_config(self, *, timeout: float | None = None) -> LLMConfig:
        """Build the `LLMConfig` this profile describes."""
        config = LLMConfig(
            model=self.model,
            backend=self.backend,
            base_url=self.base_url,
            temperature=self.temperature,
            max_output_tokens=self.max_output_tokens,
            effort=self.effort,
            seed=self.seed,
            model_revision=self.revision,
            model_artifact_sha256=self.artifact_sha256,
        )
        return replace(config, timeout=timeout) if timeout is not None else config


# ---------------------------------------------------------------------------
# Subscription-backed profiles use Claude Code or Codex authentication.
# ---------------------------------------------------------------------------

_SUBSCRIPTION: tuple[ModelProfile, ...] = (
    ModelProfile(
        name="fable",
        model="fable",
        backend="claude_cli",
        temperature=None,
        effort="high",
        description="Claude Fable 5 — strongest long-horizon Claude model",
        setup_command="claude  # then /login",
        aliases=("claude-fable", "fable-5"),
    ),
    ModelProfile(
        name="opus",
        model="opus",
        backend="claude_cli",
        temperature=None,
        effort="high",
        description="Claude Opus 5 — complex coding and proof work",
        setup_command="claude  # then /login",
        aliases=("claude", "claude-opus", "opus-5"),
        escalates_to="fable",
    ),
    ModelProfile(
        name="sonnet",
        model="sonnet",
        backend="claude_cli",
        temperature=None,
        effort="high",
        description="Claude Sonnet 5 — fast, capable proof iteration",
        setup_command="claude  # then /login",
        aliases=("claude-sonnet", "sonnet-5"),
        escalates_to="opus",
    ),
    ModelProfile(
        name="codex",
        model="gpt-5.6-sol",
        backend="codex_cli",
        temperature=None,
        effort="high",
        description="GPT-5.6 Sol — OpenAI frontier reasoning (ChatGPT subscription)",
        setup_command="codex login",
        aliases=("gpt", "openai", "gpt-5"),
    ),
    ModelProfile(
        name="codex-terra",
        model="gpt-5.6-terra",
        backend="codex_cli",
        temperature=None,
        effort="high",
        description="GPT-5.6 Terra — balanced OpenAI reasoning",
        setup_command="codex login",
        aliases=("terra", "gpt-terra"),
        escalates_to="codex",
    ),
    ModelProfile(
        name="codex-luna",
        model="gpt-5.6-luna",
        backend="codex_cli",
        temperature=None,
        effort="high",
        description="GPT-5.6 Luna — efficient OpenAI reasoning",
        setup_command="codex login",
        aliases=("luna", "gpt-luna"),
        escalates_to="codex-terra",
    ),
)

# ---------------------------------------------------------------------------
# Hosted-API profiles — metered per token
# ---------------------------------------------------------------------------

_HOSTED_API: tuple[ModelProfile, ...] = (
    ModelProfile(
        name="fable-api",
        model="claude-fable-5",
        backend="anthropic",
        temperature=None,
        effort="high",
        description="Claude Fable 5 over the Messages API (ANTHROPIC_API_KEY)",
        setup_command="export ANTHROPIC_API_KEY=... && uv sync --extra anthropic",
        aliases=("claude-fable-api",),
    ),
    ModelProfile(
        name="opus-api",
        model="claude-opus-5",
        backend="anthropic",
        temperature=None,
        effort="high",
        description="Claude Opus 5 over the Messages API (ANTHROPIC_API_KEY)",
        setup_command="export ANTHROPIC_API_KEY=... && uv sync --extra anthropic",
        aliases=("claude-api", "anthropic"),
        escalates_to="fable-api",
    ),
    ModelProfile(
        name="sonnet-api",
        model="claude-sonnet-5",
        backend="anthropic",
        temperature=None,
        effort="high",
        description="Claude Sonnet 5 over the Messages API (ANTHROPIC_API_KEY)",
        setup_command="export ANTHROPIC_API_KEY=... && uv sync --extra anthropic",
        aliases=(),
        escalates_to="opus-api",
    ),
    ModelProfile(
        name="gpt-api",
        model="gpt-5.6-sol",
        backend="openai",
        temperature=None,
        effort="high",
        description="GPT-5.6 Sol over the Responses API (OPENAI_API_KEY)",
        setup_command="export OPENAI_API_KEY=... && uv sync --extra openai",
        aliases=("openai-api",),
    ),
    ModelProfile(
        name="gpt-terra-api",
        model="gpt-5.6-terra",
        backend="openai",
        temperature=None,
        effort="high",
        description="GPT-5.6 Terra over the Responses API (OPENAI_API_KEY)",
        setup_command="export OPENAI_API_KEY=... && uv sync --extra openai",
        aliases=("openai-terra-api",),
        escalates_to="gpt-api",
    ),
    ModelProfile(
        name="gpt-luna-api",
        model="gpt-5.6-luna",
        backend="openai",
        temperature=None,
        effort="high",
        description="GPT-5.6 Luna over the Responses API (OPENAI_API_KEY)",
        setup_command="export OPENAI_API_KEY=... && uv sync --extra openai",
        aliases=("openai-luna-api",),
        escalates_to="gpt-terra-api",
    ),
)

# ---------------------------------------------------------------------------
# Local profiles — Lean-specialised open weights
# ---------------------------------------------------------------------------

_LOCAL: tuple[ModelProfile, ...] = (
    ModelProfile(
        name="muse-glimmer",
        model="muse-glimmer",
        backend="muse_glimmer",
        max_output_tokens=8192,
        temperature=0.0,
        effort="low",
        seed=0,
        revision="93769bc7ab5ad1e9cd22d857e3138cf5d977ae81",
        artifact_sha256="7e9b74b7c8875e9e265695df9613bf6290f2392e479ce740495a129019c488d8",
        description="Meta Muse Glimmer 30B — revision-pinned 17 GB GGUF via llama.cpp",
        setup_command=(
            "hf download meta-models/Muse-Glimmer-30B-GGUF "
            "--revision 93769bc7ab5ad1e9cd22d857e3138cf5d977ae81 "
            "--include muse-glimmer-30B-kquant-17gb.gguf --local-dir muse-glimmer"
        ),
        aliases=("muse", "glimmer", "muse-glimmer-30b"),
    ),
    ModelProfile(
        name="muse-glimmer-bf16",
        model="muse-glimmer",
        backend="muse_glimmer",
        base_url="http://localhost:8000",
        max_output_tokens=8192,
        temperature=0.0,
        effort="low",
        seed=0,
        revision="f84ecc3a0ea984a4c04542a84269e3d065350a6e",
        description="Meta Muse Glimmer 30B BF16 — full weights via vLLM",
        setup_command=(
            "vllm serve meta-models/Muse-Glimmer-30B "
            "--revision f84ecc3a0ea984a4c04542a84269e3d065350a6e "
            "--served-model-name muse-glimmer --reasoning-parser muse-glimmer "
            "--generation-config auto"
        ),
        aliases=("muse-bf16",),
    ),
    ModelProfile(
        name="gemma4",
        model="gemma4:26b",
        temperature=0.4,
        description="Google Gemma 4 26B — general-purpose local default",
        setup_command="ollama pull gemma4:26b",
        aliases=("gemma", "gemma4-26b"),
        escalates_to="gemma4-31b",
    ),
    ModelProfile(
        name="gemma4-31b",
        model="gemma4:31b",
        temperature=0.4,
        description="Google Gemma 4 31B — larger variant, better reasoning",
        setup_command="ollama pull gemma4:31b",
        aliases=("gemma-31b",),
    ),
    ModelProfile(
        name="deepseek-prover",
        model="yinyaowenhua1314/deepseek-prover-v2-7b",
        temperature=0.3,
        description="DeepSeek Prover V2 7B — purpose-built Lean 4 prover (88.9% miniF2F)",
        setup_command="ollama pull yinyaowenhua1314/deepseek-prover-v2-7b",
        aliases=("deepseek", "prover", "dsp"),
    ),
    ModelProfile(
        name="bfs-prover",
        model="zeyu-zheng/BFS-Prover-V2-7B:q8_0",
        max_output_tokens=256,
        temperature=0.2,
        description="BFS-Prover V2 7B — SOTA single-tactic prediction (ByteDance)",
        setup_command="ollama pull zeyu-zheng/BFS-Prover-V2-7B:q8_0",
        aliases=("bfs",),
        escalates_to="bfs-prover-32b",
    ),
    ModelProfile(
        name="bfs-prover-32b",
        model="zeyu-zheng/BFS-Prover-V2-32B:q8_0",
        max_output_tokens=512,
        temperature=0.2,
        description="BFS-Prover V2 32B — larger tactic predictor (128K context)",
        setup_command="ollama pull zeyu-zheng/BFS-Prover-V2-32B:q8_0",
        aliases=("bfs-32b",),
    ),
    ModelProfile(
        name="ntpctx",
        model="wellecks/ntpctx-llama3-8b",
        max_output_tokens=256,
        temperature=0.2,
        description="LeanDojo NTP-ctx Llama3 8B — tactic prediction with retrieval",
        setup_command="ollama pull wellecks/ntpctx-llama3-8b",
        aliases=("leandojo", "ntp"),
    ),
    ModelProfile(
        name="leanstral",
        model="mistralai/Leanstral-2603",
        backend="openai_compat",
        base_url="http://localhost:8000",
        temperature=0.3,
        description="Mistral Leanstral 119B MoE — needs vLLM (68+ GB VRAM)",
        setup_command="vllm serve mistralai/Leanstral-2603 --tensor-parallel-size 4",
        aliases=("mistral",),
    ),
)

PROFILES: dict[str, ModelProfile] = {p.name: p for p in (*_SUBSCRIPTION, *_HOSTED_API, *_LOCAL)}

#: Used when neither program.md nor the CLI names a model.
DEFAULT_PROFILE = "opus"


def resolve_profile(name: str) -> ModelProfile | None:
    """Look a profile up by name or alias. None means "a raw model string"."""
    if name in PROFILES:
        return PROFILES[name]
    return next((p for p in PROFILES.values() if name in p.aliases), None)


#: Prefix → backend, for raw model strings given without an explicit backend.
_MODEL_PREFIX_BACKEND = (
    ("meta-models/Muse-Glimmer", "muse_glimmer"),
    ("claude-", "anthropic"),
    ("gpt-", "openai"),
    ("o3", "openai"),
    ("o4", "openai"),
)


def infer_backend(model: str) -> str:
    """Guess the backend for a raw model string.

    Vendor model IDs are recognisable on sight, so `--model claude-opus-5`
    reaches the hosted Anthropic API directly. Unrecognised names are treated
    as Ollama tags.
    """
    for prefix, backend in _MODEL_PREFIX_BACKEND:
        if model.startswith(prefix):
            return backend
    return "ollama"


def resolve_llm_config(
    model: str,
    *,
    backend: str | None = None,
    base_url: str | None = None,
    temperature: float | None = None,
    timeout: float | None = None,
    max_output_tokens: int | None = None,
    effort: str | None = None,
) -> LLMConfig:
    """Turn a model name plus overrides into one `LLMConfig`.

    `model` is either a profile name or alias — which supplies the backend
    and tuned defaults — or a raw model string, whose backend is taken from
    `backend` or inferred from the string. Reasoning profiles retain their
    required default sampling configuration.
    """
    profile = resolve_profile(model)
    if profile is not None:
        config = profile.to_config()
    else:
        config = LLMConfig(model=model, backend=backend or infer_backend(model))

    resolved_temperature = config.temperature
    if temperature is not None and not (profile is not None and profile.temperature is None):
        resolved_temperature = temperature
    resolved_effort = config.effort
    if backend is not None and backend != config.backend and effort is None:
        resolved_effort = None
    elif effort is not None:
        resolved_effort = effort
    return LLMConfig(
        model=config.model,
        backend=backend or config.backend,
        base_url=base_url or config.base_url,
        temperature=resolved_temperature,
        max_output_tokens=(max_output_tokens if max_output_tokens is not None else config.max_output_tokens),
        timeout=timeout if timeout is not None else config.timeout,
        effort=resolved_effort,
        seed=config.seed,
        model_revision=config.model_revision,
        model_artifact_sha256=config.model_artifact_sha256,
        fallbacks=config.fallbacks,
    )


def profile_groups() -> list[tuple[str, tuple[ModelProfile, ...]]]:
    """Profiles grouped for display, cheapest-to-reach first."""
    return [
        ("Subscription", _SUBSCRIPTION),
        ("Hosted API", _HOSTED_API),
        ("Local", _LOCAL),
    ]


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------

_API_CREDENTIAL_ENV = {
    "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
    "openai": ("OPENAI_API_KEY",),
}
_CLI_BINARY = {"claude_cli": "claude", "codex_cli": "codex"}


def profile_status(profile: ModelProfile, installed_ollama: set[str]) -> str:
    """Report locally observable setup state for `autolean models`."""
    backend = profile.backend

    if backend in _CLI_BINARY:
        binary = _CLI_BINARY[backend]
        found = shutil.which(os.environ.get(f"AUTOLEAN_{binary.upper()}_BIN", binary))
        return "[green]installed[/]" if found else f"[red]no `{binary}`[/]"

    if backend in _API_CREDENTIAL_ENV:
        observed = next(
            (name for name in _API_CREDENTIAL_ENV[backend] if os.environ.get(name)),
            None,
        )
        if observed:
            return f"[green]{observed} set[/]"
        if backend == "anthropic":
            return "[yellow]credential unverified[/]"
        return "[yellow]no $OPENAI_API_KEY[/]"

    if backend == "ollama":
        pulled = any(
            profile.model == m or profile.model.startswith(m) or m.startswith(profile.model)
            for m in installed_ollama
        )
        return "[green]pulled[/]" if pulled else "[red]not pulled[/]"

    # openai_compat: reachability depends on a server the user starts.
    return "[dim]self-hosted[/]"


def print_models_table() -> None:
    """Print every profile with its backend, readiness, and description."""
    installed = probe_installed_models(DEFAULT_OLLAMA_URL)

    for group, profiles in profile_groups():
        table = Table(title=f"{group} models", title_justify="left", header_style="bold")
        table.add_column("Profile", style="bold cyan")
        table.add_column("Status")
        table.add_column("Backend", style="dim")
        table.add_column("Stronger sibling", style="dim")
        table.add_column("Description")

        for profile in profiles:
            table.add_row(
                profile.name,
                profile_status(profile, installed),
                profile.backend,
                profile.escalates_to or "—",
                profile.description,
            )
        console.print(table)

    missing = [
        p
        for p in PROFILES.values()
        if p.setup_command and not profile_status(p, installed).startswith("[green]")
    ]
    if missing:
        console.print("\n[bold]Setup for the profiles above that need it:[/]")
        for profile in missing:
            console.print(f"  [dim]{profile.name:16}[/] {profile.setup_command}")

    console.print("\n[bold]Backends:[/]")
    for name, spec in BACKENDS.items():
        console.print(f"  [dim]{name:16}[/] {spec.summary} — auth: {spec.auth}")
