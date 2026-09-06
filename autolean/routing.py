"""Bounded model routing for proof attempts."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from enum import StrEnum
from typing import TYPE_CHECKING, Any

from autolean.llm import LLMBackend
from autolean.models import PROFILES, ModelProfile, resolve_llm_config, resolve_profile
from autolean.tracker import Outcome
from autolean.validation import require_optional_instance

if TYPE_CHECKING:
    from autolean.llm import LLMConfig

DEFAULT_ESCALATION_AFTER = 2
RESEARCH_DIFFICULTY = 8

#: A timeout counts: the budget was spent elaborating the proof this model
#: chose, and a stronger one can choose a cheaper route to the same goal.
_ELIGIBLE_OUTCOMES = frozenset({Outcome.FAIL_BUILD, Outcome.FAIL_SORRY_REMAINS, Outcome.FAIL_TIMEOUT})
_INELIGIBLE_CATEGORIES = frozenset(
    {
        "duplicate_declaration",
        "file_structure_error",
        "lake_config_error",
        "llm_authentication",
        "llm_error",
        "llm_rate_limit",
        "llm_transient",
    }
)


class EscalationPolicy(StrEnum):
    """Authority for switching to a stronger model."""

    NEVER = "never"
    ASK = "ask"
    AUTO = "auto"


@dataclass(frozen=True)
class FailureEvidence:
    """One kernel-facing failure considered by the routing policy."""

    outcome: Outcome
    category: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.outcome, Outcome):
            raise ValueError("failure evidence must use the Outcome vocabulary")
        if not isinstance(self.category, str):
            raise ValueError("failure category must be text")

    @property
    def eligible(self) -> bool:
        """Return whether model capability can plausibly change the result."""
        return self.outcome in _ELIGIBLE_OUTCOMES and self.category not in _INELIGIBLE_CATEGORIES


@dataclass(frozen=True)
class EscalationDecision:
    """A deterministic recommendation produced from recorded failures."""

    from_model: str
    from_backend: str
    to_profile: str
    to_model: str
    to_backend: str
    failure_count: int
    categories: tuple[str, ...]
    difficulty: int
    reason: str

    def __post_init__(self) -> None:
        names = (
            self.from_model,
            self.from_backend,
            self.to_profile,
            self.to_model,
            self.to_backend,
            self.reason,
        )
        if any(not isinstance(name, str) or not name.strip() for name in names):
            raise ValueError("escalation decision identity must be complete")
        if (
            isinstance(self.failure_count, bool)
            or not isinstance(self.failure_count, int)
            or self.failure_count <= 0
        ):
            raise ValueError("escalation failure count must be positive")
        if isinstance(self.difficulty, bool) or not isinstance(self.difficulty, int) or self.difficulty < 0:
            raise ValueError("escalation difficulty must be non-negative")
        if (
            not isinstance(self.categories, tuple)
            or not self.categories
            or any(not isinstance(category, str) or not category.strip() for category in self.categories)
        ):
            raise ValueError("escalation decision must name its failure categories")


@dataclass(frozen=True)
class ModelTransition:
    """One authorized model switch retained by a proof session."""

    timestamp: str
    from_model: str
    from_backend: str
    to_model: str
    to_backend: str
    reason: str
    failure_count: int

    def __post_init__(self) -> None:
        names = (
            self.from_model,
            self.from_backend,
            self.to_model,
            self.to_backend,
            self.reason,
        )
        if any(not isinstance(name, str) or not name.strip() for name in names):
            raise ValueError("model transition identity must be complete")
        if (
            isinstance(self.failure_count, bool)
            or not isinstance(self.failure_count, int)
            or self.failure_count <= 0
        ):
            raise ValueError("model transition failure count must be positive")
        if not isinstance(self.timestamp, str):
            raise ValueError("model transition timestamp must be text")
        try:
            timestamp = datetime.fromisoformat(self.timestamp.replace("Z", "+00:00"))
        except ValueError as error:
            raise ValueError("model transition timestamp must be ISO 8601") from error
        if timestamp.tzinfo is None:
            raise ValueError("model transition timestamp must include a UTC offset")

    @classmethod
    def from_decision(cls, decision: EscalationDecision) -> ModelTransition:
        """Record an accepted routing decision at the current UTC time."""
        return cls(
            timestamp=datetime.now(UTC).isoformat().replace("+00:00", "Z"),
            from_model=decision.from_model,
            from_backend=decision.from_backend,
            to_model=decision.to_model,
            to_backend=decision.to_backend,
            reason=decision.reason,
            failure_count=decision.failure_count,
        )

    def as_dict(self) -> dict[str, str | int]:
        """Return the canonical JSON-compatible record."""
        return asdict(self)

    @classmethod
    def from_dict(cls, value: Any) -> ModelTransition:
        """Validate and decode one persisted transition."""
        if not isinstance(value, dict):
            raise ValueError("model transition must be an object")
        try:
            string_fields = (
                "timestamp",
                "from_model",
                "from_backend",
                "to_model",
                "to_backend",
                "reason",
            )
            if any(not isinstance(value.get(name), str) for name in string_fields):
                raise TypeError("transition text fields must be strings")
            failure_count = value["failure_count"]
            if isinstance(failure_count, bool) or not isinstance(failure_count, int):
                raise TypeError("failure_count must be an integer")
            return cls(
                timestamp=value["timestamp"],
                from_model=value["from_model"],
                from_backend=value["from_backend"],
                to_model=value["to_model"],
                to_backend=value["to_backend"],
                reason=value["reason"],
                failure_count=failure_count,
            )
        except (KeyError, TypeError, ValueError) as error:
            raise ValueError(f"malformed model transition: {error}") from error


@dataclass(frozen=True)
class EscalationRoute:
    """One routing observation and its optional connected backend."""

    decision: EscalationDecision | None = None
    backend: LLMBackend | None = None
    transition: ModelTransition | None = None
    notice: str = ""

    def __post_init__(self) -> None:
        if not isinstance(self.notice, str):
            raise ValueError("routing notice must be text")
        require_optional_instance(
            self.decision,
            EscalationDecision,
            "routing decision must use EscalationDecision",
        )
        require_optional_instance(
            self.backend,
            LLMBackend,
            "routing backend must implement LLMBackend",
        )
        require_optional_instance(
            self.transition,
            ModelTransition,
            "routing transition must use ModelTransition",
        )
        if (self.backend is None) != (self.transition is None):
            raise ValueError("a connected route requires its model transition")
        if self.transition is None:
            return
        if self.decision is None:
            raise ValueError("a connected route requires its routing decision")
        transition_identity = (
            self.transition.from_model,
            self.transition.from_backend,
            self.transition.to_model,
            self.transition.to_backend,
            self.transition.failure_count,
        )
        decision_identity = (
            self.decision.from_model,
            self.decision.from_backend,
            self.decision.to_model,
            self.decision.to_backend,
            self.decision.failure_count,
        )
        if transition_identity != decision_identity:
            raise ValueError("route transition must record its routing decision")


class EscalationRouter:
    """Own one bounded, evidence-backed model switch per invocation."""

    def __init__(
        self,
        confirm: Callable[[EscalationDecision], bool] | None = None,
    ) -> None:
        self._confirm = confirm
        self._failures: dict[str, list[FailureEvidence]] = {}
        self._offered = False
        self._transitions: list[ModelTransition] = []

    @property
    def transitions(self) -> tuple[ModelTransition, ...]:
        """Return every authorized switch made by this router."""
        return tuple(self._transitions)

    def route(
        self,
        *,
        target_id: str,
        outcome: Outcome,
        category: str,
        policy: EscalationPolicy,
        current_model: str,
        current_backend: str,
        difficulty: int,
        after_failures: int,
        explicit_target: str | None,
        endpoint: str | None,
        timeout: float | None,
        max_output_tokens: int | None,
        effort: str | None,
        create_backend: Callable[[LLMConfig], LLMBackend],
    ) -> EscalationRoute:
        """Observe one failure and connect an authorized stronger sibling."""
        decision, notice = self._observe(
            target_id=target_id,
            outcome=outcome,
            category=category,
            policy=policy,
            current_model=current_model,
            current_backend=current_backend,
            difficulty=difficulty,
            after_failures=after_failures,
            explicit_target=explicit_target,
        )
        if decision is None:
            return EscalationRoute(notice=notice)
        if not self._approved(policy, decision):
            return EscalationRoute(
                decision=decision,
                notice=f"Continue with `--model {decision.to_profile}` to accept this route.",
            )
        try:
            backend = connect_escalated_backend(
                decision,
                endpoint=endpoint,
                timeout=timeout,
                max_output_tokens=max_output_tokens,
                effort=effort,
                create_backend=create_backend,
            )
        except (OSError, ValueError) as error:
            return EscalationRoute(
                decision=decision,
                notice=f"Escalation unavailable: {error}",
            )
        transition = ModelTransition.from_decision(decision)
        self._transitions.append(transition)
        return EscalationRoute(
            decision=decision,
            backend=backend,
            transition=transition,
        )

    def _observe(
        self,
        *,
        target_id: str,
        outcome: Outcome,
        category: str,
        policy: EscalationPolicy,
        current_model: str,
        current_backend: str,
        difficulty: int,
        after_failures: int,
        explicit_target: str | None,
    ) -> tuple[EscalationDecision | None, str]:
        if self._transitions or self._offered:
            return None, ""
        evidence = FailureEvidence(outcome, category)
        if not evidence.eligible:
            return None, ""
        failures = self._failures.setdefault(target_id, [])
        failures.append(evidence)
        try:
            decision = decide_escalation(
                policy=policy,
                current_model=current_model,
                current_backend=current_backend,
                failures=failures,
                difficulty=difficulty,
                after_failures=after_failures,
                explicit_target=explicit_target,
            )
        except ValueError as error:
            self._offered = True
            return None, f"Model escalation is not configured: {error}"
        if decision is not None:
            self._offered = True
        return decision, ""

    def _approved(
        self,
        policy: EscalationPolicy,
        decision: EscalationDecision,
    ) -> bool:
        if policy is EscalationPolicy.AUTO:
            return True
        return bool(policy is EscalationPolicy.ASK and self._confirm is not None and self._confirm(decision))


def connect_escalated_backend(
    decision: EscalationDecision,
    *,
    endpoint: str | None,
    timeout: float | None,
    max_output_tokens: int | None,
    effort: str | None,
    create_backend: Callable[[LLMConfig], LLMBackend],
) -> LLMBackend:
    """Connect and preflight the exact backend named by one decision."""
    from autolean.llm import LLMError

    candidate: LLMBackend | None = None
    try:
        config = resolve_llm_config(
            decision.to_profile,
            base_url=endpoint,
            timeout=timeout,
            max_output_tokens=max_output_tokens,
            effort=effort,
        )
        candidate = create_backend(config)
        if not candidate.ping():
            raise LLMError(f"backend {config.backend!r} did not pass preflight for {config.model!r}")
    except (LLMError, OSError, ValueError) as error:
        if candidate is not None:
            candidate.close()
        raise ValueError(str(error)) from error
    return candidate


def profile_for_model(model: str, backend: str) -> ModelProfile | None:
    """Resolve the canonical profile for one active model and backend."""
    named = resolve_profile(model)
    if named is not None and named.backend == backend:
        return named
    return next(
        (profile for profile in PROFILES.values() if profile.model == model and profile.backend == backend),
        None,
    )


def decide_escalation(
    *,
    policy: EscalationPolicy,
    current_model: str,
    current_backend: str,
    failures: Sequence[FailureEvidence],
    difficulty: int,
    after_failures: int = DEFAULT_ESCALATION_AFTER,
    explicit_target: str | None = None,
) -> EscalationDecision | None:
    """Return one bounded routing recommendation when evidence warrants it."""
    if policy is EscalationPolicy.NEVER:
        return None
    if after_failures <= 0:
        raise ValueError("escalation_after_failures must be positive")

    eligible = tuple(item for item in failures if item.eligible)
    threshold = 1 if difficulty >= RESEARCH_DIFFICULTY else after_failures
    if len(eligible) < threshold:
        return None

    current_profile = profile_for_model(current_model, current_backend)
    target_name = explicit_target or (current_profile.escalates_to if current_profile is not None else None)
    if target_name is None:
        return None
    target_config = resolve_llm_config(target_name)
    if target_config.model == current_model and target_config.backend == current_backend:
        raise ValueError("escalation target must select a different model")
    if explicit_target is None and target_config.backend != current_backend:
        raise ValueError("profile escalation routes must stay on the current backend")

    categories = tuple(dict.fromkeys(item.category or "unclassified" for item in eligible))
    scope = (
        f"difficulty {difficulty} target"
        if difficulty >= RESEARCH_DIFFICULTY
        else f"{len(eligible)} kernel-rejected attempts"
    )
    return EscalationDecision(
        from_model=current_model,
        from_backend=current_backend,
        to_profile=target_name,
        to_model=target_config.model,
        to_backend=target_config.backend,
        failure_count=len(eligible),
        categories=categories,
        difficulty=difficulty,
        reason=f"{scope}; categories: {', '.join(categories)}",
    )
