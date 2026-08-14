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
_DIGEST_PATTERN = re.compile(r"[0-9a-f]{64}")
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

    def __post_init__(self) -> None:
        if (
            not isinstance(self.name, str)
            or not self.name
            or self.name in {".", ".."}
            or "/" in self.name
            or "\\" in self.name
        ):
            raise ReleaseIdentityError("artifact name must be one plain file name")
        if not isinstance(self.sha256, str) or _DIGEST_PATTERN.fullmatch(self.sha256) is None:
            raise ReleaseIdentityError("artifact SHA-256 must contain 64 lowercase hexadecimal digits")
        if not isinstance(self.size, int) or isinstance(self.size, bool) or self.size < 0:
            raise ReleaseIdentityError("artifact size must be a non-negative integer")


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


def _read_manifest(path: Path) -> dict[str, Any]:
    try:
        decoded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as error:
        raise ReleaseIdentityError(f"could not read release manifest: {error}") from error
    if not isinstance(decoded, dict):
        raise ReleaseIdentityError("release manifest must be a JSON object")
    return decoded


def _manifest_artifacts(manifest: dict[str, Any]) -> tuple[ArtifactIdentity, ...]:
    records = manifest.get("artifacts")
    if not isinstance(records, list) or not records:
        raise ReleaseIdentityError("release manifest must contain at least one artifact")

    artifacts: list[ArtifactIdentity] = []
    fields = {"name", "sha256", "size"}
    for record in records:
        if not isinstance(record, dict) or set(record) != fields:
            raise ReleaseIdentityError("each manifest artifact must contain name, sha256, and size")
        artifacts.append(
            ArtifactIdentity(
                name=record["name"],
                sha256=record["sha256"],
                size=record["size"],
            )
        )

    names = [artifact.name for artifact in artifacts]
    if len(names) != len(set(names)):
        raise ReleaseIdentityError("release manifest contains duplicate artifact names")
    return tuple(artifacts)


def _verify_manifest_identity(manifest: dict[str, Any], identity: ReleaseIdentity) -> None:
    expected = {
        "commit": identity.commit,
        "committed_at": identity.timestamp,
        "hashver": identity.hashver,
        "schema": _SCHEMA,
        "tag": identity.tag,
    }
    for field, value in expected.items():
        observed = manifest.get(field)
        if observed != value:
            raise ReleaseIdentityError(
                f"release manifest {field} differs: expected={value!r}, observed={observed!r}"
            )


def verify_release_manifest(
    manifest_path: Path,
    directory: Path,
    identity: ReleaseIdentity,
) -> tuple[ArtifactIdentity, ...]:
    """Verify one downloaded release against its source and manifest."""
    manifest_path = manifest_path.resolve()
    directory = directory.resolve()
    if manifest_path.parent != directory:
        raise ReleaseIdentityError("release manifest must be inside the artifact directory")

    manifest = _read_manifest(manifest_path)
    _verify_manifest_identity(manifest, identity)
    expected_artifacts = _manifest_artifacts(manifest)
    expected_names = {artifact.name for artifact in expected_artifacts} | {manifest_path.name}

    try:
        entries = tuple(directory.iterdir())
    except OSError as error:
        raise ReleaseIdentityError(f"could not inspect release artifacts: {error}") from error
    if any(entry.is_symlink() or not entry.is_file() for entry in entries):
        raise ReleaseIdentityError("release directory must contain regular files only")

    actual_names = {entry.name for entry in entries}
    if actual_names != expected_names:
        missing = sorted(expected_names - actual_names)
        unexpected = sorted(actual_names - expected_names)
        raise ReleaseIdentityError(
            f"release artifact set differs: missing={missing}, unexpected={unexpected}"
        )

    for expected in expected_artifacts:
        actual = artifact_identity(directory / expected.name)
        if actual != expected:
            raise ReleaseIdentityError(
                f"release artifact identity differs: {expected.name}: "
                f"expected=sha256:{expected.sha256},size:{expected.size}; "
                f"observed=sha256:{actual.sha256},size:{actual.size}"
            )
    return expected_artifacts


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
    verify = subcommands.add_parser("verify", help="verify downloaded release artifacts")
    verify.add_argument("--manifest", type=Path, required=True)
    verify.add_argument("--directory", type=Path, required=True)
    return parser


def main(arguments: Sequence[str] | None = None) -> int:
    """Run the release identity command-line interface."""
    options = _parser().parse_args(arguments)
    try:
        identity = identity_from_git(options.repository, options.revision)
        if options.command == "manifest":
            write_manifest(options.output, release_manifest(identity, options.artifacts))
        elif options.command == "verify":
            verified = verify_release_manifest(options.manifest, options.directory, identity)
            print(f"verified {len(verified)} release artifacts for {identity.tag}")
        else:
            print(identity.hashver)
    except ReleaseIdentityError as error:
        raise SystemExit(str(error)) from error
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
