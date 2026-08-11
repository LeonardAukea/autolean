"""Persistent proof sessions for resumable AutoLean workflows."""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any, cast

from autolean.routing import (
    DEFAULT_ESCALATION_AFTER,
    EscalationPolicy,
    ModelTransition,
)

SESSION_SCHEMA = "autolean.proof-session.v1"
_SESSION_ID = re.compile(r"[a-z0-9][a-z0-9-]{7,79}")
_SLUG_PART = re.compile(r"[^a-z0-9]+")


class SessionError(ValueError):
    """A persisted proof session is malformed or cannot be resolved."""


class SessionKind(StrEnum):
    """The workflow that owns a proof session."""

    PROJECT = "project"
    THEOREM = "theorem"
    PROBLEM = "problem"
    PAPER = "paper"


class SessionStatus(StrEnum):
    """The durable state of a proof session."""

    READY = "ready"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"


def _utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _slug(text: str) -> str:
    value = _SLUG_PART.sub("-", text.casefold()).strip("-")[:36]
    return value or "proof"


@dataclass(frozen=True)
class ProofSession:
    """A portable description of one resumable proof workflow."""

    id: str
    kind: SessionKind
    title: str
    status: SessionStatus
    created_at: str
    updated_at: str
    model: str
    backend: str
    max_cycles: int
    escalation_policy: EscalationPolicy = EscalationPolicy.ASK
    escalation_model: str = ""
    escalation_after_failures: int = DEFAULT_ESCALATION_AFTER
    target_file: str = ""
    target_filter: str = ""
    guidance: tuple[str, ...] = ()
    model_transitions: tuple[ModelTransition, ...] = ()
    remaining_targets: int | None = None
    message: str = ""
    schema: str = SESSION_SCHEMA

    def __post_init__(self) -> None:
        if self.schema != SESSION_SCHEMA:
            raise SessionError(f"unsupported proof session schema: {self.schema}")
        if _SESSION_ID.fullmatch(self.id) is None:
            raise SessionError(f"invalid proof session ID: {self.id}")
        if not self.title.strip():
            raise SessionError("proof session title must not be empty")
        if self.max_cycles < 0:
            raise SessionError("proof session cycle budget must be non-negative")
        if self.escalation_after_failures <= 0:
            raise SessionError("proof session escalation threshold must be positive")
        if self.remaining_targets is not None and self.remaining_targets < 0:
            raise SessionError("remaining target count must be non-negative")

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-compatible record."""
        record = asdict(self)
        record["kind"] = self.kind.value
        record["status"] = self.status.value
        record["escalation_policy"] = self.escalation_policy.value
        record["guidance"] = list(self.guidance)
        record["model_transitions"] = [item.as_dict() for item in self.model_transitions]
        return record

    @classmethod
    def from_dict(cls, record: dict[str, Any]) -> ProofSession:
        """Validate and decode one persisted record."""
        try:
            return cls(
                id=str(record["id"]),
                kind=SessionKind(str(record["kind"])),
                title=str(record["title"]),
                status=SessionStatus(str(record["status"])),
                created_at=str(record["created_at"]),
                updated_at=str(record["updated_at"]),
                model=str(record["model"]),
                backend=str(record["backend"]),
                max_cycles=int(record["max_cycles"]),
                escalation_policy=EscalationPolicy(
                    str(record.get("escalation_policy", EscalationPolicy.ASK.value))
                ),
                escalation_model=str(record.get("escalation_model", "")),
                escalation_after_failures=int(
                    record.get("escalation_after_failures", DEFAULT_ESCALATION_AFTER)
                ),
                target_file=str(record.get("target_file", "")),
                target_filter=str(record.get("target_filter", "")),
                guidance=tuple(str(item) for item in record.get("guidance", [])),
                model_transitions=tuple(
                    ModelTransition.from_dict(item) for item in record.get("model_transitions", [])
                ),
                remaining_targets=(
                    None if record.get("remaining_targets") is None else int(record["remaining_targets"])
                ),
                message=str(record.get("message", "")),
                schema=str(record.get("schema", "")),
            )
        except (KeyError, TypeError, ValueError) as error:
            raise SessionError(f"malformed proof session: {error}") from error

    def update(self, **changes: object) -> ProofSession:
        """Return an updated session with a fresh modification time."""
        return replace(self, updated_at=_utc_now(), **cast("Any", changes))


class SessionStore:
    """Atomic local storage rooted in one Lean project."""

    def __init__(self, project_root: Path) -> None:
        self.project_root = project_root.resolve()
        self.directory = self.project_root / ".autolean" / "sessions"

    def _path(self, session_id: str) -> Path:
        if _SESSION_ID.fullmatch(session_id) is None:
            raise SessionError(f"invalid proof session ID: {session_id}")
        return self.directory / f"{session_id}.json"

    def relative_target(self, target_file: Path | None) -> str:
        """Encode a target path inside the project root."""
        if target_file is None:
            return ""
        try:
            return target_file.resolve().relative_to(self.project_root).as_posix()
        except ValueError as error:
            raise SessionError("proof session target must be inside the Lean project") from error

    def target_path(self, session: ProofSession) -> Path | None:
        """Resolve and validate a session target path."""
        if not session.target_file:
            return None
        candidate = (self.project_root / session.target_file).resolve()
        try:
            candidate.relative_to(self.project_root)
        except ValueError as error:
            raise SessionError("proof session target escapes the Lean project") from error
        return candidate

    def create(
        self,
        *,
        kind: SessionKind,
        title: str,
        model: str,
        backend: str,
        max_cycles: int,
        escalation_policy: EscalationPolicy = EscalationPolicy.ASK,
        escalation_model: str = "",
        escalation_after_failures: int = DEFAULT_ESCALATION_AFTER,
        target_file: Path | None = None,
        target_filter: str = "",
        guidance: tuple[str, ...] = (),
        session_id: str | None = None,
    ) -> ProofSession:
        """Create and persist one ready proof session."""
        now = _utc_now()
        if session_id is None:
            stamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S")
            session_id = f"{stamp}-{_slug(title)}-{uuid.uuid4().hex[:8]}"
        session = ProofSession(
            id=session_id,
            kind=kind,
            title=title.strip(),
            status=SessionStatus.READY,
            created_at=now,
            updated_at=now,
            model=model,
            backend=backend,
            max_cycles=max_cycles,
            escalation_policy=escalation_policy,
            escalation_model=escalation_model,
            escalation_after_failures=escalation_after_failures,
            target_file=self.relative_target(target_file),
            target_filter=target_filter,
            guidance=guidance,
        )
        if self._path(session.id).exists():
            raise SessionError(f"proof session already exists: {session.id}")
        return self.save(session)

    def save(self, session: ProofSession) -> ProofSession:
        """Atomically persist one canonical session record."""
        self.directory.mkdir(parents=True, exist_ok=True)
        destination = self._path(session.id)
        temporary = self.directory / f".{session.id}.{uuid.uuid4().hex}.tmp"
        payload = json.dumps(session.as_dict(), indent=2, sort_keys=True) + "\n"
        try:
            with temporary.open("x", encoding="utf-8") as handle:
                handle.write(payload)
                handle.flush()
                os.fsync(handle.fileno())
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
        return session

    def load(self, session_id: str) -> ProofSession:
        """Load one session by exact ID."""
        path = self._path(session_id)
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except FileNotFoundError as error:
            raise SessionError(f"proof session was not found: {session_id}") from error
        except json.JSONDecodeError as error:
            raise SessionError(f"proof session is not valid JSON: {session_id}") from error
        if not isinstance(record, dict):
            raise SessionError(f"proof session record must be an object: {session_id}")
        session = ProofSession.from_dict(record)
        if session.id != session_id:
            raise SessionError(f"proof session ID does not match its filename: {session_id}")
        self.target_path(session)
        return session

    def list(self) -> list[ProofSession]:
        """Return sessions ordered from most recently updated."""
        if not self.directory.exists():
            return []
        sessions = [self.load(path.stem) for path in self.directory.glob("*.json")]
        return sorted(sessions, key=lambda item: (item.updated_at, item.id), reverse=True)

    def latest(self, *, include_completed: bool = False) -> ProofSession:
        """Return the latest resumable session."""
        sessions = self.list()
        if not include_completed:
            sessions = [item for item in sessions if item.status is not SessionStatus.COMPLETED]
        if not sessions:
            raise SessionError("no resumable proof sessions were found")
        return sessions[0]

    def find_target(self, target_file: Path) -> ProofSession | None:
        """Return the newest open session for an exact target file."""
        relative = self.relative_target(target_file)
        return next(
            (
                item
                for item in self.list()
                if item.target_file == relative and item.status is not SessionStatus.COMPLETED
            ),
            None,
        )

    def find_workflow(
        self,
        kind: SessionKind,
        *,
        target_file: Path | None = None,
        target_filter: str = "",
    ) -> ProofSession | None:
        """Return the newest open session with the same execution scope."""
        relative = self.relative_target(target_file)
        return next(
            (
                item
                for item in self.list()
                if item.kind is kind
                and item.target_file == relative
                and item.target_filter == target_filter
                and item.status is not SessionStatus.COMPLETED
            ),
            None,
        )
