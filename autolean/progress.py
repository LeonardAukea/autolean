"""Bounded observations of proof attempts and accepted learning."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from enum import StrEnum

from autolean.validation import require_int

PROGRESS_PREFIX = "AUTOLEAN_EVENT "
PROGRESS_SCHEMA = "autolean.progress.v1"
MAX_DETAIL = 8192
MAX_EVENT_BYTES = 65536


class ProgressKind(StrEnum):
    """Observable stages of a proof run."""

    PHASE = "phase"
    TARGET = "target"
    GOAL = "goal"
    CANDIDATE = "candidate"
    FEEDBACK = "feedback"
    LEARNING = "learning"
    SUMMARY = "summary"
    FINISHED = "finished"


def excerpt(value: str, limit: int = MAX_DETAIL) -> str:
    """Bound presentation text while marking omitted content."""
    return value if len(value) <= limit else value[: limit - 1] + "…"


@dataclass(frozen=True)
class ProgressEvent:
    """One public observation; proof authority remains in Lean records."""

    kind: ProgressKind
    message: str
    detail: str = ""
    target: str = ""
    cycle: int = 0
    attempt: int = 0
    timestamp: str = field(default_factory=lambda: datetime.now(UTC).isoformat())

    def __post_init__(self) -> None:
        if not isinstance(self.kind, ProgressKind):
            raise ValueError("progress kind must identify a known stage")
        for name, limit in (("message", 512), ("detail", MAX_DETAIL), ("target", 512), ("timestamp", 64)):
            value = getattr(self, name)
            if not isinstance(value, str) or len(value) > limit:
                raise ValueError(f"progress {name} exceeds its text bound")
        if not self.message.strip():
            raise ValueError("progress message must be non-empty")
        require_int(self.cycle, "progress cycle must be non-negative", minimum=0)
        require_int(self.attempt, "progress attempt must be non-negative", minimum=0)
        instant = datetime.fromisoformat(self.timestamp)
        if instant.tzinfo is None or instant.utcoffset() is None:
            raise ValueError("progress timestamp must include a timezone")

    def to_json(self) -> str:
        """Encode a single UTF-8 journal record."""
        return json.dumps({"schema": PROGRESS_SCHEMA, **asdict(self)}, ensure_ascii=False)

    @property
    def time_label(self) -> str:
        """Display an unambiguous UTC time in terminals and transcripts."""
        return datetime.fromisoformat(self.timestamp).astimezone(UTC).strftime("%H:%M:%SZ")

    @classmethod
    def from_line(cls, line: str) -> ProgressEvent | None:
        """Decode a framed observation; ordinary terminal lines pass through."""
        if not line.startswith(PROGRESS_PREFIX):
            return None
        if len(line.encode("utf-8")) > MAX_EVENT_BYTES:
            raise ValueError("progress event exceeds its byte bound")
        try:
            data = json.loads(line[len(PROGRESS_PREFIX) :])
            if not isinstance(data, dict) or data.pop("schema", None) != PROGRESS_SCHEMA:
                raise ValueError("unsupported progress schema")
            data["kind"] = ProgressKind(data["kind"])
            return cls(**data)
        except (KeyError, TypeError, ValueError, RecursionError) as error:
            raise ValueError(f"invalid progress event: {error}") from error
