"""Deterministic release identities and artifact manifests."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
from collections.abc import Sequence
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

_COMMIT_PATTERN = re.compile(r"(?:[0-9a-f]{40}|[0-9a-f]{64})")
_SCHEMA = "autolean.release-manifest.v1"


class ReleaseIdentityError(ValueError):
    """A source revision cannot produce an unambiguous release identity."""


@dataclass(frozen=True)
class ReleaseIdentity:
    """The immutable release name for one source commit."""

    commit: str
    committed_at: datetime

    def __post_init__(self) -> None:
        if _COMMIT_PATTERN.fullmatch(self.commit) is None:
            raise ReleaseIdentityError("commit must be a full lowercase Git object ID")
        if self.committed_at.tzinfo is None:
            raise ReleaseIdentityError("commit timestamp must include a timezone")

    @property
    def hashver(self) -> str:
        """Return a day-precision Hashver with a 12-character commit suffix."""
        committed_at = self.committed_at.astimezone(UTC)
        return f"{committed_at:%Y.%m.%d}+{self.commit[:12]}"

    @property
    def tag(self) -> str:
        """Return the Git tag associated with this release."""
        return f"v{self.hashver}"

    @property
    def timestamp(self) -> str:
        """Return the commit timestamp in canonical UTC notation."""
        return self.committed_at.astimezone(UTC).isoformat().replace("+00:00", "Z")


@dataclass(frozen=True)
class ArtifactIdentity:
    """The content identity of one release artifact."""

    name: str
    sha256: str
    size: int


def identity_from_git(repository: Path, revision: str = "HEAD") -> ReleaseIdentity:
    """Resolve one Git revision and its commit timestamp."""
    repository = repository.resolve()
    commit = _git(repository, "rev-parse", "--verify", f"{revision}^{{commit}}")
    commit = commit.strip()
    if _COMMIT_PATTERN.fullmatch(commit) is None:
        raise ReleaseIdentityError("Git returned an invalid commit identity")

    timestamp_text = _git(repository, "show", "-s", "--format=%ct", commit).strip()
    try:
        timestamp = int(timestamp_text)
    except ValueError as error:
        raise ReleaseIdentityError("Git returned an invalid commit timestamp") from error
    if timestamp < 0:
        raise ReleaseIdentityError("Git commit timestamp must be non-negative")
    return ReleaseIdentity(commit=commit, committed_at=datetime.fromtimestamp(timestamp, UTC))


def artifact_identity(path: Path) -> ArtifactIdentity:
    """Hash one regular file for a release manifest."""
    if not path.is_file():
        raise ReleaseIdentityError(f"release artifact is not a file: {path}")
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(1024 * 1024):
            digest.update(chunk)
    return ArtifactIdentity(name=path.name, sha256=digest.hexdigest(), size=path.stat().st_size)


def release_manifest(identity: ReleaseIdentity, artifacts: Sequence[Path]) -> dict[str, Any]:
    """Return a stable manifest for uniquely named release artifacts."""
    artifact_records = sorted((artifact_identity(path) for path in artifacts), key=lambda item: item.name)
    names = [artifact.name for artifact in artifact_records]
    if len(names) != len(set(names)):
        raise ReleaseIdentityError("release artifact names must be unique")
    return {
        "artifacts": [asdict(artifact) for artifact in artifact_records],
        "commit": identity.commit,
        "committed_at": identity.timestamp,
        "hashver": identity.hashver,
        "schema": _SCHEMA,
        "tag": identity.tag,
    }


def write_manifest(path: Path, manifest: dict[str, Any]) -> None:
    """Write a canonical human-readable release manifest."""
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _git(repository: Path, *arguments: str) -> str:
    try:
        result = subprocess.run(
            ["git", "-C", str(repository), *arguments],
            check=False,
            capture_output=True,
            text=True,
            timeout=15,
        )
    except (OSError, subprocess.SubprocessError) as error:
        raise ReleaseIdentityError(f"could not inspect Git source: {error}") from error
    if result.returncode != 0:
        detail = (result.stderr or result.stdout).strip()
        raise ReleaseIdentityError(f"Git source inspection failed: {detail or 'no detail'}")
    return result.stdout


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repository", type=Path, default=Path.cwd())
    parser.add_argument("--revision", default="HEAD")
    subcommands = parser.add_subparsers(dest="command", required=True)
    subcommands.add_parser("id", help="print the Hashver release identity")
    manifest = subcommands.add_parser("manifest", help="write an artifact manifest")
    manifest.add_argument("--output", type=Path, required=True)
    manifest.add_argument("artifacts", nargs="+", type=Path)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the release identity command-line interface."""
    options = _parser().parse_args(arguments)
    try:
        identity = identity_from_git(options.repository, options.revision)
        if options.command == "id":
            print(identity.hashver)
        else:
            write_manifest(options.output, release_manifest(identity, options.artifacts))
    except ReleaseIdentityError as error:
        raise SystemExit(str(error)) from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
