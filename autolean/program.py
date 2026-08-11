"""Typed configuration for one AutoLean proof program."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from pathlib import Path

from autolean.llm import LLMConfig, validate_backend_config, validate_endpoint
from autolean.models import DEFAULT_PROFILE, resolve_llm_config

DEFAULT_MAX_PROOF_LINES = 30


def _require_positive(name: str, value: int | float) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be positive")


def _require_finite_range(name: str, value: float, lower: float, upper: float) -> None:
    if not math.isfinite(value) or not lower <= value <= upper:
        raise ValueError(f"{name} must be finite and between {lower:g} and {upper:g}")


def _require_optional_finite_positive(name: str, value: float | None) -> None:
    if value is not None and (not math.isfinite(value) or value <= 0):
        raise ValueError(f"{name} must be finite and positive")


@dataclass
class ProgramConfig:
    """Validated settings and advisory context for one agent run."""

    mode: str = "sorry-elimination"
    lean_project_path: str = "workspace"
    model: str = DEFAULT_PROFILE
    backend: str | None = None
    endpoint: str | None = None
    effort: str | None = None
    temperature: float = 0.4
    max_output_tokens: int | None = None
    llm_timeout_seconds: float | None = None
    max_retries_per_sorry: int = 5
    cycle_timeout_seconds: int = 120
    max_cycles: int = 0
    max_proof_lines: int = DEFAULT_MAX_PROOF_LINES
    goals: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    strategy_hints: list[str] = field(default_factory=list)

    def validate(self) -> None:
        """Validate the complete program after parsing and overrides."""
        if self.mode != "sorry-elimination":
            raise ValueError(f"unsupported agent mode: {self.mode}")
        if not self.lean_project_path.strip():
            raise ValueError("lean_project_path must not be empty")
        validate_endpoint(self.endpoint)
        _require_positive("max_retries_per_sorry", self.max_retries_per_sorry)
        _require_positive("cycle_timeout_seconds", self.cycle_timeout_seconds)
        if self.max_cycles < 0:
            raise ValueError("max_cycles must be non-negative")
        _require_positive("max_proof_lines", self.max_proof_lines)
        _require_finite_range("temperature", self.temperature, 0, 2)
        if self.max_output_tokens is not None:
            _require_positive("max_output_tokens", self.max_output_tokens)
        _require_optional_finite_positive("llm_timeout_seconds", self.llm_timeout_seconds)
        if self.effort not in (None, "none", "low", "medium", "high", "xhigh", "max"):
            raise ValueError(f"unsupported reasoning effort: {self.effort}")

    def llm_config(self) -> LLMConfig:
        """Resolve the complete provider-neutral backend configuration."""
        self.validate()
        config = resolve_llm_config(
            self.model,
            backend=self.backend,
            base_url=self.endpoint,
            temperature=self.temperature,
            timeout=self.llm_timeout_seconds,
            max_output_tokens=self.max_output_tokens,
            effort=self.effort,
        )
        validate_backend_config(config)
        return config


# HTML comments document each setting and are outside the configuration syntax.
_HTML_COMMENT = re.compile(r"<!--.*?-->", re.DOTALL)
_SECTION_HEADING = re.compile(r"^##\s+(.+?)\s*$", re.MULTILINE)
_LIST_ITEM = re.compile(r"^\s*(?:[-*+]|\d+[.)])\s+(.+?)\s*$")


def _markdown_sections(content: str) -> dict[str, str]:
    """Return level-two Markdown sections keyed by exact heading."""
    matches = list(_SECTION_HEADING.finditer(content))
    return {
        match.group(1): content[
            match.end() : matches[index + 1].start() if index + 1 < len(matches) else len(content)
        ].strip()
        for index, match in enumerate(matches)
    }


def _section_value(sections: dict[str, str], heading: str) -> str | None:
    """Return the first non-empty line in a scalar program section."""
    for line in sections.get(heading, "").splitlines():
        value = line.strip()
        if value:
            return value
    return None


def _section_list(sections: dict[str, str], heading: str) -> list[str]:
    """Parse numbered or bulleted items from a program section."""
    items: list[str] = []
    for line in sections.get(heading, "").splitlines():
        match = _LIST_ITEM.match(line)
        if match:
            items.append(match.group(1))
    return items


def parse_program(path: Path) -> ProgramConfig:
    """Parse and validate one `program.md` file."""
    content = _HTML_COMMENT.sub("", path.read_text(encoding="utf-8"))
    sections = _markdown_sections(content)
    config = ProgramConfig()

    config.mode = _section_value(sections, "Mode") or config.mode
    config.lean_project_path = _section_value(sections, "Lean Project Path") or config.lean_project_path

    def extract_value(key: str, default: str | None) -> str | None:
        match = re.search(rf"^\s*{key}:\s*(\S+)", content, re.MULTILINE)
        return match.group(1) if match else default

    def extract_integer(key: str, default: int) -> int:
        raw = extract_value(key, None)
        if raw is None:
            return default
        try:
            return int(raw)
        except ValueError as error:
            raise ValueError(f"program.md: {key} must be an integer, got {raw!r}") from error

    def extract_float(key: str, default: float) -> float:
        raw = extract_value(key, None)
        if raw is None:
            return default
        try:
            return float(raw)
        except ValueError as error:
            raise ValueError(f"program.md: {key} must be numeric, got {raw!r}") from error

    config.model = extract_value("model", config.model) or config.model
    config.backend = extract_value("backend", None)
    config.endpoint = extract_value("endpoint", None)
    config.effort = extract_value("effort", None)
    config.temperature = extract_float("temperature", config.temperature)
    config.max_retries_per_sorry = extract_integer("max_retries_per_sorry", config.max_retries_per_sorry)
    config.cycle_timeout_seconds = extract_integer("cycle_timeout_seconds", config.cycle_timeout_seconds)
    config.max_cycles = extract_integer("max_cycles", config.max_cycles)
    config.max_proof_lines = extract_integer("max_proof_lines", config.max_proof_lines)

    for key in ("max_output_tokens", "num_predict"):
        if extract_value(key, None) is not None:
            config.max_output_tokens = extract_integer(key, 0)
            break

    for key in ("llm_timeout_seconds", "timeout"):
        if extract_value(key, None) is not None:
            config.llm_timeout_seconds = extract_float(key, 0.0)
            break

    config.goals = _section_list(sections, "Goals")
    config.constraints = _section_list(sections, "Constraints")
    config.strategy_hints = _section_list(sections, "Strategy Hints")
    config.validate()
    return config
