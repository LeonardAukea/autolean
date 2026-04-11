"""Model profiles — tuned configurations for different LLM backends.

Each profile captures the optimal settings for a specific model:
num_predict, temperature, thinking mode, backend, etc.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import httpx
from rich.console import Console
from rich.table import Table

console = Console()


@dataclass
class ModelProfile:
    """Configuration profile for a specific LLM."""

    name: str
    model: str  # Ollama model name or HuggingFace ID
    backend: str = "ollama"  # "ollama" | "openai_compat"
    base_url: str = "http://localhost:11434"
    num_predict: int = 4096
    temperature: float = 0.4
    thinking: bool = False  # Model uses thinking/CoT mode
    description: str = ""
    pull_command: str = ""  # How to install
    aliases: list[str] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Built-in profiles
# ---------------------------------------------------------------------------

PROFILES: dict[str, ModelProfile] = {
    "gemma4": ModelProfile(
        name="gemma4",
        model="gemma4:26b",
        num_predict=4096,
        temperature=0.4,
        thinking=True,
        description="Google Gemma 4 26B — general-purpose, extended thinking",
        pull_command="ollama pull gemma4:26b",
        aliases=["gemma", "gemma4-26b"],
    ),
    "gemma4-31b": ModelProfile(
        name="gemma4-31b",
        model="gemma4:31b",
        num_predict=4096,
        temperature=0.4,
        thinking=True,
        description="Google Gemma 4 31B — larger variant, better reasoning",
        pull_command="ollama pull gemma4:31b",
        aliases=["gemma-31b"],
    ),
    "deepseek-prover": ModelProfile(
        name="deepseek-prover",
        model="yinyaowenhua1314/deepseek-prover-v2-7b",
        num_predict=2048,
        temperature=0.3,
        thinking=True,
        description="DeepSeek Prover V2 7B — purpose-built Lean 4 prover (88.9% miniF2F)",
        pull_command="ollama pull yinyaowenhua1314/deepseek-prover-v2-7b",
        aliases=["deepseek", "prover", "dsp"],
    ),
    "bfs-prover": ModelProfile(
        name="bfs-prover",
        model="zeyu-zheng/BFS-Prover-V2-7B:q8_0",
        num_predict=256,
        temperature=0.2,
        thinking=False,
        description="BFS-Prover V2 7B — SOTA single-tactic prediction (ByteDance)",
        pull_command="ollama pull zeyu-zheng/BFS-Prover-V2-7B:q8_0",
        aliases=["bfs"],
    ),
    "bfs-prover-32b": ModelProfile(
        name="bfs-prover-32b",
        model="zeyu-zheng/BFS-Prover-V2-32B:q8_0",
        num_predict=512,
        temperature=0.2,
        thinking=False,
        description="BFS-Prover V2 32B — larger tactic predictor (128K context)",
        pull_command="ollama pull zeyu-zheng/BFS-Prover-V2-32B:q8_0",
        aliases=["bfs-32b"],
    ),
    "ntpctx": ModelProfile(
        name="ntpctx",
        model="wellecks/ntpctx-llama3-8b",
        num_predict=256,
        temperature=0.2,
        thinking=False,
        description="LeanDojo NTP-ctx Llama3 8B — tactic prediction with retrieval",
        pull_command="ollama pull wellecks/ntpctx-llama3-8b",
        aliases=["leandojo", "ntp"],
    ),
    "leanstral": ModelProfile(
        name="leanstral",
        model="mistralai/Leanstral-2603",
        backend="openai_compat",
        base_url="http://localhost:8000",
        num_predict=4096,
        temperature=0.3,
        thinking=True,
        description="Mistral Leanstral 119B MoE — requires vLLM (68+ GB VRAM)",
        pull_command="vllm serve mistralai/Leanstral-2603 --tensor-parallel-size 4",
        aliases=["mistral"],
    ),
}


def resolve_profile(name: str) -> ModelProfile | None:
    """Resolve a profile by name or alias. Returns None if not found."""
    # Direct name match
    if name in PROFILES:
        return PROFILES[name]
    # Alias match
    for profile in PROFILES.values():
        if name in profile.aliases:
            return profile
    return None


def check_ollama_models() -> set[str]:
    """Query Ollama for locally available models."""
    try:
        resp = httpx.get("http://localhost:11434/api/tags", timeout=5.0)
        resp.raise_for_status()
        return {m["name"] for m in resp.json().get("models", [])}
    except Exception:
        return set()


def print_models_table() -> None:
    """Print a table of available model profiles with installation status."""
    installed = check_ollama_models()

    table = Table(title="AutoLean Model Profiles")
    table.add_column("Name", style="bold")
    table.add_column("Status")
    table.add_column("Backend")
    table.add_column("Tokens")
    table.add_column("Description")

    for name, p in PROFILES.items():
        # Check if installed
        if p.backend == "ollama":
            is_installed = any(
                p.model == m or p.model.startswith(m) or m.startswith(p.model)
                for m in installed
            )
            if is_installed:
                status = "[green]installed[/]"
            else:
                status = f"[red]not installed[/]"
        else:
            # OpenAI-compat: try to reach the server
            try:
                httpx.get(f"{p.base_url}/v1/models", timeout=2.0)
                status = "[green]reachable[/]"
            except Exception:
                status = "[yellow]offline[/]"

        table.add_row(
            name,
            status,
            p.backend,
            str(p.num_predict),
            p.description,
        )

    console.print(table)

    # Show install commands for missing models
    console.print("\n[bold]Install commands:[/]")
    for name, p in PROFILES.items():
        if p.backend == "ollama":
            is_installed = any(
                p.model == m or p.model.startswith(m) or m.startswith(p.model)
                for m in installed
            )
            if not is_installed and p.pull_command:
                console.print(f"  {p.pull_command}")
