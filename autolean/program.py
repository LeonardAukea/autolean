"""Typed configuration for one AutoLean proof program."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path

from autolean.llm import (
    InferenceLocation,
    LLMConfig,
    inference_location,
    resolve_backend_name,
    validate_endpoint,
)
from autolean.models import DEFAULT_PROFILE, resolve_llm_config
from autolean.routing import DEFAULT_ESCALATION_AFTER, EscalationPolicy
from autolean.validation import (
    require_instance,
    require_int,
    require_optional_int,
    require_optional_number,
    require_optional_text,
    require_text,
    require_text_list,
    require_texts,
)

DEFAULT_MAX_PROOF_LINES = 30
DEFAULT_MAX_CYCLES = 5


class SearchScope(StrEnum):
    """Where advisory research queries may run."""

    AUTO = "auto"
    LOCAL = "local"
    REMOTE = "remote"


@dataclass
class ProgramConfig:
    """Validated settings and advisory context for one agent run."""

    mode: str = "sorry-elimination"
    lean_project_path: str = "workspace"
    model: str = DEFAULT_PROFILE
    backend: str | None = None
    endpoint: str | None = None
    effort: str | None = None
    temperature: float | None = None
    max_output_tokens: int | None = None
    llm_timeout_seconds: float | None = None
    search_scope: SearchScope = SearchScope.AUTO
    max_retries_per_sorry: int = 5
    cycle_timeout_seconds: int = 120
    max_cycles: int = DEFAULT_MAX_CYCLES
    max_proof_lines: int = DEFAULT_MAX_PROOF_LINES
    escalation_policy: EscalationPolicy = EscalationPolicy.ASK
    escalation_model: str | None = None
    escalation_after_failures: int = DEFAULT_ESCALATION_AFTER
    goals: list[str] = field(default_factory=list)
    constraints: list[str] = field(default_factory=list)
    strategy_hints: list[str] = field(default_factory=list)

    def validate(self) -> None:
        """Validate the complete program after parsing and overrides."""
        require_texts(
            (self.mode, self.lean_project_path, self.model),
            "program mode, project path, and model must be text",
            allow_empty=True,
        )
        if self.mode != "sorry-elimination":
            raise ValueError(f"unsupported agent mode: {self.mode}")
        require_text(self.lean_project_path, "lean_project_path must not be empty")
        require_text(self.model, "model must not be empty")
        for value in (self.backend, self.endpoint, self.effort):
            require_optional_text(
                value,
                "optional program settings must be text",
                allow_empty=True,
            )
        require_instance(
            self.search_scope,
            SearchScope,
            "search_scope must use the SearchScope vocabulary",
        )
        require_instance(
            self.escalation_policy,
            EscalationPolicy,
            "escalation_policy must use the EscalationPolicy vocabulary",
        )
        validate_endpoint(self.endpoint)
        require_int(
            self.max_retries_per_sorry,
            "max_retries_per_sorry must be positive",
            minimum=1,
        )
        require_int(
            self.cycle_timeout_seconds,
            "cycle_timeout_seconds must be positive",
            minimum=1,
        )
        require_int(self.max_cycles, "max_cycles must be non-negative", minimum=0)
        require_int(self.max_proof_lines, "max_proof_lines must be positive", minimum=1)
        require_int(
            self.escalation_after_failures,
            "escalation_after_failures must be positive",
            minimum=1,
        )
        require_optional_text(
            self.escalation_model,
            "escalation_model must not be empty",
        )
        require_optional_number(
            self.temperature,
            "temperature must be finite and between 0 and 2",
            minimum=0,
            maximum=2,
        )
        require_optional_int(
            self.max_output_tokens,
            "max_output_tokens must be positive",
            minimum=1,
        )
        require_optional_number(
            self.llm_timeout_seconds,
            "llm_timeout_seconds must be finite and positive",
            minimum=0,
            minimum_inclusive=False,
        )
        if self.effort not in (None, "none", "low", "medium", "high", "xhigh", "max"):
            raise ValueError(f"unsupported reasoning effort: {self.effort}")
        require_text_list(
            self.goals,
            "goals must contain unique non-empty text",
            unique=True,
        )
        require_text_list(
            self.constraints,
            "constraints must contain unique non-empty text",
            unique=True,
        )
        require_text_list(
            self.strategy_hints,
            "strategy_hints must contain unique non-empty text",
            unique=True,
        )

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
        return config

    def remote_search_enabled(self, config: LLMConfig) -> bool:
        """Resolve the effective network-search policy for one model."""
        if self.search_scope is SearchScope.REMOTE:
            return True
        if self.search_scope is SearchScope.LOCAL:
            return False
        return inference_location(config) is InferenceLocation.REMOTE


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


def _program_value(content: str, key: str, default: str | None = None) -> str | None:
    """Return one whitespace-delimited scalar from the configuration block."""
    match = re.search(rf"^\s*{key}:\s*(\S+)", content, re.MULTILINE)
    return match.group(1) if match else default


def _program_integer(content: str, key: str, default: int) -> int:
    """Decode one integer setting with its source name in failures."""
    raw = _program_value(content, key)
    if raw is None:
        return default
    try:
        return int(raw)
    except ValueError as error:
        raise ValueError(f"program.md: {key} must be an integer, got {raw!r}") from error


def _program_float(content: str, key: str, default: float | None = None) -> float | None:
    """Decode one optional numeric setting with its source name in failures."""
    raw = _program_value(content, key)
    if raw is None:
        return default
    try:
        return float(raw)
    except ValueError as error:
        raise ValueError(f"program.md: {key} must be numeric, got {raw!r}") from error


def _program_provider(content: str) -> str | None:
    """Resolve the public provider noun and its compatibility spelling."""
    provider = _program_value(content, "provider")
    backend = _program_value(content, "backend")
    if provider is not None and backend is not None:
        raise ValueError("program.md: choose one provider setting")
    selection = provider or backend
    if selection is None:
        return None
    resolved = resolve_backend_name(selection)
    if resolved is None:
        raise ValueError(f"program.md: unknown provider {selection!r}")
    return resolved


def _program_search_scope(content: str, default: SearchScope) -> SearchScope:
    """Decode the remote-research placement policy."""
    raw = _program_value(content, "search_scope", default.value) or default.value
    try:
        return SearchScope(raw)
    except ValueError as error:
        choices = ", ".join(scope.value for scope in SearchScope)
        raise ValueError(f"program.md: search_scope must be one of {choices}, got {raw!r}") from error


def _program_escalation_policy(
    content: str,
    default: EscalationPolicy,
) -> EscalationPolicy:
    """Decode the model-escalation policy."""
    raw = _program_value(content, "escalation_policy", default.value) or default.value
    try:
        return EscalationPolicy(raw)
    except ValueError as error:
        choices = ", ".join(policy.value for policy in EscalationPolicy)
        raise ValueError(f"program.md: escalation_policy must be one of {choices}, got {raw!r}") from error


def _program_integer_alias(content: str, keys: tuple[str, ...]) -> int | None:
    """Decode the first present spelling of one integer setting."""
    for key in keys:
        if _program_value(content, key) is not None:
            return _program_integer(content, key, 0)
    return None


def _program_float_alias(content: str, keys: tuple[str, ...]) -> float | None:
    """Decode the first present spelling of one numeric setting."""
    for key in keys:
        if _program_value(content, key) is not None:
            return _program_float(content, key)
    return None


def parse_program(path: Path) -> ProgramConfig:
    """Parse and validate one `program.md` file."""
    content = _HTML_COMMENT.sub("", path.read_text(encoding="utf-8"))
    sections = _markdown_sections(content)
    defaults = ProgramConfig()
    config = ProgramConfig(
        mode=_section_value(sections, "Mode") or defaults.mode,
        lean_project_path=(_section_value(sections, "Lean Project Path") or defaults.lean_project_path),
        model=_program_value(content, "model", defaults.model) or defaults.model,
        backend=_program_provider(content),
        endpoint=_program_value(content, "endpoint"),
        effort=_program_value(content, "effort"),
        temperature=_program_float(content, "temperature"),
        max_output_tokens=_program_integer_alias(
            content,
            ("max_output_tokens", "num_predict"),
        ),
        llm_timeout_seconds=_program_float_alias(
            content,
            ("llm_timeout_seconds", "timeout"),
        ),
        search_scope=_program_search_scope(content, defaults.search_scope),
        max_retries_per_sorry=_program_integer(
            content,
            "max_retries_per_sorry",
            defaults.max_retries_per_sorry,
        ),
        cycle_timeout_seconds=_program_integer(
            content,
            "cycle_timeout_seconds",
            defaults.cycle_timeout_seconds,
        ),
        max_cycles=_program_integer(content, "max_cycles", defaults.max_cycles),
        max_proof_lines=_program_integer(
            content,
            "max_proof_lines",
            defaults.max_proof_lines,
        ),
        escalation_policy=_program_escalation_policy(
            content,
            defaults.escalation_policy,
        ),
        escalation_model=_program_value(content, "escalation_model"),
        escalation_after_failures=_program_integer(
            content,
            "escalation_after_failures",
            defaults.escalation_after_failures,
        ),
        goals=_section_list(sections, "Goals"),
        constraints=_section_list(sections, "Constraints"),
        strategy_hints=_section_list(sections, "Strategy Hints"),
    )

    config.validate()
    return config
