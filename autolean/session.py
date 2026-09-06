"""Persistent proof sessions for resumable AutoLean workflows."""

from __future__ import annotations

import json
import os
import re
import uuid
from dataclasses import asdict, dataclass, replace
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any, cast

from autolean.routing import (
    DEFAULT_ESCALATION_AFTER,
    EscalationPolicy,
    ModelTransition,
)
from autolean.validation import (
    require_aware_datetime,
    require_instance,
    require_int,
    require_optional_int,
    require_text,
    require_texts,
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
    effort: str | None = None
    escalation_policy: EscalationPolicy = EscalationPolicy.ASK
    escalation_model: str = ""
    escalation_after_failures: int = DEFAULT_ESCALATION_AFTER
    target_file: str = ""
    target_filter: str = ""
    artifacts: tuple[str, ...] = ()
    guidance: tuple[str, ...] = ()
    model_transitions: tuple[ModelTransition, ...] = ()
    remaining_targets: int | None = None
    message: str = ""
    schema: str = SESSION_SCHEMA

    def __post_init__(self) -> None:
        require_texts(
            (
                self.id,
                self.title,
                self.created_at,
                self.updated_at,
                self.model,
                self.backend,
                self.escalation_model,
                self.target_file,
                self.target_filter,
                self.message,
                self.schema,
            ),
            "proof session text fields must be strings",
            allow_empty=True,
            error_type=SessionError,
        )
        if self.schema != SESSION_SCHEMA:
            raise SessionError(f"unsupported proof session schema: {self.schema}")
        if _SESSION_ID.fullmatch(self.id) is None:
            raise SessionError(f"invalid proof session ID: {self.id}")
        require_text(
            self.title,
            "proof session title must not be empty",
            error_type=SessionError,
        )
        require_instance(
            self.kind,
            SessionKind,
            "proof session kind and status must use their vocabularies",
            error_type=SessionError,
        )
        require_instance(
            self.status,
            SessionStatus,
            "proof session kind and status must use their vocabularies",
            error_type=SessionError,
        )
        require_instance(
            self.escalation_policy,
            EscalationPolicy,
            "proof session escalation policy is invalid",
            error_type=SessionError,
        )
        require_texts(
            (self.model, self.backend),
            "proof session model and backend must not be empty",
            error_type=SessionError,
        )
        require_int(
            self.max_cycles,
            "proof session cycle budget must be non-negative",
            minimum=0,
            error_type=SessionError,
        )
        if self.effort not in (None, "none", "low", "medium", "high", "xhigh", "max"):
            raise SessionError("proof session reasoning effort is invalid")
        require_int(
            self.escalation_after_failures,
            "proof session escalation threshold must be positive",
            minimum=1,
            error_type=SessionError,
        )
        require_optional_int(
            self.remaining_targets,
            "remaining target count must be non-negative",
            minimum=0,
            error_type=SessionError,
        )
        created = require_aware_datetime(
            self.created_at,
            "proof session timestamps must be ISO 8601 with a UTC offset",
            error_type=SessionError,
        )
        updated = require_aware_datetime(
            self.updated_at,
            "proof session timestamps must be ISO 8601 with a UTC offset",
            error_type=SessionError,
        )
        if updated < created:
            raise SessionError("proof session update precedes its creation")
        require_instance(
            self.artifacts,
            tuple,
            "proof session artifacts and guidance must be tuples",
            error_type=SessionError,
        )
        require_instance(
            self.guidance,
            tuple,
            "proof session artifacts and guidance must be tuples",
            error_type=SessionError,
        )
        require_instance(
            self.model_transitions,
            tuple,
            "proof session model transitions must be a tuple",
            error_type=SessionError,
        )
        require_texts(
            self.artifacts,
            "proof session artifacts must contain unique text",
            allow_empty=True,
            unique=True,
            error_type=SessionError,
        )
        paths = (self.target_file, *self.artifacts)
        if any(
            path and (PurePosixPath(path).is_absolute() or ".." in PurePosixPath(path).parts)
            for path in paths
        ):
            raise SessionError("proof session paths must be project-relative")
        require_texts(
            self.guidance,
            "proof session guidance must be text",
            allow_empty=True,
            error_type=SessionError,
        )
        if not all(isinstance(item, ModelTransition) for item in self.model_transitions):
            raise SessionError("proof session model transitions are invalid")

    def as_dict(self) -> dict[str, Any]:
        """Return the canonical JSON-compatible record."""
        record = asdict(self)
        record["kind"] = self.kind.value
        record["status"] = self.status.value
        record["escalation_policy"] = self.escalation_policy.value
        record["artifacts"] = list(self.artifacts)
        record["guidance"] = list(self.guidance)
        record["model_transitions"] = [item.as_dict() for item in self.model_transitions]
        return record

    @classmethod
    def from_dict(cls, record: dict[str, Any]) -> ProofSession:
        """Validate and decode one persisted record."""
        try:
            string_fields = (
                "id",
                "kind",
                "title",
                "status",
                "created_at",
                "updated_at",
                "model",
                "backend",
            )
            if any(not isinstance(record.get(name), str) for name in string_fields):
                raise TypeError("required session text fields must be strings")
            optional_strings = (
                "escalation_model",
                "target_file",
                "target_filter",
                "message",
                "schema",
            )
            if any(name in record and not isinstance(record[name], str) for name in optional_strings):
                raise TypeError("optional session text fields must be strings")
            max_cycles = record["max_cycles"]
            if isinstance(max_cycles, bool) or not isinstance(max_cycles, int):
                raise TypeError("max_cycles must be an integer")
            artifacts = record.get("artifacts", [])
            guidance = record.get("guidance", [])
            transitions = record.get("model_transitions", [])
            if not isinstance(artifacts, list) or not all(isinstance(item, str) for item in artifacts):
                raise TypeError("artifacts must be a list of strings")
            if not isinstance(guidance, list) or not all(isinstance(item, str) for item in guidance):
                raise TypeError("guidance must be a list of strings")
            if not isinstance(transitions, list):
                raise TypeError("model_transitions must be a list")
            threshold = record.get(
                "escalation_after_failures",
                DEFAULT_ESCALATION_AFTER,
            )
            if isinstance(threshold, bool) or not isinstance(threshold, int):
                raise TypeError("escalation_after_failures must be an integer")
            remaining = record.get("remaining_targets")
            if remaining is not None and (isinstance(remaining, bool) or not isinstance(remaining, int)):
                raise TypeError("remaining_targets must be an integer or null")
            return cls(
                id=record["id"],
                kind=SessionKind(record["kind"]),
                title=record["title"],
                status=SessionStatus(record["status"]),
                created_at=record["created_at"],
                updated_at=record["updated_at"],
                model=record["model"],
                backend=record["backend"],
                max_cycles=max_cycles,
                effort=record.get("effort"),
                escalation_policy=EscalationPolicy(
                    record.get("escalation_policy", EscalationPolicy.ASK.value)
                ),
                escalation_model=record.get("escalation_model", ""),
                escalation_after_failures=threshold,
                target_file=record.get("target_file", ""),
                target_filter=record.get("target_filter", ""),
                artifacts=tuple(artifacts),
                guidance=tuple(guidance),
                model_transitions=tuple(ModelTransition.from_dict(item) for item in transitions),
                remaining_targets=remaining,
                message=record.get("message", ""),
                schema=record.get("schema", ""),
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

    def artifact_paths(self, session: ProofSession) -> tuple[Path, ...]:
        """Resolve and validate the source artifacts owned by a session."""
        paths: list[Path] = []
        for relative in session.artifacts:
            candidate = (self.project_root / relative).resolve()
            try:
                candidate.relative_to(self.project_root)
            except ValueError as error:
                raise SessionError("proof session artifact escapes the Lean project") from error
            if not candidate.is_file():
                raise SessionError(f"proof session artifact does not exist: {relative}")
            paths.append(candidate)
        return tuple(paths)

    def relative_artifacts(self, artifacts: tuple[Path, ...]) -> tuple[str, ...]:
        """Encode source artifact paths inside the project root."""
        relative: list[str] = []
        for artifact in artifacts:
            if not artifact.is_file():
                raise SessionError(f"proof session artifact does not exist: {artifact}")
            try:
                path = artifact.resolve().relative_to(self.project_root).as_posix()
            except ValueError as error:
                raise SessionError("proof session artifact must be inside the Lean project") from error
            if path not in relative:
                relative.append(path)
        return tuple(relative)

    def create(
        self,
        *,
        kind: SessionKind,
        title: str,
        model: str,
        backend: str,
        max_cycles: int,
        effort: str | None = None,
        escalation_policy: EscalationPolicy = EscalationPolicy.ASK,
        escalation_model: str = "",
        escalation_after_failures: int = DEFAULT_ESCALATION_AFTER,
        target_file: Path | None = None,
        target_filter: str = "",
        artifacts: tuple[Path, ...] = (),
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
            effort=effort,
            escalation_policy=escalation_policy,
            escalation_model=escalation_model,
            escalation_after_failures=escalation_after_failures,
            target_file=self.relative_target(target_file),
            target_filter=target_filter,
            artifacts=self.relative_artifacts(artifacts),
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
        self.artifact_paths(session)
        return session

    def list(self) -> list[ProofSession]:
        """Return sessions ordered from most recently updated."""
        if not self.directory.exists():
            return []
        sessions = [self.load(path.stem) for path in self.directory.glob("*.json")]
        return sorted(
            sessions,
            key=lambda item: (datetime.fromisoformat(item.updated_at), item.id),
            reverse=True,
        )

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
