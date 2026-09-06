"""Model profiles — a short name for a tuned backend configuration.

A profile is the answer to "which model, on which backend, with what
settings". `autolean solve --model opus` resolves to one of these; anything
that does not resolve is passed through as a raw model string.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass, replace

from rich.table import Table
from rich.text import Text

from autolean.llm import (
    BACKENDS,
    DEFAULT_MAX_OUTPUT_TOKENS,
    DEFAULT_TEMPERATURE,
    PROVIDER_NAMES,
    LLMConfig,
    inference_location,
    provider_name,
    resolve_backend_name,
    validate_backend_config,
)
from autolean.llm.ollama import DEFAULT_OLLAMA_URL, probe_installed_models
from autolean.ui import console

_PROFILE_NAME = re.compile(r"[a-z0-9][a-z0-9-]*")


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

    def __post_init__(self) -> None:
        if not isinstance(self.aliases, tuple):
            raise ValueError("model profile aliases must be a tuple")
        identifiers = (self.name, *self.aliases)
        if any(
            not isinstance(value, str) or _PROFILE_NAME.fullmatch(value) is None for value in identifiers
        ) or len(set(identifiers)) != len(identifiers):
            raise ValueError("model profile names and aliases must be unique slugs")
        if any(not isinstance(value, str) for value in (self.description, self.setup_command)):
            raise ValueError("model profile help must be text")
        if self.escalates_to is not None and (
            not isinstance(self.escalates_to, str) or _PROFILE_NAME.fullmatch(self.escalates_to) is None
        ):
            raise ValueError("model escalation target must be a profile slug")
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
        validate_backend_config(config)

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
# Subscription-backed profiles use Claude, Codex, or Grok authentication.
# ---------------------------------------------------------------------------

_SUBSCRIPTION: tuple[ModelProfile, ...] = (
    ModelProfile(
        name="fable",
        model="fable",
        backend="claude_cli",
        temperature=None,
        effort="max",
        description="Claude Fable 5 — strongest long-horizon Claude model",
        setup_command="claude  # then /login",
        aliases=("claude", "claude-fable", "fable-5"),
    ),
    ModelProfile(
        name="opus",
        model="opus",
        backend="claude_cli",
        temperature=None,
        effort="high",
        description="Claude Opus 5 — complex coding and proof work",
        setup_command="claude  # then /login",
        aliases=("claude-opus", "opus-5"),
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
        model="gpt-6-astra",
        backend="codex_cli",
        temperature=None,
        effort="max",
        description="GPT-6 Astra — OpenAI frontier reasoning (ChatGPT subscription)",
        setup_command="codex login",
        aliases=("gpt", "gpt-5", "astra", "codex-astra"),
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
    ModelProfile(
        name="grok",
        model="grok-4.6",
        backend="grok_cli",
        temperature=None,
        effort="xhigh",
        description="Grok 4.6 — strongest SuperGrok / X Premium+ reasoning",
        setup_command="grok login",
        aliases=("grok-4", "grok-4-6"),
    ),
    ModelProfile(
        name="grok-4-5",
        model="grok-4.5",
        backend="grok_cli",
        temperature=None,
        effort="high",
        description="Grok 4.5 — previous SuperGrok / X Premium+ reasoning",
        setup_command="grok login",
        aliases=("grok-45",),
        escalates_to="grok",
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
        effort="max",
        description="Claude Fable 5 over the Messages API (ANTHROPIC_API_KEY)",
        setup_command="export ANTHROPIC_API_KEY=... && uv sync --extra anthropic",
        aliases=("anthropic", "claude-api", "claude-fable-api"),
    ),
    ModelProfile(
        name="opus-api",
        model="claude-opus-5",
        backend="anthropic",
        temperature=None,
        effort="high",
        description="Claude Opus 5 over the Messages API (ANTHROPIC_API_KEY)",
        setup_command="export ANTHROPIC_API_KEY=... && uv sync --extra anthropic",
        aliases=(),
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
        model="gpt-6-astra",
        backend="openai",
        temperature=None,
        effort="max",
        description="GPT-6 Astra over the Responses API (OPENAI_API_KEY)",
        setup_command="export OPENAI_API_KEY=... && uv sync --extra openai",
        aliases=("openai", "openai-api"),
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

#: Pinned Muse Glimmer revisions. The setup command downloads the revision
#: the profile records, so the artifact a user fetches is the one whose
#: digest is pinned beside it.
_GLIMMER_GGUF_REVISION = "93769bc7ab5ad1e9cd22d857e3138cf5d977ae81"
_GLIMMER_BF16_REVISION = "f84ecc3a0ea984a4c04542a84269e3d065350a6e"

_LOCAL: tuple[ModelProfile, ...] = (
    ModelProfile(
        name="muse-glimmer",
        model="muse-glimmer",
        backend="muse_glimmer",
        max_output_tokens=8192,
        temperature=0.0,
        effort="low",
        seed=0,
        revision=_GLIMMER_GGUF_REVISION,
        artifact_sha256="7e9b74b7c8875e9e265695df9613bf6290f2392e479ce740495a129019c488d8",
        description="Meta Muse Glimmer 30B — revision-pinned 17 GB GGUF via llama.cpp",
        setup_command=(
            "hf download meta-models/Muse-Glimmer-30B-GGUF "
            f"--revision {_GLIMMER_GGUF_REVISION} "
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
        revision=_GLIMMER_BF16_REVISION,
        description="Meta Muse Glimmer 30B BF16 — full weights via vLLM",
        setup_command=(
            "vllm serve meta-models/Muse-Glimmer-30B "
            f"--revision {_GLIMMER_BF16_REVISION} "
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


def _profile_registry(profiles: tuple[ModelProfile, ...]) -> dict[str, ModelProfile]:
    """Build one collision-free profile and alias vocabulary."""
    registry: dict[str, ModelProfile] = {}
    owners: dict[str, str] = {}
    for profile in profiles:
        if profile.name in registry:
            raise ValueError(f"duplicate model profile: {profile.name}")
        registry[profile.name] = profile
        for identifier in (profile.name, *profile.aliases):
            owner = owners.get(identifier)
            if owner is not None:
                raise ValueError(f"model name {identifier!r} belongs to both {owner!r} and {profile.name!r}")
            owners[identifier] = profile.name
    for profile in profiles:
        target = profile.escalates_to
        if target is not None and target not in registry:
            raise ValueError(f"model profile {profile.name!r} escalates to unknown {target!r}")
        seen = {profile.name}
        while target is not None:
            if target in seen:
                raise ValueError(f"model escalation cycle starts at {profile.name!r}")
            seen.add(target)
            target = registry[target].escalates_to
    return registry


PROFILES = _profile_registry((*_SUBSCRIPTION, *_HOSTED_API, *_LOCAL))

AUTO_PROFILE = "auto"

#: Strongest tuned profile for each automatically selectable provider.
MAX_PROFILE_BY_BACKEND = {
    "claude_cli": "fable",
    "codex_cli": "codex",
    "grok_cli": "grok",
    "anthropic": "fable-api",
    "openai": "gpt-api",
}

#: Subscription transports are preferred because they use the user's account.
_AUTO_SUBSCRIPTION_BACKENDS = ("codex_cli", "claude_cli", "grok_cli")
_AUTO_API_BACKENDS = ("openai", "anthropic")
_API_CREDENTIAL_ENV = {
    "anthropic": ("ANTHROPIC_API_KEY", "ANTHROPIC_AUTH_TOKEN"),
    "openai": ("OPENAI_API_KEY",),
}

#: Used when neither program.md nor the CLI names a model.
DEFAULT_PROFILE = AUTO_PROFILE


class ModelSelectionError(ValueError):
    """The machine has no provider that can satisfy automatic selection."""


def maximum_profile_for_backend(backend: str) -> ModelProfile:
    """Return the strongest tuned profile for one selectable provider."""
    canonical = resolve_backend_name(backend) or backend
    profile_name = MAX_PROFILE_BY_BACKEND.get(canonical)
    if profile_name is None:
        raise ModelSelectionError(
            f"provider {backend!r} requires an explicit model; "
            "automatic selection supports Claude, Codex, Grok, Anthropic, and OpenAI"
        )
    return PROFILES[profile_name]


def detect_default_profile(backend: str | None = None) -> ModelProfile:
    """Select the strongest profile for an authenticated provider."""
    if backend is not None:
        return maximum_profile_for_backend(backend)

    from autolean.llm.subscription import probe_subscription_backend

    for candidate in _AUTO_SUBSCRIPTION_BACKENDS:
        if probe_subscription_backend(candidate).ready:
            return maximum_profile_for_backend(candidate)

    for candidate in _AUTO_API_BACKENDS:
        if any(os.environ.get(name) for name in _API_CREDENTIAL_ENV[candidate]):
            return maximum_profile_for_backend(candidate)

    raise ModelSelectionError(
        "automatic model selection found no authenticated provider; run "
        "`claude` and /login, run `codex login`, run `grok login`, configure "
        "a hosted API key, or pass `--model`"
    )


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
    ("grok-", "grok_cli"),
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
    canonical_backend = resolve_backend_name(backend) if backend is not None else None
    selected_backend = canonical_backend or backend
    if selected_backend is not None and selected_backend not in BACKENDS:
        raise ModelSelectionError(f"unknown provider: {selected_backend!r}")
    profile = detect_default_profile(selected_backend) if model == AUTO_PROFILE else resolve_profile(model)
    if profile is not None:
        if selected_backend is not None and selected_backend != profile.backend:
            chosen = provider_name(profile.backend)
            requested = provider_name(selected_backend)
            raise ModelSelectionError(
                f"model profile {profile.name!r} belongs to provider {chosen!r}; "
                f"choose a {requested!r} profile or pass its provider model ID"
            )
        config = profile.to_config()
    else:
        raw_backend = selected_backend or infer_backend(model)
        config = LLMConfig(
            model=model,
            backend=raw_backend,
            temperature=(DEFAULT_TEMPERATURE if BACKENDS[raw_backend].capabilities.temperature else None),
        )

    resolved_temperature = config.temperature
    if temperature is not None:
        if not BACKENDS[config.backend].capabilities.temperature:
            raise ModelSelectionError(
                f"provider {provider_name(config.backend)!r} does not accept temperature"
            )
        resolved_temperature = temperature
    if max_output_tokens is not None and not BACKENDS[config.backend].capabilities.output_limit:
        raise ModelSelectionError(
            f"provider {provider_name(config.backend)!r} does not accept an output limit"
        )
    if base_url is not None and not BACKENDS[config.backend].custom_endpoint:
        raise ModelSelectionError(
            f"provider {provider_name(config.backend)!r} does not accept a custom endpoint"
        )
    resolved_effort = config.effort
    if effort is not None:
        resolved_effort = effort
    resolved = LLMConfig(
        model=config.model,
        backend=selected_backend or config.backend,
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
    validate_backend_config(resolved)
    return resolved


def profile_groups() -> list[tuple[str, tuple[ModelProfile, ...]]]:
    """Profiles grouped for display, cheapest-to-reach first."""
    return [
        ("Subscription", _SUBSCRIPTION),
        ("Hosted API", _HOSTED_API),
        ("Local", _LOCAL),
    ]


def profiles_for_backend(backend: str) -> tuple[ModelProfile, ...]:
    """Return profiles belonging to one provider or canonical backend ID."""
    canonical = resolve_backend_name(backend)
    if canonical is None:
        raise ModelSelectionError(f"unknown provider {backend!r}")
    return tuple(profile for profile in PROFILES.values() if profile.backend == canonical)


# ---------------------------------------------------------------------------
# Availability
# ---------------------------------------------------------------------------


def profile_status(profile: ModelProfile, installed_ollama: set[str]) -> str:
    """Report locally observable setup state for `autolean models`."""
    backend = profile.backend

    if backend in ("claude_cli", "codex_cli", "grok_cli"):
        from autolean.llm.subscription import probe_subscription_backend

        status = probe_subscription_backend(backend)
        if status.ready:
            return "[green]ready[/]"
        detail = status.detail.casefold()
        if "not found" in detail:
            binary = {"claude_cli": "claude", "codex_cli": "codex", "grok_cli": "grok"}[backend]
            return f"[red]no `{binary}`[/]"
        return "[yellow]sign-in required[/]"

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


def _print_automatic_default() -> None:
    """Print the provider selected by automatic detection."""
    try:
        automatic = detect_default_profile()
    except ModelSelectionError as error:
        console.print(f"[bold]Automatic default:[/] [yellow]setup required[/] — {error}")
    else:
        console.print(
            f"[bold]Automatic default:[/] {automatic.name} "
            f"[dim]({automatic.model} via {provider_name(automatic.backend)}, "
            f"effort {automatic.effort})[/]"
        )


def _installed_models(profiles: tuple[ModelProfile, ...]) -> set[str]:
    """Probe Ollama only when the requested view contains Ollama profiles."""
    if any(profile.backend == "ollama" for profile in profiles):
        return probe_installed_models(DEFAULT_OLLAMA_URL)
    return set()


def _profiles_table(
    title: str,
    profiles: tuple[ModelProfile, ...],
    installed: set[str],
) -> Table:
    """Build one concise model table."""
    table = Table(title=title, title_justify="left", header_style="bold")
    table.add_column("Model", style="bold cyan")
    table.add_column("Provider", style="magenta")
    table.add_column("Status")
    table.add_column("Description")
    for profile in profiles:
        table.add_row(
            profile.name,
            provider_name(profile.backend),
            profile_status(profile, installed),
            profile.description,
        )
    return table


def _print_setup(profiles: tuple[ModelProfile, ...], installed: set[str]) -> None:
    """Print each setup command needed by the selected profiles once."""
    commands = dict.fromkeys(
        profile.setup_command
        for profile in profiles
        if profile.setup_command and not profile_status(profile, installed).startswith("[green]")
    )
    if not commands:
        return
    console.print("\n[bold]Setup:[/]")
    for command in commands:
        console.print(f"  {command}")


def _print_provider(backend: str) -> None:
    """Print models and selection guidance for one provider."""
    spec = BACKENDS[backend]
    profiles = profiles_for_backend(backend)
    installed = _installed_models(profiles)
    console.print(f"[bold]{spec.provider}[/] — {spec.summary}")
    console.print(f"Authentication: {spec.auth}")
    console.print(f"Default inference: {spec.location.value}")
    default = MAX_PROFILE_BY_BACKEND.get(backend)
    if default is not None:
        console.print(f"Choose it: [cyan]autolean solve --provider {spec.provider}[/]")
    elif profiles:
        console.print(f"Choose one: [cyan]autolean solve --model {profiles[0].name}[/]")
    console.print(_profiles_table("Models", profiles, installed))
    _print_setup(profiles, installed)


def _print_profile(profile: ModelProfile) -> None:
    """Print the exact configuration and usage for one profile."""
    installed = _installed_models((profile,))
    console.print(f"[bold cyan]{profile.name}[/] — {profile.description}")
    details = Table.grid(padding=(0, 2))
    details.add_column(style="bold")
    details.add_column()
    details.add_row("Provider", provider_name(profile.backend))
    details.add_row("Provider model", profile.model)
    details.add_row("Inference", inference_location(profile.to_config()).value)
    details.add_row("Status", profile_status(profile, installed))
    if profile.effort is not None:
        details.add_row("Reasoning", profile.effort)
    if profile.escalates_to is not None:
        details.add_row("Stronger model", profile.escalates_to)
    if profile.aliases:
        details.add_row("Also called", ", ".join(profile.aliases))
    console.print(details)
    console.print(f"\nChoose it: [cyan]autolean solve --model {profile.name}[/]")
    _print_setup((profile,), installed)


def _selection(selection: str) -> tuple[str, str | ModelProfile]:
    """Resolve a models-command selector with exact names taking priority."""
    name = selection.lower()
    if name in PROVIDER_NAMES or name in BACKENDS:
        backend = resolve_backend_name(name)
        assert backend is not None
        return "provider", backend
    if name in PROFILES:
        return "profile", PROFILES[name]
    backend = resolve_backend_name(name)
    if backend is not None:
        return "provider", backend
    profile = resolve_profile(name)
    if profile is not None:
        return "profile", profile
    providers = ", ".join(PROVIDER_NAMES)
    raise ModelSelectionError(f"unknown model or provider {selection!r}; providers: {providers}")


def _profile_record(profile: ModelProfile, installed: set[str]) -> dict[str, object]:
    """Return one JSON-compatible model profile and its observed readiness."""
    config = profile.to_config()
    return {
        "name": profile.name,
        "provider": provider_name(profile.backend),
        "provider_model": profile.model,
        "inference": inference_location(config).value,
        "status": Text.from_markup(profile_status(profile, installed)).plain,
        "description": profile.description,
        "reasoning_effort": profile.effort,
        "temperature": profile.temperature,
        "max_output_tokens": profile.max_output_tokens,
        "seed": profile.seed,
        "endpoint": profile.base_url,
        "model_revision": profile.revision,
        "model_artifact_sha256": profile.artifact_sha256,
        "stronger_model": profile.escalates_to,
        "aliases": list(profile.aliases),
        "setup": profile.setup_command or None,
        "choose": f"autolean solve --model {profile.name}",
    }


def _provider_record(backend: str, profiles: tuple[ModelProfile, ...]) -> dict[str, object]:
    """Return one JSON-compatible provider and its request surface."""
    spec = BACKENDS[backend]
    maximum = MAX_PROFILE_BY_BACKEND.get(backend)
    choose = (
        f"autolean solve --provider {spec.provider}"
        if maximum is not None
        else (f"autolean solve --model {profiles[0].name}" if profiles else None)
    )
    return {
        "name": spec.provider,
        "summary": spec.summary,
        "authentication": spec.auth,
        "default_inference": spec.location.value,
        "default_model": maximum,
        "models": [profile.name for profile in profiles],
        "choose": choose,
        "controls": {
            "temperature": spec.capabilities.temperature,
            "reasoning_effort": sorted(spec.capabilities.effort_values),
            "stop_sequences": spec.capabilities.stop_sequences,
            "token_counts": spec.capabilities.token_counts,
            "output_limit": spec.capabilities.output_limit,
            "document_inputs": spec.capabilities.document_inputs,
            "custom_endpoint": spec.custom_endpoint,
        },
    }


def _automatic_default_record() -> dict[str, object]:
    """Return the automatic selection or its actionable setup boundary."""
    try:
        profile = detect_default_profile()
    except ModelSelectionError as error:
        return {"profile": None, "detail": str(error)}
    return {
        "profile": profile.name,
        "provider": provider_name(profile.backend),
        "provider_model": profile.model,
        "reasoning_effort": profile.effort,
    }


def model_catalog(selection: str | None = None) -> dict[str, object]:
    """Return the selected provider/profile catalog as stable JSON data."""
    if selection is None:
        profiles = tuple(PROFILES.values())
        backends = tuple(BACKENDS)
        selected = {"kind": "catalog", "name": "all"}
        automatic: dict[str, object] | None = _automatic_default_record()
    else:
        kind, value = _selection(selection)
        automatic = None
        if kind == "provider":
            assert isinstance(value, str)
            profiles = profiles_for_backend(value)
            backends = (value,)
            selected = {"kind": kind, "name": provider_name(value)}
        else:
            assert isinstance(value, ModelProfile)
            profiles = (value,)
            backends = (value.backend,)
            selected = {"kind": kind, "name": value.name}
    installed = _installed_models(profiles)
    return {
        "schema": "autolean-model-catalog-v1",
        "selection": selected,
        "automatic_default": automatic,
        "providers": [_provider_record(backend, profiles_for_backend(backend)) for backend in backends],
        "models": [_profile_record(profile, installed) for profile in profiles],
    }


def print_models_table(selection: str | None = None) -> None:
    """Print the model catalog or one selected provider or profile."""
    if selection is not None:
        kind, selected = _selection(selection)
        if kind == "provider":
            assert isinstance(selected, str)
            _print_provider(selected)
        else:
            assert isinstance(selected, ModelProfile)
            _print_profile(selected)
        return

    _print_automatic_default()
    all_profiles = tuple(PROFILES.values())
    installed = _installed_models(all_profiles)
    for group, profiles in profile_groups():
        console.print(_profiles_table(group, profiles, installed))

    providers = " | ".join(PROVIDER_NAMES)
    console.print("\n[bold]Choose:[/]")
    console.print("  [cyan]autolean models codex[/]         inspect one provider")
    console.print("  [cyan]autolean solve --model opus[/]  choose one model")
    console.print("  [cyan]autolean solve --provider codex[/]  choose a provider")
    console.print(f"\n[dim]Providers: {providers}[/]")
